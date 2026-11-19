"""India-only location rules.

The agent only searches and keeps jobs located in India (including remote /
work-from-home roles posted on Indian boards or tagged with an Indian location).
"""

from __future__ import annotations

import re

STATES_AND_UTS = [
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh", "goa", "gujarat",
    "haryana", "himachal pradesh", "jharkhand", "karnataka", "kerala", "madhya pradesh",
    "maharashtra", "manipur", "meghalaya", "mizoram", "nagaland", "odisha", "orissa", "punjab",
    "rajasthan", "sikkim", "tamil nadu", "telangana", "tripura", "uttar pradesh", "uttarakhand",
    "west bengal", "andaman", "chandigarh", "dadra", "daman", "delhi", "jammu", "kashmir",
    "ladakh", "lakshadweep", "puducherry", "pondicherry",
]

CITIES = [
    "bengaluru", "bangalore", "mumbai", "bombay", "navi mumbai", "thane", "new delhi", "delhi ncr",
    "ncr", "gurugram", "gurgaon", "noida", "greater noida", "ghaziabad", "faridabad", "hyderabad",
    "secunderabad", "chennai", "madras", "pune", "kolkata", "calcutta", "ahmedabad", "gandhinagar",
    "jaipur", "kochi", "cochin", "ernakulam", "thiruvananthapuram", "trivandrum", "kozhikode",
    "calicut", "coimbatore", "madurai", "tiruchirappalli", "trichy", "salem", "vellore", "mysuru",
    "mysore", "mangaluru", "mangalore", "hubli", "hubballi", "belagavi", "belgaum", "indore",
    "bhopal", "nagpur", "nashik", "aurangabad", "kolhapur", "surat", "vadodara", "baroda",
    "rajkot", "lucknow", "kanpur", "varanasi", "prayagraj", "allahabad", "agra", "meerut",
    "dehradun", "mohali", "panchkula", "ludhiana", "amritsar", "jalandhar", "bhubaneswar",
    "cuttack", "visakhapatnam", "vizag", "vijayawada", "guntur", "tirupati", "warangal",
    "patna", "ranchi", "jamshedpur", "raipur", "guwahati", "shillong", "srinagar", "shimla",
    "jodhpur", "udaipur", "kota", "ajmer", "durgapur", "siliguri", "manipal", "udupi",
    "gwalior", "jabalpur", "panaji", "margao",
]

INDIA_WORDS = ["india", "bharat"]

# Places that would otherwise be ambiguous or that clearly mean "not India".
FOREIGN_MARKERS = [
    "pakistan", "bangladesh", "sri lanka", "nepal", "united states", "usa", "u.s.", "canada",
    "united kingdom", "uk", "england", "london", "ireland", "germany", "france", "netherlands",
    "spain", "italy", "poland", "portugal", "sweden", "switzerland", "europe", "emea", "latam",
    "dubai", "uae", "united arab emirates", "saudi", "qatar", "singapore", "malaysia",
    "indonesia", "philippines", "vietnam", "thailand", "japan", "china", "hong kong", "australia",
    "new zealand", "brazil", "mexico", "argentina", "south africa", "nigeria", "kenya", "israel",
    "indiana", "new york", "california", "texas", "toronto", "berlin", "amsterdam", "sydney",
]

REMOTE_WORDS = ["remote", "work from home", "wfh", "anywhere in india", "pan india"]

# Boards that only list Indian jobs, so an empty or "Work from home" location still means India.
INDIA_NATIVE_SOURCES = {"naukri", "internshala"}


def _pattern(terms: list[str]) -> re.Pattern[str]:
    escaped = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    return re.compile(r"(?<![a-z])(" + "|".join(escaped) + r")(?![a-z])", re.IGNORECASE)


_INDIA_RE = _pattern(INDIA_WORDS + STATES_AND_UTS + CITIES)
_FOREIGN_RE = _pattern(FOREIGN_MARKERS)
_REMOTE_RE = _pattern(REMOTE_WORDS)


def mentions_india(text: str | None) -> bool:
    return bool(text and _INDIA_RE.search(text))


def mentions_foreign(text: str | None) -> bool:
    return bool(text and _FOREIGN_RE.search(text))


def is_remote(text: str | None) -> bool:
    return bool(text and _REMOTE_RE.search(text))


def is_india_job(location: str | None, source: str | None = None) -> bool:
    """True if a job's location is in India.

    Jobs from India-only boards pass unless they name a foreign place. Everything
    else must name an Indian place and must not *only* name foreign places.
    """
    loc = (location or "").strip()
    if source in INDIA_NATIVE_SOURCES:
        return not (mentions_foreign(loc) and not mentions_india(loc))
    if not loc:
        return False
    if mentions_india(loc):
        return True
    return False


def check_preferred_location(place: str) -> tuple[bool, str]:
    """Validate a location the user asked for. Returns (ok, note)."""
    p = place.strip()
    if not p:
        return False, "empty location"
    if is_remote(p) and not mentions_foreign(p):
        return True, "remote (India)"
    if mentions_india(p):
        return True, "India"
    if mentions_foreign(p):
        return False, f"'{p}' is outside India; this agent only searches jobs in India"
    # Unknown small towns: accept but flag so the agent can double-check.
    return True, f"'{p}' not in the built-in list of Indian places; assuming it is in India"


def search_location(place: str) -> str:
    """Turn a preferred location into the string sent to job boards."""
    p = place.strip()
    if is_remote(p):
        return "India"
    if re.search(r"(?<![a-z])india(?![a-z])", p, re.IGNORECASE):
        return p
    return f"{p}, India"


_ALIASES = {
    "bangalore": "bengaluru", "gurgaon": "gurugram", "bombay": "mumbai", "madras": "chennai",
    "calcutta": "kolkata", "trivandrum": "thiruvananthapuram", "cochin": "kochi",
    "mysore": "mysuru", "mangalore": "mangaluru", "vizag": "visakhapatnam", "baroda": "vadodara",
    "orissa": "odisha", "pondicherry": "puducherry", "calicut": "kozhikode", "trichy": "tiruchirappalli",
    "allahabad": "prayagraj", "belgaum": "belagavi", "hubli": "hubballi", "new delhi": "delhi",
}
_NCR = {"delhi", "gurugram", "noida", "greater noida", "ghaziabad", "faridabad", "ncr", "delhi ncr"}


def _canonical_places(text: str) -> set[str]:
    found = {m.group(1).lower() for m in _INDIA_RE.finditer(text or "")}
    places = {_ALIASES.get(p, p) for p in found}
    if places & _NCR:
        places |= {"ncr"}
    return places


def location_matches(job_location: str | None, wanted: list[str], remote_ok: bool) -> bool:
    """Does a job's location satisfy the preferred places? Empty `wanted` = anywhere in India."""
    if not wanted:
        return True
    loc = job_location or ""
    if remote_ok and is_remote(loc):
        return True
    job_places = _canonical_places(loc)
    for place in wanted:
        if is_remote(place):
            if is_remote(loc):
                return True
            continue
        want = _canonical_places(place)
        if not want:  # unknown town: fall back to substring match
            if place.strip().lower() in loc.lower():
                return True
            continue
        if want == {"india"}:
            return True
        # Compare at city level when the user named a city, else at state level.
        states = {_ALIASES.get(s, s) for s in STATES_AND_UTS} - {"delhi"}
        want_cities = want - states - {"india"}
        target = want_cities or (want - {"india"})
        if job_places & target:
            return True
    return False
