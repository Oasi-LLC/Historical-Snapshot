from __future__ import annotations

from historical_snapshot.chat.llm_claude import call_claude_with_retries, is_claude_overload_error
from historical_snapshot.chat.orchestrator import _claude_failure_reply
from historical_snapshot.chat.parser_rules import parse_message


class _FakeOverloaded(Exception):
    def __str__(self) -> str:
        return "Error code: 529 - overloaded_error"


def test_is_claude_overload_error_detects_529():
    assert is_claude_overload_error(_FakeOverloaded())
    assert is_claude_overload_error(RuntimeError("Rate limit exceeded 429"))
    assert not is_claude_overload_error(RuntimeError("invalid api key"))


def test_call_claude_with_retries_eventually_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeOverloaded()
        return "ok"

    monkeypatch.setattr("historical_snapshot.chat.llm_claude.CLAUDE_OVERLOAD_RETRIES", 3)
    monkeypatch.setattr("historical_snapshot.chat.llm_claude.CLAUDE_OVERLOAD_BASE_DELAY_SEC", 0.01)
    monkeypatch.setattr("historical_snapshot.chat.llm_claude.time.sleep", lambda _s: None)
    assert call_claude_with_retries(flaky, label="test") == "ok"
    assert calls["n"] == 3


def test_claude_failure_reply_keeps_wmb_property():
    message = "Tell me about overall performance for WMB for upcoming weekend"
    parsed = parse_message(message)
    reply = _claude_failure_reply(message, parsed, _FakeOverloaded())
    assert "overloaded" in reply.lower()
    assert "Wimberley" in reply or "wmb" in reply.lower()
    assert "529" not in reply
    assert "which property and dates" not in reply.lower()
