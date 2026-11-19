"""Fetch full job descriptions for jobs that came back from search without one.

Only done for the top candidates (after keyword pre-filtering) to keep requests low.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import re

from bs4 import BeautifulSoup

from .sources import internshala
from .sources.base import html_to_text, http

log = logging.getLogger(__name__)
MAX_DESC = 8000


def _linkedin(job: dict) -> str | None:
    m = re.search(r"(\d{6,})", job.get("external_id") or job["url"])
    if not m:
        return None
    r = http().get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}", timeout=20)
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "lxml")
    desc = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    criteria = soup.select_one(".description__job-criteria-list")
    parts = [desc.get_text("\n", strip=True) if desc else ""]
    if criteria:
        parts.append(criteria.get_text(" | ", strip=True))
    text = "\n".join(p for p in parts if p)
    return text or None


def _naukri(job: dict) -> str | None:
    m = re.search(r"(\d{9,})", job.get("external_id") or job["url"])
    if not m:
        return None
    try:  # jobspy ships the request-signing helper Naukri's API needs
        from curl_cffi import requests as cffi
        from jobspy.naukri.util import generate_nkparam
    except ImportError:
        return None
    jid = m.group(1)
    r = cffi.get(
        f"https://www.naukri.com/jobapi/v4/job/{jid}",
        headers={"appid": "121", "systemid": "Naukri", "nkparam": generate_nkparam(jid)},
        impersonate="chrome",
        timeout=20,
    )
    if r.status_code != 200:
        return None
    d = r.json().get("jobDetails") or {}
    parts = [html_to_text(d.get("description"))]
    if d.get("keySkills"):
        skills = d["keySkills"]
        names = [s.get("label") for group in skills.values() if isinstance(group, list) for s in group if isinstance(s, dict)]
        if names:
            parts.append("Key skills: " + ", ".join(n for n in names if n))
    return "\n".join(p for p in parts if p) or None


def _internshala(job: dict) -> str | None:
    r = http().get(job["url"], timeout=20)
    return internshala.parse_detail(r.text) if r.status_code == 200 else None


def _greenhouse(job: dict) -> str | None:
    _, slug, jid = (job.get("external_id") or "::").split(":", 2)
    r = http().get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}", timeout=20)
    return html_to_text(r.json().get("content")) if r.status_code == 200 else None


def _smartrecruiters(job: dict) -> str | None:
    _, slug, jid = (job.get("external_id") or "::").split(":", 2)
    r = http().get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{jid}", timeout=20)
    if r.status_code != 200:
        return None
    sections = ((r.json().get("jobAd") or {}).get("sections") or {}).values()
    return "\n\n".join(
        f"{s.get('title', '')}\n{html_to_text(s.get('text'))}" for s in sections if isinstance(s, dict)
    ) or None


FETCHERS = {
    "linkedin": _linkedin,
    "naukri": _naukri,
    "internshala": _internshala,
    "company:greenhouse": _greenhouse,
    "company:smartrecruiters": _smartrecruiters,
}


def fetch_description(job: dict) -> str | None:
    fn = FETCHERS.get(job["source"])
    if not fn:
        return None
    try:
        text = fn(job)
    except Exception as e:
        log.info("description fetch failed for job %s: %s", job["id"], e)
        return None
    return text[:MAX_DESC] if text else None


def enrich_missing(jobs: list[dict], store) -> int:
    """Fill in descriptions for jobs that lack one. Returns how many were filled."""
    todo = [j for j in jobs if not (j.get("description") or "").strip() and j["source"] in FETCHERS]
    filled = 0
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for job, text in zip(todo, pool.map(fetch_description, todo)):
            if text:
                job["description"] = text
                store.update_job(job["id"], description=text)
                filled += 1
    return filled
