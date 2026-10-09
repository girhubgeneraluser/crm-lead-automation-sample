"""Synthetic data generator (deterministic for a given seed).

Produces ~300 inbound lead records across three sources with deliberate defects:
  data/webhook_events.jsonl   website + Typeform webhook deliveries, one per line:
                              {"endpoint", "scenario", "body"} - body is the raw string sent
  data/tradeshow_leads.csv    messy badge-scanner export

Defects injected (counts are reported by ``generate`` and asserted in tests):
  cross-source duplicates (same email, different case/whitespace), fuzzy duplicates
  (personal free-mail address + one-letter name typo + differently-written company),
  exact duplicate CSV rows, invalid / placeholder emails, assorted phone formats, mixed-case
  names and countries, missing company, corrupt CSV rows (wrong column count, NUL byte),
  replayed webhook events, bad signatures, a stale timestamp, malformed JSON, schema errors.

All people, companies, domains and numbers are fictional. UK numbers use Ofcom's drama range
(07700 900xxx) and North American numbers the 555-01xx fictional range.
"""
import csv
import io
import json
import os
import random

SAMPLE_NOTE = "Sample project - synthetic data, not client work"

FIRST = ["Olivia", "Liam", "Amara", "Noah", "Isla", "Mateo", "Freya", "Arjun", "Chloe", "Ethan", "Zara", "Lucas",
         "Maeve", "Felix", "Priya", "Oscar", "Hannah", "Jonas", "Leila", "Samuel", "Nora", "Tobias", "Grace", "Rafael",
         "Elena", "Callum", "Ines", "Declan", "Saoirse", "Henrik", "Yuki", "Marcus", "Aoife", "Daniel", "Lena", "Kofi",
         "Ruth", "Viktor", "Imogen", "Theo", "Carmen", "Rohan", "Fiona", "Stefan", "Ada", "Bruno", "Clara", "Dmitri",
         "Esme", "Gareth", "Hugo", "Ivy", "Julian", "Katarina", "Leon", "Mila", "Niamh", "Patrick", "Rosa", "Sven"]
LAST = ["Whitfield", "Okafor", "Brennan", "Lindqvist", "Hargreaves", "Moreau", "Castellano", "Novak", "Pemberton",
        "Achterberg", "Fitzgerald", "Kowalski", "Ramsay", "Takahashi", "Delacroix", "Huber", "Mbeki", "Sorensen",
        "O'Donnell", "McAllister", "van der Berg", "Ellison", "Gallagher", "Schreiber", "Patel", "Thornbury",
        "Ashworth", "Brandt", "Carvalho", "Donnelly", "Eriksen", "Fairbairn", "Grunewald", "Holloway", "Iversen",
        "Jablonski", "Kaplan", "Lockhart", "Maddox", "Nakamura", "Oyelaran", "Prescott", "Quigley", "Rasmussen",
        "Stirling", "Trevelyan", "Underhill", "Vasquez", "Winterbottom", "Yilmaz"]
PREFIX = ["Brightfold", "Kestrelmoor", "Harbourline", "Tidewell", "Copperleaf", "Quillfield", "Lanternby", "Mossgate",
          "Fernhollow", "Cindervale", "Glasswing", "Ironbark", "Juniperhill", "Larkspire", "Marrowby", "Nettlecombe",
          "Oakhaven", "Pebblewick", "Rookhurst", "Saltmarsh", "Thistlebrook", "Umberfield", "Wrenford", "Yarrowdale",
          "Ashgrove", "Bramblecot", "Clearwell", "Duskmoor", "Embervale", "Foxlowe", "Gorsefield", "Hazelmere",
          "Inkwater", "Kilnworth", "Lowmeadow"]
SUFFIX = ["Analytics", "Logistics", "Systems", "Partners", "Health", "Foods", "Engineering", "Labs", "Media", "Retail",
          "Robotics", "Advisory", "Energy", "Software"]
COUNTRIES = [("GB", 34), ("US", 24), ("DE", 14), ("IE", 6), ("CA", 6), ("AT", 3), ("CH", 3), ("FR", 3), ("NL", 3),
             ("AU", 2), ("SG", 2)]
TLD = {"GB": "co.uk", "US": "com", "DE": "de", "IE": "ie", "CA": "ca", "AT": "at", "CH": "ch", "FR": "fr",
       "NL": "nl", "AU": "com.au", "SG": "com.sg"}
LEGAL = {"GB": ["Ltd", "Limited", "PLC"], "US": ["Inc", "LLC", "Inc."], "DE": ["GmbH", "AG"], "IE": ["Ltd", "Limited"],
         "CA": ["Inc", "Ltd"], "AT": ["GmbH"], "CH": ["AG", "GmbH"], "FR": ["SA"], "NL": ["BV"], "AU": ["Pty Ltd"],
         "SG": ["Pte Ltd"]}
COUNTRY_SPELLINGS = {"GB": ["United Kingdom", "UK", "uk", "England", "GB", "U.K."], "US": ["United States", "USA", "usa", "U.S.", "US"],
                     "DE": ["Germany", "Deutschland", "DE", "germany"], "IE": ["Ireland", "IE", "ireland"],
                     "CA": ["Canada", "CA"], "AT": ["Austria", "AT"], "CH": ["Switzerland", "CH"], "FR": ["France", "FR"],
                     "NL": ["Netherlands", "Holland", "NL"], "AU": ["Australia", "AU"], "SG": ["Singapore", "SG"]}
TITLES = [("CEO", 3), ("Founder", 2), ("Managing Director", 3), ("Director of Operations", 4), ("Head of Sales", 4),
          ("VP Marketing", 3), ("Chief Financial Officer", 2), ("Operations Manager", 6), ("Marketing Manager", 6),
          ("Team Lead", 4), ("Analyst", 6), ("Consultant", 5), ("Office Administrator", 4), ("Engineer", 5),
          ("Procurement Specialist", 3), ("Student", 1)]
FREEMAIL = ["gmail.com", "outlook.com", "yahoo.co.uk", "hotmail.com", "icloud.com", "gmx.de", "proton.me"]
SIZES = [5, 12, 25, 40, 60, 85, 120, 180, 250, 400, 750, 1200, 3000]
BUDGETS = ["<5k", "5k-20k", "20k-50k", "50k+"]
TIMELINES = ["ASAP", "This quarter", "Next quarter", "6-12 months", "Just researching"]
BAD_EMAILS = ["jane.doe@", "mark.smith@@{d}", "n/a", "tom at {d}", "anna.k@{stem}", "lee..ross@{d}", "chris@example.com",
              "info@{d} sales", "   ", "noone@localhost"]


def _weighted(rng, pairs):
    return rng.choices([p for p, _ in pairs], weights=[w for _, w in pairs])[0]


def _slug(s):
    return s.lower().replace(" ", "-")


def _local_phone(rng, country):
    if country == "GB":
        return "07700 900%03d" % rng.randint(0, 999)
    if country in ("US", "CA"):
        area = rng.choice(["212", "415", "312", "617", "646", "303"] if country == "US" else ["416", "604", "514"])
        return "(%s) 555-01%02d" % (area, rng.randint(0, 99))
    if country == "DE":
        return "030 %d" % rng.randint(1000000, 99999999)
    if country == "IE":
        return "01 %d" % rng.randint(1000000, 9999999)
    if country == "AT":
        return "01 %d" % rng.randint(100000, 9999999)
    if country == "CH":
        return "044 %d" % rng.randint(1000000, 9999999)
    if country == "FR":
        return "01 %02d %02d %02d %02d" % tuple(rng.randint(10, 99) for _ in range(4))
    if country == "NL":
        return "020 %d" % rng.randint(1000000, 9999999)
    if country == "AU":
        return "02 %d" % rng.randint(10000000, 99999999)
    return "6%d" % rng.randint(1000000, 9999999)  # SG


_CC = {"GB": "44", "US": "1", "CA": "1", "DE": "49", "IE": "353", "AT": "43", "CH": "41", "FR": "33", "NL": "31",
       "AU": "61", "SG": "65"}


def _messy_phone(rng, person):
    """Render the same underlying number in one of several real-world formats."""
    local = person["phone_local"]
    digits = "".join(ch for ch in local if ch.isdigit())
    cc = _CC[person["country"]]
    nsn = digits[1:] if person["country"] in ("GB", "DE", "IE", "AT", "CH", "FR", "NL", "AU") else digits
    style = rng.randint(0, 5)
    if style == 0:
        return local
    if style == 1:
        return "+%s %s" % (cc, nsn)
    if style == 2:
        return "00%s%s" % (cc, nsn)
    if style == 3:
        return "+%s (0)%s" % (cc, nsn) if person["country"] in ("GB", "DE") else "+%s-%s" % (cc, nsn)
    if style == 4:
        return local.replace(" ", "-").replace("(", "").replace(")", "")
    return digits


def build_people(rng, n=230):
    companies = []
    used = set()
    while len(companies) < 75:
        p, s = rng.choice(PREFIX), rng.choice(SUFFIX)
        if (p, s) in used:
            continue
        used.add((p, s))
        country = _weighted(rng, COUNTRIES)
        companies.append({"name": "%s %s" % (p, s), "legal": rng.choice(LEGAL[country]), "country": country,
                          "domain": "%s-%s.%s" % (_slug(p), _slug(s), TLD[country]), "size": rng.choice(SIZES)})
    people, emails = [], set()
    while len(people) < n:
        first, last = rng.choice(FIRST), rng.choice(LAST)
        comp = rng.choice(companies)
        local = "%s.%s" % (first.lower(), last.lower().replace(" ", "").replace("'", ""))
        email = "%s@%s" % (local, comp["domain"])
        if email in emails:
            continue
        emails.add(email)
        people.append({"first": first, "last": last, "company": comp, "country": comp["country"], "email": email,
                       "title": _weighted(rng, TITLES), "phone_local": _local_phone(rng, comp["country"]),
                       "freemail": "%s.%s%d@%s" % (first.lower(), last.lower().replace(" ", "").replace("'", ""),
                                                   rng.randint(1, 99), rng.choice(FREEMAIL))})
    return people


def _typo(rng, word):
    i = rng.randint(1, len(word) - 2)
    return word[:i] + word[i + 1:] if rng.random() < 0.5 else word[:i] + word[i] + word[i:]


def _ts(rng, day_lo=1, day_hi=28):
    return "2026-09-%02dT%02d:%02d:%02dZ" % (rng.randint(day_lo, day_hi), rng.randint(7, 19), rng.randint(0, 59), rng.randint(0, 59))


def _event_id(rng, prefix):
    return "%s_%012x" % (prefix, rng.getrandbits(48))


def _website_payload(rng, person, email=None):
    lead = {"first_name": person["first"], "last_name": person["last"], "email": email or person["email"],
            "phone": _messy_phone(rng, person) if rng.random() < 0.8 else None,
            "company": "%s %s" % (person["company"]["name"], person["company"]["legal"]) if rng.random() < 0.85 else None,
            "job_title": person["title"], "country": rng.choice(COUNTRY_SPELLINGS[person["country"]]) if rng.random() < 0.85 else None,
            "company_size": person["company"]["size"] if rng.random() < 0.6 else None,
            "message": "Interested in your services. (%s)" % SAMPLE_NOTE}
    return {"event_id": _event_id(rng, "wev"), "submitted_at": _ts(rng),
            "form": rng.choice(["contact", "contact", "demo_request", "demo_request", "newsletter"]), "lead": lead}


def _typeform_payload(rng, person, email=None):
    def ans(ref, kind, value):
        a = {"type": kind, "field": {"id": "fld_" + ref[:6], "ref": ref}}
        if kind == "choice":
            a["choice"] = {"label": value}
        else:
            a[kind] = value
        return a
    answers = [ans("first_name", "text", person["first"]), ans("last_name", "text", person["last"]),
               ans("email", "email", email or person["email"]),
               ans("company", "text", person["company"]["name"]),
               ans("job_title", "text", person["title"]),
               ans("country", "choice", rng.choice(COUNTRY_SPELLINGS[person["country"]])),
               ans("company_size", "number", person["company"]["size"]),
               ans("budget", "choice", rng.choice(BUDGETS)),
               ans("timeline", "choice", rng.choice(TIMELINES))]
    if rng.random() < 0.7:
        answers.append(ans("phone", "phone_number", "+%s%s" % (_CC[person["country"]], "".join(
            c for c in person["phone_local"] if c.isdigit()).lstrip("0") if person["country"] not in ("US", "CA") else
            "".join(c for c in person["phone_local"] if c.isdigit()))))
    return {"event_id": _event_id(rng, "tf"), "event_type": "form_response",
            "form_response": {"form_id": "PRICECALC", "submitted_at": _ts(rng), "answers": answers}}


def _messy_case(rng, s):
    if not s:
        return s
    r = rng.random()
    if r < 0.25:
        return s.upper()
    if r < 0.5:
        return s.lower()
    if r < 0.65:
        return "  %s " % s
    return s


CSV_HEADERS = ["First Name", "last_name", "E-mail", " Company ", "Job Title", "Phone", "Country", "Employees",
               "Scan Date", "Notes"]


def _csv_row(rng, person, email=None, company=True, first=None, last=None):
    comp = person["company"]
    company_val = ""
    if company:
        company_val = rng.choice(["%s %s" % (comp["name"], comp["legal"]), comp["name"], comp["name"].upper(),
                                  "%s %s." % (comp["name"].lower(), comp["legal"].lower())])
    day = rng.randint(15, 17)
    scan = rng.choice(["2026-09-%02d %02d:%02d" % (day, rng.randint(9, 17), rng.randint(0, 59)),
                       "%02d/09/2026 %02d:%02d" % (day, rng.randint(9, 17), rng.randint(0, 59))])
    em = email or person["email"]
    em = rng.choice([em, em.upper(), " %s " % em, em.capitalize()])
    size = comp["size"]
    size_val = rng.choice([str(size), "%d-%d" % (size, size * 2), "%d+" % size, ""])
    return [_messy_case(rng, first or person["first"]), _messy_case(rng, last or person["last"]), em, company_val,
            _messy_case(rng, person["title"]), _messy_phone(rng, person) if rng.random() < 0.85 else "",
            rng.choice(COUNTRY_SPELLINGS[person["country"]] + [""]), size_val, scan,
            rng.choice(["", "", "Wants a call back", "Met at stand B12", "Follow up re pricing"])]


def generate(out_dir, seed=20260917):
    rng = random.Random(seed)
    people = build_people(rng)
    rng.shuffle(people)
    web_only, tf_only, csv_only = people[:80], people[80:135], people[135:205]
    web_and_csv, web_and_tf, fuzzy_people = people[205:225], people[225:230], web_only[:10]
    counts = {"people": len(people)}

    events = []  # (endpoint, scenario, payload_or_raw)
    for p in web_only + web_and_csv + web_and_tf:
        events.append(("website", "ok", _website_payload(rng, p)))
    for p in tf_only + web_and_tf:
        events.append(("typeform", "ok", _typeform_payload(rng, p)))
    # invalid emails arriving via webhooks (valid envelope, unusable email)
    for i in range(4):
        p = tf_only[i]
        bad = rng.choice(BAD_EMAILS).format(d=p["company"]["domain"], stem=p["company"]["domain"].split(".")[0])
        events.append(("website", "ok", _website_payload(rng, csv_only[-1 - i], email=bad)))
    rng.shuffle(events)

    # replays: identical body re-delivered (sender retry)
    ok_events = [e for e in events if e[2]["event_id"]]
    replays = [("website" if "lead" in e[2] else "typeform", "replay", e[2]) for e in rng.sample(ok_events, 4)]
    # adversarial / broken deliveries
    broken = [
        ("website", "bad_signature", _website_payload(rng, csv_only[0])),
        ("typeform", "bad_signature", _typeform_payload(rng, csv_only[1])),
        ("website", "stale_timestamp", _website_payload(rng, csv_only[2])),
        ("website", "malformed_json", None),
        ("website", "schema_error", {"event_id": "wev_schema01", "submitted_at": "yesterday", "form": "contact",
                                     "lead": {"first_name": "Missing", "last_name": "Email"}}),
        ("website", "schema_error", {"event_id": "wev_schema02", "submitted_at": "2026-09-20T10:00:00Z", "form": "webinar",
                                     "lead": {"email": "x@y.com", "company_size": "lots", "fax": "01234"}}),
        ("typeform", "schema_error", {"event_id": "tf_schema03", "event_type": "form_response",
                                      "form_response": {"form_id": "PRICECALC", "submitted_at": "2026-09-20T10:00:00Z", "answers": []}}),
    ]
    lines = []
    for endpoint, scenario, payload in events + replays + broken:
        if payload is None:
            body = '{"event_id": "wev_trunc001", "submitted_at": "2026-09-21T09:00:00Z", "form": "contact", "lead": {"email": "trunc'
        else:
            body = json.dumps(payload, sort_keys=True)
        lines.append(json.dumps({"endpoint": endpoint, "scenario": scenario, "body": body}))
    counts.update({"webhook_deliveries": len(lines), "webhook_ok": len(events), "webhook_replays": len(replays),
                   "webhook_broken": len(broken)})

    # ---------------------------------------------------------------- trade-show CSV
    rows = []
    for p in csv_only:
        rows.append(_csv_row(rng, p, company=rng.random() > 0.25))
    for p in web_and_csv:
        rows.append(_csv_row(rng, p, company=rng.random() > 0.3))
    for p in fuzzy_people:  # personal email + one-letter typo in surname, company written differently
        rows.append(_csv_row(rng, p, email=p["freemail"], company=True, last=_typo(rng, p["last"].split(" ")[-1])
                             if " " not in p["last"] else p["last"]))
    for i in range(6):  # bad emails
        p = csv_only[i]
        bad = BAD_EMAILS[i].format(d=p["company"]["domain"], stem=p["company"]["domain"].split(".")[0])
        rows.append(_csv_row(rng, people[200 - i], email=bad))
    dupes = [list(r) for r in rng.sample(rows, 6)]  # exact duplicate rows (scanned twice)
    rows += dupes
    rng.shuffle(rows)
    # corrupt rows: stray comma (extra column) and a truncated row
    corrupt_extra = _csv_row(rng, people[10]) + ["unexpected"]
    corrupt_short = _csv_row(rng, people[11])[:4]
    rows.insert(37, corrupt_extra)
    rows.insert(71, corrupt_short)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_HEADERS)
    for r in rows:
        w.writerow(r)
    text = buf.getvalue()
    # one row with a NUL byte inside a name (badge scanner glitch)
    nul_target = rows[90][0]
    if nul_target:
        text = text.replace(nul_target, nul_target[:2] + "\x00" + nul_target[2:], 1)
    counts.update({"csv_rows": len(rows), "csv_exact_duplicates": len(dupes), "csv_corrupt_rows": 2,
                   "csv_bad_emails": 6, "fuzzy_duplicates": len(fuzzy_people), "cross_source_email_duplicates":
                   len(web_and_csv) + len(web_and_tf)})
    counts["total_inbound_records"] = counts["webhook_deliveries"] + counts["csv_rows"]

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "webhook_events.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(os.path.join(out_dir, "tradeshow_leads.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    with open(os.path.join(out_dir, "generation_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(counts, seed=seed, note=SAMPLE_NOTE), fh, indent=2, sort_keys=True)
    return counts
