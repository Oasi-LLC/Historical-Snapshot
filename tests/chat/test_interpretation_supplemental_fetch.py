from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import historical_snapshot.chat.llm_claude as llm_claude


@dataclass
class _FakeToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeResponse:
    stop_reason: str
    content: list[Any] = field(default_factory=list)


class _FakeMessages:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.messages = _FakeMessages(responses)


def test_interpretation_requests_supplemental_metrics_when_not_covered(monkeypatch):
    """The interpretation step should be able to ask for more data instead of guessing.

    Simulates: original metrics only had headline portfolio figures (no per-listing booking
    window). Claude's first response asks for `fetch_more_metrics`; we hand back canned
    per-listing data; Claude's second response writes bullets citing it. Verifies the tool
    round-trip is wired correctly end-to-end without hitting the real Anthropic API or CSVs.
    """
    responses = [
        _FakeResponse(
            stop_reason="tool_use",
            content=[
                _FakeToolUseBlock(
                    id="tu_1",
                    name="fetch_more_metrics",
                    input={"listing_names": ["Greenhouse", "Spyglass"]},
                )
            ],
        ),
        _FakeResponse(
            stop_reason="end_turn",
            content=[
                _FakeTextBlock(
                    text=(
                        '```json\n{"bullets": '
                        '["Greenhouse averages 12.9 days booking window vs Spyglass at 4.5 days."]}'
                        "\n```"
                    )
                )
            ],
        ),
    ]
    fake_client = _FakeClient(responses)

    monkeypatch.setattr(llm_claude, "anthropic_configured", lambda: True)
    monkeypatch.setattr(llm_claude, "_anthropic_client", lambda: fake_client)

    fetch_calls: list[dict[str, Any]] = []

    def fake_fetch(*, query, fields, listing_names, data_root):
        fetch_calls.append(
            {"query": query, "fields": fields, "listing_names": listing_names, "data_root": data_root}
        )
        return {
            "ok": True,
            "metrics": {
                "listing_breakdown": {
                    "ly_final": [
                        {"listing": "Greenhouse", "booking_window": {"mean_days": 12.9}},
                        {"listing": "Spyglass", "booking_window": {"mean_days": 4.5}},
                    ]
                }
            },
            "error": None,
        }

    monkeypatch.setattr(llm_claude, "_fetch_supplemental_metrics", fake_fetch)

    result = llm_claude.run_claude_interpretation(
        "average booking window between greenhouse and spyglass",
        query={"property": "wmb", "start_date": "2026-07-31", "end_date": "2026-08-02"},
        metrics={"room_revenue": {"current": 1000}},  # headline-only, no per-listing data
        report="Onera Wimberley — Jul 31-Aug 2",
        reply_mode="listing_compare",
        data_root="data",
    )

    assert len(fetch_calls) == 1
    assert fetch_calls[0]["listing_names"] == ["Greenhouse", "Spyglass"]
    assert result is not None
    assert "12.9" in result
    assert "4.5" in result
    # Exactly two model calls: the initial ask + the follow-up after the tool result.
    assert len(fake_client.messages.calls) == 2
    assert "tools" in fake_client.messages.calls[0]
    assert "tools" not in fake_client.messages.calls[1]


def test_interpretation_skips_tool_round_trip_when_not_needed(monkeypatch):
    """No fetch requested -> exactly one model call, no extra latency/cost."""
    responses = [
        _FakeResponse(
            stop_reason="end_turn",
            content=[_FakeTextBlock(text='```json\n{"bullets": ["Revenue is up 12% vs last year."]}\n```')],
        ),
    ]
    fake_client = _FakeClient(responses)

    monkeypatch.setattr(llm_claude, "anthropic_configured", lambda: True)
    monkeypatch.setattr(llm_claude, "_anthropic_client", lambda: fake_client)

    def fail_if_called(**_kwargs):
        raise AssertionError("fetch_more_metrics should not be called when not requested")

    monkeypatch.setattr(llm_claude, "_fetch_supplemental_metrics", fail_if_called)

    result = llm_claude.run_claude_interpretation(
        "how's revenue looking?",
        query={"property": "wmb", "start_date": "2026-07-31", "end_date": "2026-08-02"},
        metrics={"room_revenue": {"current": 1000, "ly_final": 890}},
        report="Onera Wimberley — Jul 31-Aug 2",
        reply_mode="interpretation_only",
        data_root="data",
    )

    assert result is not None
    assert "12%" in result
    assert len(fake_client.messages.calls) == 1
