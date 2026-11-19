"""Rank jobs against the candidate: a cheap keyword pre-filter, then Claude scores the best ones."""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging

from .llm import LLM, LLMError
from .models import Preferences, Profile, ScoreBatch
from .textutil import JUNIOR_TITLE, SENIOR_TITLE, contains_term, parse_years, title_similarity

log = logging.getLogger(__name__)

SCORE_BATCH_SIZE = 8


def job_text(job: dict) -> str:
    skills = job.get("skills") or []
    if isinstance(skills, str):
        skills = [skills]
    return " ".join([job.get("title") or "", " ".join(skills), job.get("description") or ""])


def prefilter(job: dict, profile: Profile | None, prefs: Preferences) -> tuple[float, str | None]:
    """Return (score 0..1, reason-to-drop or None)."""
    title = job.get("title") or ""
    company = job.get("company") or ""
    text = job_text(job)

    for c in prefs.exclude_companies:
        if c.strip() and c.strip().lower() in company.lower():
            return 0.0, f"excluded company {c}"
    for kw in prefs.exclude_keywords:
        if kw.strip() and contains_term(title + " " + text, kw):
            return 0.0, f"excluded keyword {kw}"

    roles = prefs.roles or (profile.suggested_roles if profile else [])
    t_score = max((title_similarity(title, r) for r in roles), default=0.0)

    s_score = 0.0
    if profile and profile.skills:
        skills = profile.skills[:30]
        hits = sum(1 for s in skills if contains_term(text, s))
        s_score = min(1.0, hits / max(4, min(len(skills), 12)))

    score = 0.6 * t_score + 0.4 * s_score

    years = profile.total_experience_years if profile else None
    if years is not None:
        lo, _ = parse_years(job.get("experience_text") or "")
        if lo is None:
            lo, _ = parse_years((job.get("description") or "")[:3000])
        if lo is not None and lo > years + 2:
            score -= 0.3
        if years < 2 and SENIOR_TITLE.search(title):
            score -= 0.35
        if years >= 4 and JUNIOR_TITLE.search(title) and "internship" not in prefs.job_types:
            score -= 0.3

    is_intern = (job.get("job_type") == "internship") or bool(
        JUNIOR_TITLE.search(title) and "intern" in title.lower()
    )
    if prefs.job_types == ["internship"] and not is_intern and job.get("job_type"):
        score -= 0.3
    if "internship" not in prefs.job_types and is_intern:
        score -= 0.3

    for kw in prefs.must_have_keywords:
        if kw.strip() and not contains_term(text, kw):
            score -= 0.15

    for c in prefs.preferred_companies:
        if c.strip() and c.strip().lower() in company.lower():
            score += 0.15

    return max(0.0, min(1.0, score)), None


def candidate_brief(profile: Profile, prefs: Preferences) -> str:
    brief = {
        "name": profile.full_name,
        "headline": profile.headline,
        "location": profile.location,
        "total_experience_years": profile.total_experience_years,
        "skills": profile.skills,
        "experience": [
            {"title": e.title, "company": e.company, "from": e.start, "to": e.end, "highlights": e.highlights[:3]}
            for e in profile.experience[:5]
        ],
        "education": [f"{e.degree} {e.field_of_study or ''}, {e.institution} ({e.end or ''})" for e in profile.education],
        "projects": [f"{p.name}: {', '.join(p.technologies)}" for p in profile.projects[:5]],
        "certifications": profile.certifications[:8],
    }
    return json.dumps({"candidate": brief, "preferences": prefs.model_dump(exclude_none=True)}, ensure_ascii=False)


SCORING_SYSTEM = """You are an expert technical recruiter in India. Score how well each job fits the candidate.

Scoring rubric (0-100):
- 85-100 excellent: role, seniority and core skills all match; meets stated preferences.
- 65-84 good: strong overlap, maybe 1-2 learnable gaps or a minor preference miss.
- 40-64 stretch: related role but notable gaps (seniority, key skills, domain).
- 0-39 poor: different field, far too senior/junior, or conflicts with preferences.

Weigh: required years of experience vs the candidate's, core skill overlap, title/seniority, job type
(internship vs full-time), location vs preferred locations, and pay vs minimums when stated.
Judge only from the information given; if the description is missing, judge from title, company,
skills and experience fields and be a little conservative. Keep reasons to one or two short sentences.
Return one score per job, using the job_id given."""


def _score_batch(llm: LLM, brief: str, jobs: list[dict], effort: str) -> list:
    payload = []
    for j in jobs:
        payload.append({
            "job_id": j["id"],
            "title": j["title"],
            "company": j.get("company"),
            "location": j.get("location"),
            "job_type": j.get("job_type"),
            "salary": j.get("salary_text"),
            "experience_required": j.get("experience_text"),
            "skills": j.get("skills"),
            "description": (j.get("description") or "")[:2500] or None,
        })
    content = (
        f"<candidate_and_preferences>\n{brief}\n</candidate_and_preferences>\n\n"
        f"<jobs>\n{json.dumps(payload, ensure_ascii=False)}\n</jobs>\n\n"
        f"Score all {len(jobs)} jobs."
    )
    result = llm.structured(schema=ScoreBatch, system=SCORING_SYSTEM, content=content, effort=effort)
    return result.scores


def llm_score(llm: LLM, store, profile: Profile, prefs: Preferences, jobs: list[dict], effort: str = "low") -> int:
    """Score jobs with Claude in parallel batches and save results. Returns number scored."""
    if not jobs:
        return 0
    brief = candidate_brief(profile, prefs)
    batches = [jobs[i : i + SCORE_BATCH_SIZE] for i in range(0, len(jobs), SCORE_BATCH_SIZE)]
    valid_ids = {j["id"] for j in jobs}
    scored = 0
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_score_batch, llm, brief, b, effort) for b in batches]
        for fut in cf.as_completed(futures):
            try:
                scores = fut.result()
            except LLMError as e:
                log.warning("scoring batch failed: %s", e)
                continue
            for s in scores:
                if s.job_id not in valid_ids:
                    continue
                store.update_job(
                    s.job_id,
                    match_score=max(0, min(100, s.score)),
                    match_verdict=s.verdict,
                    match_reasons=s.reasons,
                    missing_skills=s.missing_skills,
                    red_flags=s.red_flags,
                )
                scored += 1
    return scored
