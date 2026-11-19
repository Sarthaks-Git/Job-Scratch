"""Company career pages through their public applicant-tracking-system (ATS) APIs.

Supported: Greenhouse, Lever, Ashby, SmartRecruiters. These are the official
public job-board APIs those companies publish, so no scraping is involved.
Only openings located in India are kept.
"""

from __future__ import annotations

import concurrent.futures as cf
import re
from typing import Callable

import requests

from ..india import is_india_job, location_matches
from ..models import JobPosting
from ..textutil import title_relevant
from .base import SearchQuery, SourceResult, html_to_text, http

ATS_LIST = ["greenhouse", "lever", "ashby", "smartrecruiters"]


def _greenhouse(session: requests.Session, slug: str, name: str) -> list[JobPosting]:
    r = session.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        offices = " / ".join(o.get("location") or o.get("name") or "" for o in j.get("offices") or [])
        location = loc if is_india_job(loc) else offices if is_india_job(offices) else loc
        out.append(JobPosting(
            source="company:greenhouse", external_id=f"gh:{slug}:{j['id']}", title=j["title"],
            company=j.get("company_name") or name, location=location, url=j["absolute_url"],
            apply_url=j["absolute_url"], date_posted=(j.get("first_published") or j.get("updated_at") or "")[:10],
        ))
    return out


def _lever(session: requests.Session, slug: str, name: str) -> list[JobPosting]:
    r = session.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location") or ""]
        location = ", ".join(l for l in locs if l)
        if j.get("country") == "IN" and "india" not in location.lower():
            location = f"{location}, India".strip(", ")
        parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
        for lst in j.get("lists") or []:
            parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
        parts.append(j.get("additionalPlain") or "")
        out.append(JobPosting(
            source="company:lever", external_id=f"lever:{slug}:{j['id']}", title=j.get("text") or "",
            company=name, location=location, url=j.get("hostedUrl") or "", apply_url=j.get("applyUrl"),
            description="\n".join(p for p in parts if p).strip() or None,
            job_type=(cats.get("commitment") or "").lower().replace(" ", "") or None,
            is_remote=(j.get("workplaceType") == "remote"),
        ))
    return out


def _ashby(session: requests.Session, slug: str, name: str) -> list[JobPosting]:
    r = session.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        places = [j.get("location") or ""]
        for sec in j.get("secondaryLocations") or []:
            places.append(sec.get("location") or "")
            country = ((sec.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
            if country:
                places.append(country)
        country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
        if country:
            places.append(country)
        out.append(JobPosting(
            source="company:ashby", external_id=f"ashby:{slug}:{j['id']}", title=j.get("title") or "",
            company=name, location=" / ".join(p for p in places if p), url=j.get("jobUrl") or "",
            apply_url=j.get("applyUrl"), description=j.get("descriptionPlain"),
            job_type=(j.get("employmentType") or "").lower() or None, is_remote=j.get("isRemote"),
            date_posted=(j.get("publishedAt") or "")[:10],
        ))
    return out


def _smartrecruiters(session: requests.Session, slug: str, name: str) -> list[JobPosting]:
    out, offset = [], 0
    while offset < 500:
        r = session.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
            params={"country": "in", "limit": 100, "offset": offset}, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        for j in data.get("content", []):
            loc = j.get("location") or {}
            out.append(JobPosting(
                source="company:smartrecruiters", external_id=f"sr:{slug}:{j['id']}", title=j.get("name") or "",
                company=(j.get("company") or {}).get("name") or name,
                location=loc.get("fullLocation") or f"{loc.get('city', '')}, India",
                url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                apply_url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                is_remote=loc.get("remote"), date_posted=(j.get("releasedDate") or "")[:10],
            ))
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return out


FETCHERS: dict[str, Callable[[requests.Session, str, str], list[JobPosting]]] = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "smartrecruiters": _smartrecruiters,
}


def fetch_company(ats: str, slug: str, name: str) -> list[JobPosting]:
    """All India openings at one company."""
    jobs = FETCHERS[ats](http(), slug, name)
    return [j for j in jobs if j.url and is_india_job(j.location)]


def search(q: SearchQuery, companies: list[dict]) -> SourceResult:
    result = SourceResult(source="company_boards")
    errors = []

    def one(c: dict) -> list[JobPosting]:
        return fetch_company(c["ats"], c["slug"], c["name"])

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(one, c): c for c in companies}
        for fut in cf.as_completed(futures):
            c = futures[fut]
            try:
                jobs = fut.result()
            except Exception as e:
                errors.append(f"{c['name']} ({c['ats']}): {type(e).__name__}")
                continue
            for job in jobs:
                if not title_relevant(job.title, q.roles):
                    continue
                if not location_matches(job.location, q.locations, q.remote_ok):
                    continue
                result.jobs.append(job)
    if errors:
        result.error = f"{len(errors)} company boards failed: " + ", ".join(errors[:5])
    return result


def _slug_candidates(name: str) -> list[str]:
    base = name.strip().lower()
    base = re.sub(r"\b(pvt|private|ltd|limited|inc|technologies|technology|labs|india)\b\.?", "", base).strip()
    words = re.findall(r"[a-z0-9]+", base)
    cands = ["".join(words), "-".join(words)]
    if words:
        cands.append(words[0])
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def detect_company(name: str, ats: str | None = None, slug: str | None = None) -> dict | None:
    """Find which ATS a company uses by probing the public APIs. Returns a company dict or None."""
    ats_list = [ats] if ats else ATS_LIST
    slugs = [slug] if slug else _slug_candidates(name)
    session = http()
    for a in ats_list:
        for s in slugs:
            try:
                jobs = FETCHERS[a](session, s, name)
            except Exception:
                continue
            if jobs:
                india = [j for j in jobs if is_india_job(j.location)]
                return {"ats": a, "slug": s, "name": name, "total_openings": len(jobs), "india_openings": len(india)}
    return None
