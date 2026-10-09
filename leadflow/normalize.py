"""Field-level cleaning: email, phone (E.164), names, company, country, sizing fields.

Every function is pure (no I/O) so it is easy to unit-test. ``normalize_lead`` returns the
cleaned lead, a list of "fixes" (what we changed, for the run report) and a reject reason
(or None). A lead is only rejected when it has no usable email: email is the identity key for
HubSpot imports and for deduplication.
"""
import re

# --------------------------------------------------------------------------- countries

COUNTRY_ALIASES = {
    "GB": ["gb", "uk", "u.k.", "united kingdom", "great britain", "england", "scotland", "wales", "northern ireland"],
    "IE": ["ie", "ireland", "republic of ireland", "eire"],
    "DE": ["de", "germany", "deutschland"],
    "AT": ["at", "austria", "osterreich", "österreich"],
    "CH": ["ch", "switzerland", "schweiz", "suisse"],
    "US": ["us", "usa", "u.s.", "u.s.a.", "united states", "united states of america", "america"],
    "CA": ["ca", "canada"],
    "FR": ["fr", "france"],
    "NL": ["nl", "netherlands", "the netherlands", "holland"],
    "ES": ["es", "spain", "espana", "españa"],
    "AU": ["au", "australia"],
    "SE": ["se", "sweden", "sverige"],
    "SG": ["sg", "singapore"],
    "IN": ["in", "india"],
}
_COUNTRY_LOOKUP = {alias: iso for iso, aliases in COUNTRY_ALIASES.items() for alias in aliases}

COUNTRY_NAMES = {
    "GB": "United Kingdom", "IE": "Ireland", "DE": "Germany", "AT": "Austria", "CH": "Switzerland",
    "US": "United States", "CA": "Canada", "FR": "France", "NL": "Netherlands", "ES": "Spain",
    "AU": "Australia", "SE": "Sweden", "SG": "Singapore", "IN": "India",
}

# calling code, national trunk prefix, (min, max) national significant number length
PHONE_PLANS = {
    "GB": ("44", "0", (9, 10)),
    "IE": ("353", "0", (7, 9)),
    "DE": ("49", "0", (6, 11)),
    "AT": ("43", "0", (4, 13)),
    "CH": ("41", "0", (9, 9)),
    "US": ("1", "1", (10, 10)),
    "CA": ("1", "1", (10, 10)),
    "FR": ("33", "0", (9, 9)),
    "NL": ("31", "0", (9, 9)),
    "ES": ("34", "", (9, 9)),
    "AU": ("61", "0", (9, 9)),
    "SE": ("46", "0", (7, 9)),
    "SG": ("65", "", (8, 8)),
    "IN": ("91", "0", (10, 10)),
}
# Calling code -> country, used to infer country from an international number.
# "1" is shared by US/CA; we pick US and say so in the README limitations.
_CC_TO_COUNTRY = {"44": "GB", "353": "IE", "49": "DE", "43": "AT", "41": "CH", "1": "US", "33": "FR",
                  "31": "NL", "34": "ES", "61": "AU", "46": "SE", "65": "SG", "91": "IN"}

TLD_COUNTRY = {"uk": "GB", "ie": "IE", "de": "DE", "at": "AT", "ch": "CH", "ca": "CA", "fr": "FR",
               "nl": "NL", "es": "ES", "au": "AU", "se": "SE", "sg": "SG", "in": "IN"}

MULTI_PART_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz",
                       "com.sg", "co.in", "co.za", "com.br", "co.jp"}

PLACEHOLDER_VALUES = {"", "-", "--", ".", "n/a", "na", "none", "null", "nil", "unknown", "tbc", "?", "x"}
PLACEHOLDER_EMAIL_DOMAINS = {"example.com", "test.com", "domain.com", "email.com"}

_EMAIL_RE = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$"
)


def clean_text(value):
    """Trim, collapse internal whitespace, map placeholders ("n/a", "-") to None."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = re.sub(r"\s+", " ", value.replace("\x00", "")).strip()
    if value.lower() in PLACEHOLDER_VALUES:
        return None
    return value


def normalize_country(value):
    value = clean_text(value)
    if not value:
        return None
    key = value.lower().strip(" .") if len(value) > 3 else value.lower()
    return _COUNTRY_LOOKUP.get(key) or _COUNTRY_LOOKUP.get(value.lower())


# --------------------------------------------------------------------------- email

def normalize_email(value):
    """Return (email, error). Lowercases, strips whitespace / mailto: / angle brackets."""
    value = clean_text(value)
    if not value:
        return None, "missing_email"
    value = value.strip("<>\"' ").lower()
    if value.startswith("mailto:"):
        value = value[len("mailto:"):]
    value = value.rstrip(".,;")
    if " " in value or value.count("@") != 1:
        return None, "invalid_email"
    if not _EMAIL_RE.match(value):
        return None, "invalid_email"
    if value.split("@")[1] in PLACEHOLDER_EMAIL_DOMAINS:
        return None, "placeholder_email"
    return value, None


def registrable_domain(domain):
    labels = domain.lower().split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_PART_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def email_type(email, freemail_domains):
    domain = email.split("@", 1)[1]
    return "freemail" if domain in freemail_domains or registrable_domain(domain) in freemail_domains else "corporate"


def company_from_domain(domain):
    """'brightfold-analytics.co.uk' -> 'Brightfold Analytics'."""
    stem = registrable_domain(domain).split(".")[0]
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", stem) if part)


# --------------------------------------------------------------------------- phone

def normalize_phone(value, country):
    """Return (phone, valid, note). E.164 when we can be sure, otherwise the trimmed raw value."""
    raw = clean_text(value)
    if not raw:
        return None, False, None
    work = re.split(r"(?i)\s*(?:ext\.?|x|#)\s*\d+$", raw)[0]  # drop extensions
    work = work.replace("(0)", "")                              # "+44 (0)20 ..." style
    digits = re.sub(r"\D", "", work)
    international = work.lstrip().startswith("+") or work.lstrip().startswith("00")
    if work.lstrip().startswith("00"):
        digits = digits[2:]
    if international:
        if 8 <= len(digits) <= 15:
            return "+" + digits, True, None
        return raw, False, "phone_unparseable"
    if not country or country not in PHONE_PLANS:
        return raw, False, "phone_country_unknown"
    cc, trunk, (lo, hi) = PHONE_PLANS[country]
    nsn = digits
    if trunk and nsn.startswith(trunk) and lo <= len(nsn) - len(trunk) <= hi:
        nsn = nsn[len(trunk):]
    elif nsn.startswith(cc) and lo <= len(nsn) - len(cc) <= hi and not (lo <= len(nsn) <= hi):
        nsn = nsn[len(cc):]  # international number typed without the "+"
    if lo <= len(nsn) <= hi:
        return "+" + cc + nsn, True, None
    return raw, False, "phone_unparseable"


def country_from_phone(e164):
    if not e164 or not e164.startswith("+"):
        return None
    digits = e164[1:]
    for length in (3, 2, 1):
        if digits[:length] in _CC_TO_COUNTRY:
            return _CC_TO_COUNTRY[digits[:length]]
    return None


# --------------------------------------------------------------------------- names / titles

_PARTICLES = {"van", "von", "der", "den", "de", "da", "di", "du", "la", "le", "del", "dos", "ter"}
_TITLE_ACRONYMS = {"ceo", "cfo", "coo", "cto", "cio", "cmo", "vp", "it", "hr", "svp", "evp", "pr", "ops", "emea", "uk", "us"}


def _cap_word(word):
    lower = word.lower()
    if lower.startswith("mc") and len(lower) > 2:
        return "Mc" + lower[2:].capitalize()
    if "'" in lower:
        return "'".join(p.capitalize() for p in lower.split("'"))
    return lower.capitalize()


def normalize_name(value):
    """Fix casing of tokens that are all-lower or ALL-UPPER; leave mixed-case tokens alone
    (respect 'DeShawn', 'McKay'). 'ada LOVELACE' -> 'Ada Lovelace'."""
    value = clean_text(value)
    if not value:
        return None
    out = []
    for i, token in enumerate(value.split(" ")):
        if token != token.lower() and token != token.upper():
            out.append(token)
        elif i > 0 and token.lower() in _PARTICLES:
            out.append(token.lower())
        else:
            out.append("-".join(_cap_word(p) for p in token.split("-")))
    return " ".join(out)


def normalize_title(value):
    value = clean_text(value)
    if not value:
        return None
    if value != value.lower() and value != value.upper():
        return value
    words = []
    for w in value.lower().split(" "):
        words.append(w.upper() if w.strip(",&/") in _TITLE_ACRONYMS else (w if w in {"of", "and", "&", "for"} else w.capitalize()))
    return " ".join(words)


_COMPANY_SUFFIX_CASE = {"ltd": "Ltd", "limited": "Limited", "gmbh": "GmbH", "llc": "LLC", "plc": "PLC", "inc": "Inc",
                        "ag": "AG", "bv": "BV", "b.v": "B.V.", "sa": "SA", "llp": "LLP", "pty": "Pty", "pte": "Pte"}
_COMPANY_SUFFIXES = {"ltd", "limited", "inc", "incorporated", "llc", "llp", "gmbh", "plc", "ag", "bv", "sa",
                     "corp", "corporation", "co", "company", "the"}


def normalize_company(value):
    value = clean_text(value)
    if not value:
        return None
    value = re.sub(r"\.{2,}$", ".", value)  # "Inc.." -> "Inc."
    if (value == value.lower() or value == value.upper()) and len(value) > 4:
        value = " ".join(_COMPANY_SUFFIX_CASE.get(w.lower().rstrip("."), w.capitalize()) for w in value.split(" "))
    return value


def company_key(name):
    """Comparison key: lowercase, no punctuation, legal suffixes dropped ('ACME Ltd.' == 'Acme Limited')."""
    if not name:
        return None
    words = re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split()
    words = [w for w in words if w not in _COMPANY_SUFFIXES]
    return " ".join(words) or None


# --------------------------------------------------------------------------- sizing fields

def parse_company_size(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 1 else None
    text = clean_text(value)
    if not text:
        return None
    m = re.match(r"^\D*(\d[\d,]*)", text)
    if not m:
        return None
    n = int(m.group(1).replace(",", ""))
    return n if n >= 1 else None


_BUDGET_MAP = {"<5k": "<5k", "under5k": "<5k", "5k-20k": "5k-20k", "5-20k": "5k-20k", "20k-50k": "20k-50k",
               "20-50k": "20k-50k", "50k+": "50k+", ">50k": "50k+", "50kplus": "50k+", "over50k": "50k+"}


def normalize_budget(value):
    text = clean_text(value)
    if not text:
        return None
    key = re.sub(r"[\s£$€]", "", text.lower())
    return _BUDGET_MAP.get(key, key)


def normalize_timeline(value):
    text = clean_text(value)
    if not text:
        return None
    key = text.lower()
    return {"this qtr": "this quarter", "immediately": "asap", "now": "asap"}.get(key, key)


# --------------------------------------------------------------------------- whole lead

def normalize_lead(raw, freemail_domains):
    """raw: canonical dict from a source adapter. Returns (lead, fixes, reject_reason)."""
    fixes = []
    original_email = raw.get("email")
    email, err = normalize_email(original_email)
    if err:
        return None, fixes, err
    if isinstance(original_email, str) and original_email != email:
        fixes.append("email_cleaned")

    first = raw.get("first_name")
    last = raw.get("last_name")
    first_n, last_n = normalize_name(first), normalize_name(last)
    if first_n and not last_n and " " in first_n:
        first_n, last_n = first_n.split(" ", 1)[0], first_n.split(" ", 1)[1]
        fixes.append("name_split")
    if (first and clean_text(first) != first_n) or (last and clean_text(last) != last_n):
        fixes.append("name_recased")

    domain = email.split("@", 1)[1]
    etype = email_type(email, freemail_domains)

    country = normalize_country(raw.get("country"))
    if raw.get("country") and not country:
        fixes.append("country_unrecognised")

    phone, phone_valid, phone_note = normalize_phone(raw.get("phone"), country)
    if phone_note:
        fixes.append(phone_note)
    elif phone and phone != clean_text(raw.get("phone")):
        fixes.append("phone_e164")

    # ccTLD first: "+1" is shared by US and Canada, a ".ca" domain is not ambiguous
    if not country and etype == "corporate":
        country = TLD_COUNTRY.get(domain.rsplit(".", 1)[-1])
        if country:
            fixes.append("country_from_domain")
    if not country and phone_valid:
        country = country_from_phone(phone)
        if country:
            fixes.append("country_from_phone")
    if phone and not phone_valid and phone_note == "phone_country_unknown" and country:
        phone, phone_valid, phone_note = normalize_phone(raw.get("phone"), country)
        if phone_valid:
            fixes.remove("phone_country_unknown")
            fixes.append("phone_e164")

    company = normalize_company(raw.get("company"))
    company_inferred = False
    company_domain = registrable_domain(domain) if etype == "corporate" else None
    if not company and company_domain:
        company = company_from_domain(company_domain)
        company_inferred = True
        fixes.append("company_from_domain")

    lead = {
        "email": email,
        "email_type": etype,
        "first_name": first_n,
        "last_name": last_n,
        "phone": phone,
        "phone_valid": bool(phone_valid),
        "company": company,
        "company_inferred": company_inferred,
        "company_domain": company_domain,
        "company_key": company_key(company),
        "job_title": normalize_title(raw.get("job_title")),
        "country": country,
        "company_size": parse_company_size(raw.get("company_size")),
        "budget": normalize_budget(raw.get("budget")),
        "timeline": normalize_timeline(raw.get("timeline")),
        "form": clean_text(raw.get("form")),
        "submitted_at": clean_text(raw.get("submitted_at")),
    }
    return lead, fixes, None
