"""SQLite storage for profile, preferences, jobs, applications and saved answers."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import JobPosting, Preferences, Profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE NOT NULL,
    dedup_key TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    url TEXT NOT NULL,
    apply_url TEXT,
    description TEXT,
    job_type TEXT,
    salary_text TEXT,
    experience_text TEXT,
    skills TEXT,
    date_posted TEXT,
    is_remote INTEGER,
    easy_apply INTEGER,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    prefilter_score REAL,
    match_score INTEGER,
    match_verdict TEXT,
    match_reasons TEXT,
    missing_skills TEXT,
    red_flags TEXT,
    status TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS jobs_dedup ON jobs(dedup_key);
CREATE INDEX IF NOT EXISTS jobs_score ON jobs(match_score);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary TEXT,
    log_path TEXT
);
CREATE TABLE IF NOT EXISTS answers (
    question_key TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS companies (
    ats TEXT NOT NULL,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (ats, slug)
);
"""

JOB_STATUSES = ["new", "shortlisted", "applying", "applied", "failed", "skipped", "not_interested"]

# Companies whose public career pages had India openings when this list was made.
DEFAULT_COMPANIES = [
    ("greenhouse", "databricks", "Databricks"), ("greenhouse", "mongodb", "MongoDB"),
    ("greenhouse", "okta", "Okta"), ("greenhouse", "zscaler", "Zscaler"),
    ("greenhouse", "inmobi", "InMobi"), ("greenhouse", "stripe", "Stripe"),
    ("greenhouse", "gitlab", "GitLab"), ("greenhouse", "rubrik", "Rubrik"),
    ("greenhouse", "hackerrank", "HackerRank"), ("greenhouse", "elastic", "Elastic"),
    ("greenhouse", "twilio", "Twilio"), ("greenhouse", "toast", "Toast"),
    ("greenhouse", "groww", "Groww"), ("greenhouse", "druva", "Druva"),
    ("greenhouse", "airbnb", "Airbnb"), ("greenhouse", "coinbase", "Coinbase"),
    ("lever", "paytm", "Paytm"), ("lever", "meesho", "Meesho"), ("lever", "zeta", "Zeta"),
    ("lever", "mindtickle", "Mindtickle"), ("lever", "fampay", "FamPay"), ("lever", "cred", "CRED"),
    ("ashby", "snowflake", "Snowflake"), ("ashby", "notion", "Notion"),
    ("smartrecruiters", "freshworks", "Freshworks"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_question(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()[:200]


def dedup_key(title: str, company: str | None) -> str:
    def norm(s: str | None) -> str:
        s = (s or "").lower()
        s = re.sub(r"\b(pvt|private|ltd|limited|inc|llp|llc|technologies|technology|solutions)\b", " ", s)
        return re.sub(r"[^a-z0-9]+", " ", s).strip()

    return f"{norm(title)}|{norm(company)}"


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        if self.get_kv("companies_seeded") is None:
            for ats, slug, name in DEFAULT_COMPANIES:
                self.add_company(ats, slug, name)
            self.set_kv("companies_seeded", True)

    # ----------------------------------------------------------------- kv ---
    def get_kv(self, key: str) -> Any:
        with self._lock:
            row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_kv(self, key: str, value: Any) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self.conn.commit()

    def get_profile(self) -> Profile | None:
        data = self.get_kv("profile")
        return Profile.model_validate(data) if data else None

    def save_profile(self, profile: Profile) -> None:
        self.set_kv("profile", profile.model_dump())

    def get_preferences(self) -> Preferences:
        data = self.get_kv("preferences")
        return Preferences.model_validate(data) if data else Preferences()

    def save_preferences(self, prefs: Preferences) -> None:
        self.set_kv("preferences", prefs.model_dump())

    # --------------------------------------------------------------- jobs ---
    def upsert_job(self, job: JobPosting) -> tuple[int, bool]:
        """Insert or refresh a job. Returns (job id, is_new)."""
        uid_src = f"{job.source}|{job.external_id or job.url}"
        uid = hashlib.sha1(uid_src.encode()).hexdigest()[:16]
        dkey = dedup_key(job.title, job.company)
        ts = now()
        with self._lock:
            row = self.conn.execute("SELECT id FROM jobs WHERE uid=?", (uid,)).fetchone()
            if row is None:
                # Same title+company already stored from another source: keep one copy,
                # but fill in a description if the existing copy lacks one.
                row = self.conn.execute(
                    "SELECT id, description FROM jobs WHERE dedup_key=? "
                    "AND dedup_key NOT LIKE '|%' AND dedup_key NOT LIKE '%|'",
                    (dkey,),
                ).fetchone()
                if row is not None:
                    if not row["description"] and job.description:
                        self.conn.execute(
                            "UPDATE jobs SET description=? WHERE id=?", (job.description, row["id"])
                        )
                    self.conn.execute("UPDATE jobs SET last_seen=? WHERE id=?", (ts, row["id"]))
                    self.conn.commit()
                    return row["id"], False
                cur = self.conn.execute(
                    """INSERT INTO jobs(uid, dedup_key, source, external_id, title, company, location, url,
                       apply_url, description, job_type, salary_text, experience_text, skills, date_posted,
                       is_remote, easy_apply, first_seen, last_seen)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uid, dkey, job.source, job.external_id, job.title, job.company, job.location,
                        job.url, job.apply_url, job.description, job.job_type, job.salary_text,
                        job.experience_text, json.dumps(job.skills), job.date_posted,
                        None if job.is_remote is None else int(job.is_remote),
                        None if job.easy_apply is None else int(job.easy_apply), ts, ts,
                    ),
                )
                self.conn.commit()
                return int(cur.lastrowid), True
            self.conn.execute(
                "UPDATE jobs SET last_seen=?, description=COALESCE(description, ?) WHERE id=?",
                (ts, job.description, row["id"]),
            )
            self.conn.commit()
            return row["id"], False

    def get_job(self, job_id: int) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _job_row(row) if row else None

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        for key in ("skills", "missing_skills", "red_flags"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self.conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
            self.conn.commit()

    def list_jobs(
        self,
        *,
        ids: Iterable[int] | None = None,
        min_score: int | None = None,
        statuses: list[str] | None = None,
        source: str | None = None,
        text: str | None = None,
        limit: int = 20,
        order: str = "score",
    ) -> list[dict]:
        where, args = [], []
        if ids is not None:
            ids = list(ids)
            if not ids:
                return []
            where.append(f"id IN ({','.join('?' * len(ids))})")
            args += ids
        if min_score is not None:
            where.append("match_score >= ?")
            args.append(min_score)
        if statuses:
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            args += statuses
        if source:
            where.append("source = ?")
            args.append(source)
        if text:
            where.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
            args += [f"%{text}%"] * 3
        order_sql = {
            "score": "COALESCE(match_score, -1) DESC, COALESCE(prefilter_score, 0) DESC, id DESC",
            "recent": "id DESC",
        }.get(order, "id DESC")
        sql = "SELECT * FROM jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order_sql} LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [_job_row(r) for r in rows]

    def count_jobs(self) -> int:
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # ------------------------------------------------------- applications ---
    def start_application(self, job_id: int, log_path: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO applications(job_id, status, started_at, log_path) VALUES(?,?,?,?)",
                (job_id, "in_progress", now(), log_path),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def finish_application(self, app_id: int, status: str, summary: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE applications SET status=?, summary=?, finished_at=? WHERE id=?",
                (status, summary, now(), app_id),
            )
            self.conn.commit()

    def list_applications(self, status: str | None = None, limit: int = 50) -> list[dict]:
        sql = """SELECT a.*, j.title, j.company, j.location, j.url FROM applications a
                 JOIN jobs j ON j.id = a.job_id"""
        args: list[Any] = []
        if status:
            sql += " WHERE a.status = ?"
            args.append(status)
        sql += " ORDER BY a.id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def has_applied(self, job_id: int) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM applications WHERE job_id=? AND status IN ('applied','already_applied')",
                (job_id,),
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------ answers ---
    def save_answer(self, question: str, answer: str) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO answers(question_key, question, answer, updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(question_key) DO UPDATE SET answer=excluded.answer, question=excluded.question,
                   updated_at=excluded.updated_at""",
                (normalize_question(question), question.strip(), answer.strip(), now()),
            )
            self.conn.commit()

    def list_answers(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT question, answer FROM answers ORDER BY question").fetchall()
        return [dict(r) for r in rows]

    def delete_answer(self, question: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM answers WHERE question_key=?", (normalize_question(question),)
            )
            self.conn.commit()
        return cur.rowcount > 0

    # ---------------------------------------------------------- companies ---
    def add_company(self, ats: str, slug: str, name: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO companies(ats, slug, name, added_at) VALUES(?,?,?,?)",
                (ats, slug, name, now()),
            )
            self.conn.commit()

    def remove_company(self, name_or_slug: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM companies WHERE lower(name)=lower(?) OR lower(slug)=lower(?)",
                (name_or_slug, name_or_slug),
            )
            self.conn.commit()
        return cur.rowcount

    def list_companies(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM companies ORDER BY name").fetchall()]


def _job_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("skills", "missing_skills", "red_flags"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
