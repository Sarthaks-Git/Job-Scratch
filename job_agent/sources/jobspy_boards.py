"""LinkedIn, Indeed, Naukri, Glassdoor and Google Jobs via the python-jobspy library.

All searches are pinned to India: Indeed/Glassdoor use the India site, and every
location sent to LinkedIn/Google ends in ", India".
"""

from __future__ import annotations

import logging
import math
from typing import Any

from ..india import is_remote, search_location
from ..models import JobPosting
from .base import SearchQuery, SourceResult

JOBSPY_SITES = ["linkedin", "indeed", "naukri", "glassdoor", "google"]

log = logging.getLogger(__name__)


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if str(value) in {"nan", "NaT", "None", ""}:
        return None
    return value


def _salary_text(row: dict) -> str | None:
    lo, hi = _clean(row.get("min_amount")), _clean(row.get("max_amount"))
    if lo is None and hi is None:
        return None
    cur = _clean(row.get("currency")) or ""
    interval = _clean(row.get("interval")) or ""
    def fmt(v: Any) -> str:
        return f"{float(v):,.0f}"
    amount = fmt(lo) if hi is None or lo == hi else (fmt(hi) if lo is None else f"{fmt(lo)} - {fmt(hi)}")
    return f"{cur} {amount} {interval}".strip()


def row_to_posting(site: str, row: dict) -> JobPosting | None:
    title = _clean(row.get("title"))
    url = _clean(row.get("job_url"))
    if not title or not url:
        return None
    skills_raw = _clean(row.get("skills"))
    skills = [s.strip() for s in str(skills_raw).split(",") if s.strip()] if skills_raw else []
    location = _clean(row.get("location"))
    remote = _clean(row.get("is_remote"))
    date_posted = _clean(row.get("date_posted"))
    return JobPosting(
        source=site,
        external_id=str(_clean(row.get("id")) or url),
        title=str(title),
        company=_clean(row.get("company")),
        location=str(location) if location else None,
        url=str(url),
        apply_url=_clean(row.get("job_url_direct")) or str(url),
        description=_clean(row.get("description")),
        job_type=_clean(row.get("job_type")),
        salary_text=_salary_text(row),
        experience_text=_clean(row.get("experience_range")),
        skills=skills,
        date_posted=str(date_posted) if date_posted else None,
        is_remote=bool(remote) if remote is not None else None,
    )


def _job_type(job_types: list[str]) -> str | None:
    return job_types[0] if len(job_types) == 1 else None


def search_site(site: str, q: SearchQuery) -> SourceResult:
    from jobspy import scrape_jobs  # imported lazily: it pulls in pandas

    result = SourceResult(source=site)
    errors = []
    for role, place in q.combos():
        remote = bool(place and is_remote(place))
        location = search_location(place) if place else "India"
        kwargs: dict[str, Any] = dict(
            site_name=[site],
            search_term=role,
            location=location,
            results_wanted=q.results_per_query,
            hours_old=q.posted_within_days * 24,
            country_indeed="India",
            is_remote=remote,
            verbose=0,
            description_format="markdown",
        )
        jt = _job_type(q.job_types)
        if jt:
            kwargs["job_type"] = jt
        if site == "google":
            where = "India" if remote or not place else search_location(place)
            kind = "internships" if jt == "internship" else "jobs"
            kwargs["google_search_term"] = f"{role} {kind} in {where} since last week"
        try:
            df = scrape_jobs(**kwargs)
        except Exception as e:  # one failed query should not sink the whole source
            errors.append(f"{role} @ {location}: {type(e).__name__}: {str(e)[:200]}")
            continue
        for row in df.to_dict("records"):
            posting = row_to_posting(site, row)
            if posting:
                result.jobs.append(posting)
    if errors and not result.jobs:
        result.error = "; ".join(errors[:3])
    elif errors:
        log.warning("%s: %d queries failed: %s", site, len(errors), errors[0])
    return result
