"""Small text helpers for keyword matching (no ML dependencies)."""

from __future__ import annotations

import re

_PHRASES = [
    (r"\bfront[\s-]?end\b", "frontend"),
    (r"\bback[\s-]?end\b", "backend"),
    (r"\bfull[\s-]?stack\b", "fullstack"),
    (r"\bmachine learning\b", "ml"),
    (r"\bartificial intelligence\b", "ai"),
    (r"\bdeep learning\b", "dl"),
    (r"\bnatural language processing\b", "nlp"),
    (r"\bdata science\b", "datascience"),
    (r"\bdata scientist\b", "datascience scientist"),
    (r"\bdev[\s-]?ops\b", "devops"),
    (r"\bnode\.?js\b", "nodejs"),
    (r"\breact\.?js\b", "react"),
    (r"\bnext\.?js\b", "nextjs"),
    (r"\bc\+\+", "cplusplus"),
    (r"\bc#", "csharp"),
    (r"\.net\b", "dotnet"),
    (r"\bsde\b", "software engineer"),
    (r"\bswe\b", "software engineer"),
    (r"\bui[\s/]*ux\b", "uiux"),
    (r"\bq\.?a\b", "qa"),
]

STOPWORDS = {
    "a", "an", "and", "the", "of", "for", "to", "in", "on", "at", "with", "or", "by", "as", "is",
    "be", "we", "you", "our", "your", "are", "will", "this", "that", "job", "jobs", "role", "opening",
    "hiring", "urgent", "immediate", "position", "india", "remote", "hybrid", "onsite",
}

# Words that describe level or a generic job family, not the specialty.
GENERIC = {
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "head", "chief", "associate",
    "assistant", "intern", "internship", "trainee", "fresher", "graduate", "entry", "level", "mid",
    "i", "ii", "iii", "iv", "1", "2", "3", "engineer", "engineering", "developer", "development",
    "specialist", "executive", "consultant", "manager", "officer", "analyst", "member", "team",
    "technical", "professional", "expert", "programmer",
}

SENIOR_TITLE = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|architect|head|director|vp|vice president|manager|chief)\b",
    re.IGNORECASE,
)
JUNIOR_TITLE = re.compile(r"\b(intern|internship|trainee|fresher|graduate|entry[\s-]?level|junior|jr\.?)\b", re.I)


def normalize(text: str) -> str:
    t = (text or "").lower()
    for pattern, repl in _PHRASES:
        t = re.sub(pattern, repl, t)
    return t


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9+#]+", normalize(text)) if w not in STOPWORDS]


def core_tokens(text: str) -> set[str]:
    return {t for t in tokens(text) if t not in GENERIC}


def title_relevant(title: str, roles: list[str]) -> bool:
    """Cheap gate: does a title share a specialty word with any target role?"""
    if not roles:
        return True
    title_core = core_tokens(title)
    title_all = set(tokens(title))
    for role in roles:
        rc = core_tokens(role)
        if rc and rc & title_core:
            return True
        if not rc and set(tokens(role)) & title_all:
            return True
    return False


def title_similarity(title: str, role: str) -> float:
    """0..1 overlap between a job title and a target role."""
    rt, tt = set(tokens(role)), set(tokens(title))
    if not rt or not tt:
        return 0.0
    rc, tc = rt - GENERIC, tt - GENERIC
    core = len(rc & tc) / len(rc) if rc else 0.0
    overall = len(rt & tt) / len(rt)
    return 0.7 * core + 0.3 * overall


def contains_term(haystack: str, term: str) -> bool:
    h, t = normalize(haystack), normalize(term).strip()
    if not t:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", h) is not None


def parse_years(text: str | None) -> tuple[float | None, float | None]:
    """Extract an experience range like '2-5 Yrs' or '3+ years' -> (2, 5) / (3, None)."""
    if not text:
        return None, None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(\d+(?:\.\d+)?)\s*\+?\s*(?:yrs?|years?)", text, re.I)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:yrs?|years?|year\(s\))", text, re.I)
    if m:
        return float(m.group(1)), None
    return None, None
