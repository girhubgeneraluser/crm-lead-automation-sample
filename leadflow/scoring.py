"""Rules-based lead scoring driven entirely by the JSON config (``scoring`` section).

Each rule: {"id", "field", "op", "value", "points"}. Supported ops:
eq, ne, in, contains_any (case-insensitive substring), gte, lte, between [lo, hi], present.
Score is clamped to 0..100. Tier: score >= hot -> "hot", >= warm -> "warm", else "cold".
"""

SUPPORTED_OPS = {"eq", "ne", "in", "contains_any", "gte", "lte", "between", "present"}


def _matches(op, actual, expected):
    if op == "present":
        return actual not in (None, "", []) if expected else actual in (None, "", [])
    if actual is None:
        return False
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "in":
        return actual in expected
    if op == "contains_any":
        text = str(actual).lower()
        return any(str(e).lower() in text for e in expected)
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return False
    if op == "gte":
        return actual >= expected
    if op == "lte":
        return actual <= expected
    if op == "between":
        return expected[0] <= actual <= expected[1]
    raise ValueError("unsupported op %r" % op)


def validate_config(scoring_cfg):
    problems = []
    th = scoring_cfg.get("thresholds", {})
    if not (isinstance(th.get("hot"), (int, float)) and isinstance(th.get("warm"), (int, float)) and th["hot"] > th["warm"]):
        problems.append("thresholds must have numeric hot > warm")
    for i, rule in enumerate(scoring_cfg.get("rules", [])):
        for key in ("id", "field", "op", "points"):
            if key not in rule:
                problems.append("rule %d missing %r" % (i, key))
        if rule.get("op") not in SUPPORTED_OPS:
            problems.append("rule %s: unsupported op %r" % (rule.get("id", i), rule.get("op")))
    return problems


def score_lead(lead, scoring_cfg):
    """Return (score, tier, reasons). ``reasons`` lists the rule ids that fired with their points."""
    total = 0
    reasons = []
    for rule in scoring_cfg["rules"]:
        if _matches(rule["op"], lead.get(rule["field"]), rule.get("value")):
            total += rule["points"]
            reasons.append("%s(%+d)" % (rule["id"], rule["points"]))
    total = max(0, min(100, total))
    return total, tier_for(total, scoring_cfg["thresholds"]), reasons


def tier_for(score, thresholds):
    if score >= thresholds["hot"]:
        return "hot"
    if score >= thresholds["warm"]:
        return "warm"
    return "cold"
