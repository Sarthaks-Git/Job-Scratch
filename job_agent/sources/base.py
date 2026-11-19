"""Shared helpers for job sources."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

from ..models import JobPosting, Preferences

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def http() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"})
    return s


def html_to_text(raw: str | None, limit: int | None = None) -> str:
    """Convert (possibly entity-escaped) HTML to readable plain text."""
    if not raw:
        return ""
    if "&lt;" in raw and "<" not in raw:
        raw = html.unescape(raw)
    soup = BeautifulSoup(raw, "lxml")
    for br in soup.find_all(["br"]):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    text = soup.get_text(" ")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text[:limit] if limit else text


@dataclass
class SearchQuery:
    """What to search for, derived from preferences (all locations are in India)."""

    roles: list[str]
    locations: list[str]  # human-entered Indian places, may include "Remote"
    remote_ok: bool
    job_types: list[str]
    posted_within_days: int
    results_per_query: int
    max_queries: int

    @classmethod
    def from_preferences(cls, prefs: Preferences, results_per_query: int, max_queries: int) -> "SearchQuery":
        return cls(
            roles=list(prefs.roles),
            locations=list(prefs.locations),
            remote_ok=prefs.remote_ok,
            job_types=list(prefs.job_types),
            posted_within_days=prefs.posted_within_days,
            results_per_query=results_per_query,
            max_queries=max_queries,
        )

    def combos(self) -> list[tuple[str, str | None]]:
        """(role, location) pairs, capped at max_queries. Location None = all India."""
        locs: list[str | None] = [l for l in self.locations if l.strip()] or [None]
        pairs = [(r, l) for l in locs for r in self.roles]
        # Interleave so the cap keeps variety in both roles and places.
        pairs.sort(key=lambda p: (self.roles.index(p[0]) + locs.index(p[1])))
        return pairs[: self.max_queries]


@dataclass
class SourceResult:
    source: str
    jobs: list[JobPosting] = field(default_factory=list)
    error: str | None = None
