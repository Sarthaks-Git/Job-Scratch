"""Data models: candidate profile, job preferences, jobs, and LLM output schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    # extra="forbid" makes the JSON schema say additionalProperties: false,
    # which structured outputs require on every object.
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- profile ---

class Link(_Strict):
    label: str = Field(description="e.g. LinkedIn, GitHub, Portfolio")
    url: str


class Experience(_Strict):
    title: str
    company: str
    location: str | None
    start: str | None = Field(description="e.g. 2023-06 or Jun 2023")
    end: str | None = Field(description="'Present' if current")
    highlights: list[str]


class Education(_Strict):
    degree: str
    field_of_study: str | None
    institution: str
    start: str | None
    end: str | None
    grade: str | None = Field(description="CGPA / percentage if stated")


class Project(_Strict):
    name: str
    description: str
    technologies: list[str]


class Profile(_Strict):
    full_name: str
    email: str | None
    phone: str | None
    location: str | None = Field(description="Current city")
    headline: str | None = Field(description="One-line professional headline")
    summary: str | None = Field(description="3-4 sentence professional summary")
    total_experience_years: float | None = Field(
        description="Total full-time work experience in years; 0 for students/freshers"
    )
    skills: list[str]
    experience: list[Experience]
    education: list[Education]
    projects: list[Project]
    certifications: list[str]
    languages: list[str]
    links: list[Link]
    suggested_roles: list[str] = Field(description="5-8 job titles this person is a strong fit for")


# ------------------------------------------------------------ preferences ---

JobType = Literal["fulltime", "internship", "parttime", "contract"]


class Preferences(BaseModel):
    roles: list[str] = []
    locations: list[str] = []
    remote_ok: bool = True
    job_types: list[JobType] = ["fulltime"]
    experience_level: str | None = None  # fresher / junior / mid / senior / lead
    min_salary_lpa: float | None = None  # INR lakhs per annum
    min_stipend_per_month: int | None = None  # INR, for internships
    must_have_keywords: list[str] = []
    exclude_keywords: list[str] = []
    exclude_companies: list[str] = []
    preferred_companies: list[str] = []
    posted_within_days: int = 14
    notes: str | None = None

    def is_complete(self) -> bool:
        return bool(self.roles)


# ------------------------------------------------------------------- jobs ---

class JobPosting(BaseModel):
    """A job as returned by a source, before it is stored."""

    source: str
    external_id: str | None = None
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    apply_url: str | None = None
    description: str | None = None
    job_type: str | None = None
    salary_text: str | None = None
    experience_text: str | None = None
    skills: list[str] = []
    date_posted: str | None = None
    is_remote: bool | None = None
    easy_apply: bool | None = None


# ------------------------------------------------------ LLM output schemas ---

class JobScore(_Strict):
    job_id: int
    score: int = Field(description="0-100 fit score")
    verdict: Literal["excellent", "good", "stretch", "poor"]
    reasons: str = Field(description="One or two short sentences on why")
    missing_skills: list[str]
    red_flags: list[str] = Field(
        description="e.g. needs 5+ years, relocation outside preferred cities, unpaid"
    )


class ScoreBatch(_Strict):
    scores: list[JobScore]
