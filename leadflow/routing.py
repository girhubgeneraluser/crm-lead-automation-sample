"""Territory + round-robin routing with account stickiness.

1. Account stickiness (configurable): if we already have an owned lead at the same company
   (same corporate email domain or same normalised company name) and that owner is active,
   the new contact goes to the same rep.
2. Otherwise the lead's ISO country picks a territory (default territory if none matches).
3. Within the territory, active reps are assigned round-robin. The pointer lives in SQLite
   (``routing_state``) and is advanced inside the same transaction as the lead insert, so a
   crash or restart never skips or double-counts a rep.
"""


def _active(routing_cfg, rep):
    return routing_cfg["reps"].get(rep, {}).get("active", False)


def territory_for(country, routing_cfg):
    for terr in routing_cfg["territories"]:
        if country and country in terr["countries"]:
            return terr
    return routing_cfg["default_territory"]


def sticky_owner(conn, lead, routing_cfg):
    if not routing_cfg.get("account_stickiness"):
        return None
    clauses, params = [], []
    if lead.get("company_domain"):
        clauses.append("company_domain = ?")
        params.append(lead["company_domain"])
    if lead.get("company_key"):
        clauses.append("company_key = ?")
        params.append(lead["company_key"])
    if not clauses:
        return None
    sql = "SELECT owner, territory FROM leads WHERE owner IS NOT NULL AND (%s) ORDER BY id" % " OR ".join(clauses)
    for row in conn.execute(sql, params):
        if _active(routing_cfg, row["owner"]):
            return row["owner"], row["territory"]
    return None


def round_robin(conn, pool_name, reps):
    row = conn.execute("SELECT next_index FROM routing_state WHERE pool = ?", (pool_name,)).fetchone()
    idx = row["next_index"] if row else 0
    owner = reps[idx % len(reps)]
    conn.execute(
        "INSERT INTO routing_state (pool, next_index) VALUES (?, ?) "
        "ON CONFLICT(pool) DO UPDATE SET next_index = excluded.next_index",
        (pool_name, idx + 1),
    )
    return owner


def route(conn, lead, routing_cfg):
    """Return (owner_email, territory_name, method). Must be called inside a write transaction."""
    sticky = sticky_owner(conn, lead, routing_cfg)
    if sticky:
        return sticky[0], sticky[1], "account"
    terr = territory_for(lead.get("country"), routing_cfg)
    reps = [r for r in terr["reps"] if _active(routing_cfg, r)]
    method = "round_robin"
    if not reps:
        terr = routing_cfg["default_territory"]
        reps = [r for r in terr["reps"] if _active(routing_cfg, r)]
        method = "round_robin_fallback"
    if not reps:
        raise RuntimeError("no active reps available for routing; check config.routing")
    return round_robin(conn, terr["name"], reps), terr["name"], method
