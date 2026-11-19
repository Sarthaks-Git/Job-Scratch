"""Settings and on-disk locations.

All personal data (resume copy, job database, browser logins, logs) lives in
JOB_AGENT_HOME, which defaults to ~/.job-agent so it never ends up in git.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def data_dir() -> Path:
    path = Path(os.environ.get("JOB_AGENT_HOME", Path.home() / ".job-agent")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


# Sources that run by default. "glassdoor" and "google" also work through jobspy
# but are more often blocked, so they are opt-in (add them with /set sources ...).
DEFAULT_SOURCES = ["linkedin", "indeed", "naukri", "internshala", "company_boards"]
ALL_SOURCES = ["linkedin", "indeed", "naukri", "glassdoor", "google", "internshala", "company_boards"]


@dataclass
class Settings:
    model: str = "claude-opus-5-5"
    # Effort per kind of call. Claude Opus 5.5 always thinks; effort controls how much.
    chat_effort: str = "medium"
    apply_effort: str = "medium"
    scoring_effort: str = "low"
    # Applying: by default the agent stops before the final Submit and asks you.
    auto_submit: bool = False
    max_apply_steps: int = 60
    # Browser. headless=False so you can watch, log in, and solve CAPTCHAs/OTPs.
    headless: bool = False
    slow_mo_ms: int = 100
    browser_channel: str | None = None  # e.g. "chrome" to drive your installed Chrome
    browser_executable: str | None = None
    # Search.
    sources: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))
    results_per_query: int = 15
    max_queries_per_source: int = 4
    llm_score_top_n: int = 25

    @classmethod
    def path(cls) -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        settings = cls()
        p = cls.path()
        if p.exists():
            try:
                stored = json.loads(p.read_text())
            except json.JSONDecodeError:
                stored = {}
            known = {f.name for f in fields(cls)}
            for key, value in stored.items():
                if key in known:
                    setattr(settings, key, value)
        if os.environ.get("JOB_AGENT_MODEL"):
            settings.model = os.environ["JOB_AGENT_MODEL"]
        return settings

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2))

    def set_from_string(self, key: str, raw: str) -> None:
        """Set a field from CLI text, converting to the field's type."""
        current = {f.name: f for f in fields(self)}
        if key not in current:
            raise KeyError(f"Unknown setting '{key}'. Known: {', '.join(sorted(current))}")
        old = getattr(self, key)
        if isinstance(old, bool):
            word = raw.strip().lower()
            if word not in {"1", "true", "yes", "on", "y", "0", "false", "no", "off", "n"}:
                raise ValueError(f"'{key}' takes true/false, got '{raw}'")
            value: object = word in {"1", "true", "yes", "on", "y"}
        elif isinstance(old, int):
            value = int(raw)
        elif isinstance(old, list):
            value = [s.strip() for s in raw.replace(",", " ").split() if s.strip()]
            if key == "sources":
                bad = [s for s in value if s not in ALL_SOURCES]
                if bad:
                    raise ValueError(f"Unknown sources {bad}. Choose from {ALL_SOURCES}")
        elif raw.strip().lower() in {"none", "null", ""}:
            value = None
        else:
            value = raw.strip()
        setattr(self, key, value)
        self.save()
