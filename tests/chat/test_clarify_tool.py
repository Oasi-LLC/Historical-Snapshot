from __future__ import annotations

from historical_snapshot.chat.tools import ToolSession


def test_clarify_adds_header():
    session = ToolSession()
    payload = session.dispatch(
        "clarify",
        {
            "message": "Which weekend did you mean?",
            "questions": ["stay_window"],
            "proposed": {
                "property": "onera",
                "start_date": "2026-08-01",
                "end_date": "2026-08-03",
            },
        },
    )
    assert payload["ok"] is True
    assert session.clarification is not None
    assert session.clarification.startswith("Clarification\n")
    assert "Stay window: Aug 1–3, 2026" in session.clarification
    assert "*" not in session.clarification
    assert session.clarification_structured["proposed"]["start_date"] == "2026-08-01"
