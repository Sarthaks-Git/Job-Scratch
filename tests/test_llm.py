import json
from types import SimpleNamespace

import anthropic
import pytest

from job_agent.agent import JobAgent
from job_agent.config import Settings
from job_agent.llm import LLM, LLMError
from job_agent.models import Profile, ScoreBatch


class FakeMessages:
    def __init__(self, response=None, stream_events=None, final=None, raise_first=None):
        self.response, self.stream_events, self.final = response, stream_events or [], final
        self.raise_first = raise_first
        self.kwargs = []

    def create(self, **kw):
        self.kwargs.append(kw)
        return self.response

    def stream(self, **kw):
        self.kwargs.append(kw)
        outer = self

        class _S:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def __iter__(self_inner):
                if outer.raise_first:
                    err, outer.raise_first = outer.raise_first, None
                    raise err
                return iter(outer.stream_events)

            def get_final_message(self_inner):
                return outer.final

        return _S()


def fake_client(messages):
    return SimpleNamespace(beta=SimpleNamespace(messages=messages))


def test_output_schemas_transform_cleanly():
    for model in (Profile, ScoreBatch):
        schema = anthropic.transform_schema(model)
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        json.dumps(schema)


def test_structured_request_shape_and_parse():
    payload = {"scores": [{"job_id": 3, "score": 81, "verdict": "good", "reasons": "Strong SQL.",
                           "missing_skills": ["Tableau"], "red_flags": []}]}
    resp = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(payload))])
    msgs = FakeMessages(response=resp)
    llm = LLM("claude-opus-5-5", client=fake_client(msgs))
    out = llm.structured(schema=ScoreBatch, system="s", content="c")
    assert out.scores[0].score == 81
    kw = msgs.kwargs[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in kw and "thinking" not in kw


def test_structured_refusal_raises():
    resp = SimpleNamespace(stop_reason="refusal", content=[])
    llm = LLM("m", client=fake_client(FakeMessages(response=resp)))
    with pytest.raises(LLMError):
        llm.structured(schema=ScoreBatch, system="s", content="c")


def test_stream_turn_streams_text_and_retries_bad_tool_json():
    final = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Hi there")])
    events = [SimpleNamespace(type="text", text="Hi "), SimpleNamespace(type="text", text="there")]
    msgs = FakeMessages(stream_events=events, final=final, raise_first=ValueError("bad partial json"))
    llm = LLM("m", client=fake_client(msgs))
    got = []
    turn = llm.stream_turn(system="s", messages=[], tools=[], effort="low", on_text=got.append)
    assert turn.text == "Hi there" and "".join(got) == "Hi there"
    assert len(msgs.kwargs) == 2  # re-issued once after the parse error
    kw = msgs.kwargs[-1]
    assert kw["cache_control"] == {"type": "ephemeral"} and kw["output_config"] == {"effort": "low"}


def test_all_tool_definitions_are_valid_json_schema(store):
    agent = JobAgent(settings=Settings(), store=store, llm=SimpleNamespace(), ui=SimpleNamespace(),
                     browser_factory=lambda: None)
    names = set()
    for d in agent.tool_defs:
        json.dumps(d)
        names.add(d["name"])
        if "input_schema" in d:
            assert d["input_schema"]["type"] == "object" and d["eager_input_streaming"] is True
    assert {"search_jobs", "apply_to_jobs", "web_search", "web_fetch"} <= names
