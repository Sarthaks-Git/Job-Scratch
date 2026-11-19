"""Internshala (internships and fresher jobs, India only)."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..india import location_matches
from ..models import JobPosting
from .base import SearchQuery, SourceResult, http

BASE = "https://internshala.com"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _days_ago(text: str | None) -> int | None:
    if not text:
        return None
    t = text.lower()
    if "today" in t or "just now" in t or "hour" in t or "few" in t:
        return 0
    m = re.search(r"(\d+)\s*(day|week|month)", t)
    if not m:
        return None
    n = int(m.group(1))
    return n * {"day": 1, "week": 7, "month": 30}[m.group(2)]


def _row_item(card, icon_class: str) -> str | None:
    icon = card.select_one(f"i.{icon_class}")
    if not icon:
        return None
    item = icon.find_parent(class_="row-1-item")
    if not item:
        return None
    span = item.select_one("span.mobile") or item.select_one("span")
    return span.get_text(" ", strip=True) if span else None


def parse_listing(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select("div.individual_internship[data-href]"):
        title_el = card.select_one(".job-internship-name")
        if not title_el:
            continue
        href = card.get("data-href") or ""
        kind = card.get("employment_type") or ("job" if href.startswith("/job/") else "internship")
        locs = [a.get_text(" ", strip=True) for a in card.select(".locations a")]
        if not locs:
            loc_el = card.select_one(".locations span")
            locs = [loc_el.get_text(" ", strip=True)] if loc_el else []
        posted_el = card.select_one(".color-labels span")
        out.append(
            {
                "id": card.get("internshipid") or href,
                "kind": kind,
                "title": title_el.get_text(" ", strip=True),
                "company": (card.select_one(".company-name") or title_el).get_text(" ", strip=True),
                "location": ", ".join(locs),
                "url": BASE + href if href.startswith("/") else href,
                "pay": (card.select_one(".stipend").get_text(" ", strip=True) if card.select_one(".stipend")
                        else _row_item(card, "ic-16-money")),
                "experience": _row_item(card, "ic-16-briefcase"),
                "duration": _row_item(card, "ic-16-calendar"),
                "posted": posted_el.get_text(" ", strip=True) if posted_el else None,
                "skills": [s.get_text(" ", strip=True) for s in card.select(".job_skill")],
                "about": (card.select_one(".about_job .text").get_text(" ", strip=True)
                          if card.select_one(".about_job .text") else None),
            }
        )
    return out


def parse_detail(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    el = soup.select_one(".internship_details") or soup.select_one(".detail_view")
    if not el:
        return None
    text = el.get_text("\n", strip=True)
    return re.sub(r"\n{2,}", "\n", text)


def search(q: SearchQuery) -> SourceResult:
    result = SourceResult(source="internshala")
    session = http()
    kinds = []
    if "internship" in q.job_types:
        kinds.append("internships")
    if any(t in q.job_types for t in ("fulltime", "parttime", "contract")) or not kinds:
        kinds.append("jobs")
    seen: set[str] = set()
    errors = []
    for role in q.roles[: q.max_queries]:
        for kind in kinds:
            url = f"{BASE}/{kind}/keywords-{_slug(role)}/"
            try:
                resp = session.get(url, timeout=25)
                resp.raise_for_status()
            except Exception as e:
                errors.append(f"{url}: {e}")
                continue
            for card in parse_listing(resp.text):
                if card["url"] in seen:
                    continue
                seen.add(card["url"])
                days = _days_ago(card["posted"])
                if days is not None and days > q.posted_within_days:
                    continue
                if not location_matches(card["location"], q.locations, q.remote_ok):
                    continue
                desc_bits = [card["about"] or ""]
                if card["duration"]:
                    desc_bits.append(f"Duration: {card['duration']}")
                result.jobs.append(
                    JobPosting(
                        source="internshala",
                        external_id=str(card["id"]),
                        title=card["title"],
                        company=card["company"],
                        location=card["location"] or None,
                        url=card["url"],
                        apply_url=card["url"],
                        description=None,  # fetched later for the top matches
                        job_type="internship" if card["kind"] == "internship" else "fulltime",
                        salary_text=card["pay"],
                        experience_text=card["experience"],
                        skills=card["skills"],
                        date_posted=card["posted"],
                        is_remote="work from home" in (card["location"] or "").lower(),
                    )
                )
                if len([j for j in result.jobs]) >= q.results_per_query * q.max_queries:
                    break
    if errors and not result.jobs:
        result.error = "; ".join(errors[:2])
    return result
