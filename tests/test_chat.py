"""Tests for ChatSession: message dedup, token tracking, Deep Agent delegation,
streaming fallback, and corrupted-persistence tolerance. Uses stub models (no network).
"""

import json

import pytest

from mini_agent import ChatSession
from mini_agent.types import TokenUsage


class StubModel:
    """Blocking-only model stub."""

    model_name = "stub"
    temperature = 0.0
    max_output_tokens = 512

    def __init__(self, responses):
        self._responses = list(responses)
        self.last_messages = None

    def generate(self, messages, stop_sequences=None, **kwargs):
        self.last_messages = messages
        text = self._responses.pop(0) if self._responses else ""
        return type(
            "R",
            (),
            {"content": text, "token_usage": TokenUsage(input_tokens=10, output_tokens=5)},
        )


class StreamingModel(StubModel):
    """Model that also supports generate_stream."""

    def generate_stream(self, messages, stop_sequences=None, **kwargs):
        self.last_messages = messages
        for chunk in ["Hel", "lo!"]:
            yield chunk


def _flatten(messages):
    """Return all user/assistant message texts, in order, as plain strings."""
    texts = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
        texts.append(c or "")
    return texts


# --------------------------------------------------------------------------
# P0: message de-dup
# --------------------------------------------------------------------------

@pytest.mark.parametrize("deep_enabled", [True, False])
def test_user_message_not_duplicated(deep_enabled):
    m = StubModel(["ok"])
    s = ChatSession(m, enable_deep_agent=deep_enabled)
    s.chat("hello there")
    user_texts = [
        t for t, msg in zip(_flatten(m.last_messages), m.last_messages)
        if msg["role"] == "user"
    ]
    assert user_texts.count("hello there") == 1


# --------------------------------------------------------------------------
# P0: token tracking
# --------------------------------------------------------------------------

def test_token_usage_accumulates():
    m = StubModel(["a", "b"])
    s = ChatSession(m, enable_deep_agent=False)
    s.chat("q1")
    s.chat("q2")
    assert s.total_tokens == {"input": 20, "output": 10, "total": 30}


# --------------------------------------------------------------------------
# P0: history recording (normal + deep)
# --------------------------------------------------------------------------

def test_turn_recorded_to_history_and_persisted(tmp_path):
    p = tmp_path / "c.jsonl"
    m = StubModel(["hi"])
    s = ChatSession(m, enable_deep_agent=False, persist_path=p)
    s.chat("hey")
    assert [x.content for x in s.history] == ["hey", "hi"]
    # persist file has user + assistant
    lines = [json.loads(l) for l in p.read_text().strip().splitlines()]
    assert [x["role"] for x in lines] == ["user", "assistant"]


def test_deep_trigger_delegates_and_records(monkeypatch):
    m = StubModel(["```deep\ndo the thing\n```"])
    s = ChatSession(m, enable_deep_agent=True)
    monkeypatch.setattr(s, "_delegate_to_deep_agent", lambda task: f"DEEP_RESULT[{task}]")

    result = s.chat("please do it")
    assert "DEEP_RESULT[do the thing]" in result
    assert "```deep" not in result
    # history: user + combined assistant
    assert len(s.history) == 2
    assert "DEEP_RESULT" in s.history[1].content
    assert "do the thing" in s.history[1].content


def test_wrapped_deep_trigger_stripped(monkeypatch):
    # Model wraps the trigger in an outer code fence.
    content = "before\n```\n```deep\ntask here\n```\n```\nafter"
    m = StubModel([content])
    s = ChatSession(m, enable_deep_agent=True)
    monkeypatch.setattr(s, "_delegate_to_deep_agent", lambda task: f"R[{task}]")

    result = s.chat("go")
    assert "R[task here]" in result
    assert "before" in result and "after" in result
    assert "```" not in result  # no fence remnants


# --------------------------------------------------------------------------
# P0/P1: deep agent error handling + disabled passthrough
# --------------------------------------------------------------------------

def test_deep_agent_failure_surfaced_not_crashed(monkeypatch):
    m = StubModel(["```deep\nboom task\n```"])
    s = ChatSession(m, enable_deep_agent=True)

    def _boom(task):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(s, "_delegate_to_deep_agent", _boom)
    result = s.chat("go")
    assert "Deep Agent failed" in result
    assert "kaboom" in result
    assert len(s.history) == 2  # turn still recorded


def test_deep_disabled_trigger_passthrough():
    m = StubModel(["```deep\nnope\n```"])
    s = ChatSession(m, enable_deep_agent=False)
    result = s.chat("go")
    assert result == "```deep\nnope\n```"  # returned verbatim
    # and no Deep Agent protocol in the system prompt
    sys_text = _flatten(m.last_messages)[0]
    assert "Deep Agent" not in sys_text


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------

def test_stream_returns_iterator_with_model_support():
    m = StreamingModel(["ignored"])  # generate_stream used, generate unused
    s = ChatSession(m, enable_deep_agent=False)
    out = "".join(s.chat("hi", stream=True))
    assert out == "Hello!"
    assert len(s.history) == 2
    assert s.history[0].content == "hi"


def test_stream_fallback_single_chunk_without_support():
    m = StubModel(["one shot"])
    s = ChatSession(m, enable_deep_agent=False)
    chunks = list(s.chat("hi", stream=True))  # no generate_stream -> fallback
    assert chunks == ["one shot"]
    assert len(s.history) == 2


def test_stream_handles_deep_trigger(monkeypatch):
    # StreamingModel yields chunks; make them form a deep block.
    class DeepStream(StreamingModel):
        def generate_stream(self, messages, stop_sequences=None, **kwargs):
            self.last_messages = messages
            for ch in ["```deep\n", "task abc\n", "```"]:
                yield ch

    m = DeepStream([])
    s = ChatSession(m, enable_deep_agent=True)
    monkeypatch.setattr(s, "_delegate_to_deep_agent", lambda task: f"DONE[{task}]")

    out = "".join(s.chat("go", stream=True))
    assert "DONE[task abc]" in out
    assert "task abc" in s.history[1].content
    assert "DONE" in s.history[1].content


# --------------------------------------------------------------------------
# Persistence robustness
# --------------------------------------------------------------------------

def test_load_history_skips_corrupted_lines(tmp_path):
    p = tmp_path / "c.jsonl"
    good1 = json.dumps({"type": "message", "role": "user", "content": "q1"})
    bad = "{not json"
    good2 = json.dumps({"type": "message", "role": "assistant", "content": "a1"})
    p.write_text("\n".join([good1, bad, good2]) + "\n", encoding="utf-8")

    s = ChatSession(StubModel(["x"]), persist_path=p)
    assert [x.content for x in s.history] == ["q1", "a1"]


def test_load_history_empty_for_missing_file(tmp_path):
    s = ChatSession(StubModel(["x"]), persist_path=tmp_path / "nope.jsonl")
    assert s.history == []


# --------------------------------------------------------------------------
# Explicit deep_chat + context summarization
# --------------------------------------------------------------------------

def test_explicit_user_deep_block_skips_model(monkeypatch):
    """A ```deep block in the USER message is delegated directly, no model call."""
    m = StubModel([])
    s = ChatSession(m, enable_deep_agent=True)
    monkeypatch.setattr(s, "_delegate_to_deep_agent", lambda task: f"DONE[{task}]")

    result = s.chat("```deep\nuser explicit task\n```")
    assert result == "DONE[user explicit task]"
    assert m.last_messages is None  # model never called
    assert len(s.history) == 2
    assert s.history[1].content == "DONE[user explicit task]"


def test_deep_chat_explicit(monkeypatch):
    m = StubModel([])
    s = ChatSession(m)
    monkeypatch.setattr(s, "_delegate_to_deep_agent", lambda task: f"DONE[{task}]")
    r = s.deep_chat("fix it")
    assert r == "DONE[fix it]"
    assert len(s.history) == 2
    assert s.history[0].content == "fix it"
    assert s.history[1].content == "DONE[fix it]"


def test_summarize_context_flows_into_delegate(monkeypatch):
    captured = {}

    def spy(task):
        captured["calls"] = captured.get("calls", 0) + 1
        return "R"

    m = StubModel(["first", "```deep\nfinal task\n```"])
    s = ChatSession(m, enable_deep_agent=True)
    monkeypatch.setattr(s, "_delegate_to_deep_agent", spy)

    s.chat("first user msg")  # establishes history
    s.chat("final user msg")
    assert captured["calls"] == 1