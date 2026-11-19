import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

CHROMIUM = os.environ.get("JOB_AGENT_TEST_CHROMIUM", "/opt/pw-browsers/chromium")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_AGENT_HOME", str(tmp_path / "home"))
    yield tmp_path / "home"


@pytest.fixture
def store(isolated_home):
    from job_agent.db import Store

    isolated_home.mkdir(parents=True, exist_ok=True)
    return Store(isolated_home / "jobs.db")


@pytest.fixture
def profile():
    from job_agent.models import Profile

    return Profile(
        full_name="Asha Rao", email="asha@example.com", phone="+91 90000 00000", location="Bengaluru",
        headline="Python developer", summary=None, total_experience_years=1.0,
        skills=["Python", "Django", "SQL", "REST APIs", "Docker", "Git"], experience=[], education=[],
        projects=[], certifications=[], languages=["English"], links=[], suggested_roles=["Python Developer"],
    )


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, input, id=None):
    tool_block.n = getattr(tool_block, "n", 0) + 1
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id or f"toolu_{tool_block.n}")


class FakeLLM:
    """Plays back a script. Each entry is a list of blocks, or a callable(messages) -> list of blocks."""

    def __init__(self, script, scores=None):
        self.script = list(script)
        self.calls = []
        self.model = "fake"

    def stream_turn(self, *, system, messages, tools, effort, on_text=None, max_tokens=32000):
        from job_agent.llm import TurnResult

        self.calls.append([dict(m) for m in messages])
        step = self.script.pop(0)
        blocks = step(messages) if callable(step) else step
        text = "".join(b.text for b in blocks if b.type == "text")
        if on_text and text:
            on_text(text)
        tool_uses = [b for b in blocks if b.type == "tool_use"]
        msg = SimpleNamespace(content=blocks, stop_reason="tool_use" if tool_uses else "end_turn")
        return TurnResult(message=msg, text=text, tool_uses=tool_uses)

    def structured(self, **kw):
        raise AssertionError("structured() not expected in this test")


class FakeUI:
    def __init__(self, answers=None, approve=True, confirm=True):
        self.answers = list(answers or [])
        self.approve = approve
        self._confirm = confirm
        self.events, self.questions, self.reviews, self.text = [], [], [], ""

    def begin_thinking(self, label="Thinking"): pass
    def stop_thinking(self): pass
    def agent_text(self, chunk): self.text += chunk
    def end_agent_text(self): pass
    def tool_event(self, text): self.events.append(text)
    def info(self, msg): self.events.append(msg)
    def warn(self, msg): self.events.append("WARN " + msg)
    def markdown(self, text): pass
    def ask(self, question):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else "skip"
    def confirm(self, prompt, default=False): return self._confirm
    def review_submission(self, title, summary):
        self.reviews.append(summary)
        return (True, "") if self.approve else (False, "User declined to submit this application.")
    def wait_for_enter(self, msg): pass


def chromium_available():
    return Path(CHROMIUM).exists() or shutil.which("chromium") is not None
