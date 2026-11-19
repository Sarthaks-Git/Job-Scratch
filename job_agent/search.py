"""Run every enabled source in parallel, keep India jobs, store, pre-filter and score."""

from __future__ import annotations

import concurrent.futures as cf
import logging
from dataclasses import dataclass, field
from typing import Callable

from .config import Settings
from .db import Store
from .enrich import enrich_missing
from .india import is_india_job
from .llm import LLM
from .matching import llm_score, prefilter
from .models import Preferences, Profile
from .sources import company_boards, internshala, jobspy_boards
from .sources.base import SearchQuery, SourceResult

log = logging.getLogger(__name__)


@dataclass
class SearchReport:
    per_source: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    found: int = 0
    new: int = 0
    dropped_outside_india: int = 0
    dropped_by_filters: int = 0
    scored: int = 0
    job_ids: list[int] = field(default_factory=list)


def run_sources(q: SearchQuery, sources: list[str], store: Store) -> list[SourceResult]:
    tasks: dict[str, Callable[[], SourceResult]] = {}
    for s in sources:
        if s in jobspy_boards.JOBSPY_SITES:
            tasks[s] = (lambda site=s: jobspy_boards.search_site(site, q))
        elif s == "internshala":
            tasks[s] = lambda: internshala.search(q)
        elif s == "company_boards":
            tasks[s] = lambda: company_boards.search(q, store.list_companies())
    results = []
    with cf.ThreadPoolExecutor(max_workers=len(tasks) or 1) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for fut in cf.as_completed(futures):
            name = futures[fut]
            try:
                results.append(fut.result())
            except Exception as e:
                results.append(SourceResult(source=name, error=f"{type(e).__name__}: {e}"))
    return results


def search_and_rank(
    *,
    store: Store,
    settings: Settings,
    llm: LLM | None,
    profile: Profile | None,
    prefs: Preferences,
    sources: list[str] | None = None,
    progress: Callable[[str], None] = lambda msg: None,
) -> SearchReport:
    q = SearchQuery.from_preferences(prefs, settings.results_per_query, settings.max_queries_per_source)
    if not q.roles:
        raise ValueError("No target roles set. Set preferences (roles) first.")
    sources = sources or settings.sources
    report = SearchReport()

    progress(f"Searching {', '.join(sources)} ...")
    results = run_sources(q, sources, store)

    ids: list[int] = []
    for res in results:
        report.per_source[res.source] = len(res.jobs)
        if res.error:
            report.errors[res.source] = res.error
        for job in res.jobs:
            report.found += 1
            native = res.source if res.source in ("naukri", "internshala") else None
            if not is_india_job(job.location, native):
                report.dropped_outside_india += 1
                continue
            job_id, is_new = store.upsert_job(job)
            report.new += int(is_new)
            if job_id not in ids:
                ids.append(job_id)

    # Keyword pre-filter over everything found in this search.
    jobs = store.list_jobs(ids=ids, limit=len(ids) or 1)
    kept = []
    for job in jobs:
        score, drop = prefilter(job, profile, prefs)
        store.update_job(job["id"], prefilter_score=round(score, 3))
        if drop:
            report.dropped_by_filters += 1
            continue
        job["prefilter_score"] = score
        kept.append(job)
    kept.sort(key=lambda j: j["prefilter_score"], reverse=True)

    # Claude scores the most promising ones that are not scored yet.
    top = kept[: settings.llm_score_top_n]
    to_score = [j for j in top if j.get("match_score") is None]
    if llm and profile and to_score:
        progress(f"Fetching full descriptions for {len(to_score)} top jobs ...")
        enrich_missing(to_score, store)
        progress(f"Scoring {len(to_score)} jobs against your profile ...")
        report.scored = llm_score(llm, store, profile, prefs, to_score, effort=settings.scoring_effort)

    report.job_ids = [j["id"] for j in kept]
    return report
