import pytest

from job_agent.agent import JobAgent
from job_agent.config import Settings
from tests.conftest import FakeLLM, FakeUI, text_block, tool_block


def make_agent(store, script, ui=None):
    llm = FakeLLM(script)
    ui = ui or FakeUI()
    agent = JobAgent(settings=Settings(), store=store, llm=llm, ui=ui, browser_factory=lambda: None)
    return agent, llm, ui


def last_tool_results(messages):
    return [b for b in messages[-1]["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]


def test_preferences_saved_and_foreign_locations_rejected(store):
    agent, llm, _ = make_agent(store, [
        [tool_block("set_preferences", {"roles": ["Python Developer"], "locations": ["Pune", "London, UK", "Remote"],
                                        "job_types": ["fulltime", "internship"], "min_salary_lpa": 6})],
        [text_block("Saved your preferences.")],
    ])
    out = agent.send("Python dev roles in Pune, London or remote, 6 LPA+")
    assert out == "Saved your preferences."
    prefs = store.get_preferences()
    assert prefs.locations == ["Pune", "Remote"] and prefs.min_salary_lpa == 6
    result = last_tool_results(llm.calls[1])[0]
    assert "Rejected" in result["content"] and not result["is_error"]


def test_invalid_tool_input_returns_error_result(store):
    agent, llm, _ = make_agent(store, [
        [tool_block("get_job_details", {"job_id": "not-a-number"})],
        [text_block("Sorry, let me fix that.")],
    ])
    agent.send("details for job abc")
    result = last_tool_results(llm.calls[1])[0]
    assert result["is_error"] and "INVALID_INPUT" in result["content"]


def test_search_refuses_locations_outside_india(store):
    agent, llm, _ = make_agent(store, [
        [tool_block("search_jobs", {"roles": ["Data Analyst"], "locations": ["New York, USA"]})],
        [text_block("I can only search in India.")],
    ])
    agent.send("find data analyst jobs in New York")
    result = last_tool_results(llm.calls[1])[0]
    assert result["is_error"] and "outside India" in result["content"]


def test_turn_rolled_back_on_error(store):
    def boom(messages):
        raise RuntimeError("network down")

    agent, _, _ = make_agent(store, [[tool_block("list_jobs", {})], boom])
    with pytest.raises(RuntimeError):
        agent.send("list my jobs")
    assert agent.messages == []


def test_apply_requires_existing_unapplied_jobs_and_confirmation(store):
    from job_agent.models import JobPosting

    jid, _ = store.upsert_job(JobPosting(source="linkedin", title="Dev", company="A", location="Pune, India",
                                         url="https://example.com/j"))
    ui = FakeUI(confirm=False)
    agent, llm, _ = make_agent(store, [
        [tool_block("apply_to_jobs", {"job_ids": [jid, 9999]})],
        [text_block("Okay, cancelled.")],
    ], ui=ui)
    agent.send(f"apply to {jid} and 9999")
    content = last_tool_results(llm.calls[1])[0]["content"]
    assert '"job_id": 9999' in content and '"skipped"' in content
