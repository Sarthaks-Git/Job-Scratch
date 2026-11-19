"""End-to-end: the apply agent fills a real form in headless Chromium, driven by a scripted model."""

import re
from pathlib import Path

import pytest

from job_agent.applier import Applier
from job_agent.browser import Browser
from job_agent.config import Settings
from job_agent.models import JobPosting
from tests.conftest import CHROMIUM, FakeLLM, FakeUI, chromium_available, tool_block

pytestmark = pytest.mark.skipif(not chromium_available(), reason="Chromium not available")

FORM = Path(__file__).parent / "fixtures" / "apply_form.html"


def results(messages):
    return [b for b in messages[-1]["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]


def refs_from(snapshot: str) -> dict:
    refs = {}
    for line in snapshot.splitlines():
        m = re.match(r"\[(\d+)\] \S+ \"([^\"]*)\"", line)
        if m:
            refs.setdefault(m.group(2), int(m.group(1)))
    return refs


def test_apply_flow_with_submit_gate(store, profile, tmp_path):
    resume = tmp_path / "Asha_Rao_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    store.save_profile(profile)
    store.set_kv("resume_path", str(resume))
    jid, _ = store.upsert_job(JobPosting(source="company:greenhouse", title="Backend Engineer", company="Example Labs",
                                         location="Bengaluru, India", url=FORM.resolve().as_uri()))
    seen = {}

    def fill(messages):
        r = refs_from(results(messages)[0]["content"])
        seen.update(r)
        return [
            tool_block("type_text", {"ref": r["First name"], "text": "Asha"}),
            tool_block("type_text", {"ref": r["Last name"], "text": "Rao"}),
            tool_block("type_text", {"ref": r["Email"], "text": profile.email}),
            tool_block("select_option", {"ref": r["Current city"], "option": "Bengaluru"}),
            tool_block("set_checkbox", {"ref": r["Yes"], "checked": True}),
            tool_block("upload_file", {"ref": r["Resume/CV"], "file": "resume"}),
            tool_block("set_checkbox", {"ref": r["I agree to the privacy policy"], "checked": True}),
            tool_block("ask_user", {"question": "What is your notice period in days?", "remember_as": "Notice period (days)"}),
        ]

    def notice_then_premature_submit(messages):
        answer = results(messages)[-1]["content"]
        assert "30" in answer
        return [
            tool_block("type_text", {"ref": seen["q_123"], "text": "30"}),
            tool_block("click", {"ref": seen["Submit application"]}),  # no approval yet -> must be refused
        ]

    def approve(messages):
        refused = results(messages)[-1]
        assert refused["is_error"] and "request_submit_approval" in refused["content"]
        return [tool_block("request_submit_approval", {"summary": "Name: Asha Rao\nCity: Bengaluru\nNotice: 30"})]

    script = [
        [tool_block("page_snapshot", {})],
        fill,
        notice_then_premature_submit,
        approve,
        [tool_block("click", {"ref": seen.get("Submit application", 0) or 14, "is_final_submit": True})],
        [tool_block("page_snapshot", {})],
        lambda m: ([tool_block("finish", {"status": "applied", "summary": "Submitted"})]
                   if "Application submitted" in results(m)[0]["content"]
                   else [tool_block("finish", {"status": "failed", "summary": "no confirmation"})]),
    ]

    # The final click must use the ref from the snapshot; patch the script entry lazily.
    script[4] = lambda m: [tool_block("click", {"ref": seen["Submit application"], "is_final_submit": True})]

    browser = Browser(tmp_path / "profile", headless=True, executable_path=CHROMIUM)
    ui = FakeUI(answers=["30"])
    settings = Settings()
    applier = Applier(llm=FakeLLM(script), settings=settings, store=store, browser=browser, ui=ui)
    try:
        job = store.get_job(jid)
        res = applier.apply(job)
    finally:
        browser.close()

    assert res.status == "applied", res.summary
    assert len(ui.reviews) == 1
    assert {"question": "Notice period (days)", "answer": "30"} in store.list_answers()
    assert store.get_job(jid)["status"] == "applied"
    assert store.has_applied(jid)


def test_password_fields_are_never_typed(store, profile, tmp_path):
    page = tmp_path / "login.html"
    page.write_text('<html><body><label>Password <input type="password" name="pw"></label></body></html>')
    store.save_profile(profile)
    jid, _ = store.upsert_job(JobPosting(source="linkedin", title="X", company="Y", location="Pune, India",
                                         url=page.as_uri()))
    script = [
        [tool_block("page_snapshot", {})],
        lambda m: [tool_block("type_text", {"ref": refs_from(results(m)[0]["content"])["Password"], "text": "hunter2"})],
        lambda m: [tool_block("finish", {"status": "needs_manual",
                                         "summary": results(m)[0]["content"][:80]})],
    ]
    browser = Browser(tmp_path / "profile", headless=True, executable_path=CHROMIUM)
    try:
        res = Applier(llm=FakeLLM(script), settings=Settings(), store=store, browser=browser, ui=FakeUI()).apply(
            store.get_job(jid))
    finally:
        browser.close()
    assert res.status == "needs_manual" and "Never type passwords" in res.summary
