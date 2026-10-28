from historical_snapshot.chat import llm_claude


def test_truncated_json_is_omitted_not_posted():
    # What claude-sonnet-5-5 returned on 27 Oct 2026 when max_tokens=512 ran out mid-answer.
    text = '```json\n{\n  "stay_context": "28 days out — open",\n  "'
    assert llm_claude._normalize_interpretation(text) is None
    assert llm_claude._compose_final_reply("REPORT", text) == "REPORT"


def test_bare_truncated_json_is_omitted():
    assert llm_claude._normalize_interpretation('{"stay_context": "open", "analysis": "Rev') is None


def test_complete_json_still_renders():
    text = '```json\n{"stay_context": "28 days out — open", "analysis": "Ahead of pace.", "gap_and_action": "Hold rate."}\n```'
    out = llm_claude._normalize_interpretation(text)
    assert out.startswith("Interpretation — 28 days out — open")
    assert "1. Ahead of pace." in out and "2. Hold rate." in out


def test_plain_text_still_renders():
    out = llm_claude._normalize_interpretation("Interpretation\n- Ahead of pace.\n- Hold rate.")
    assert out == "Interpretation\n\n1. Ahead of pace.\n2. Hold rate."


def test_output_limit_is_not_512():
    assert llm_claude.MAX_OUTPUT_TOKENS >= 8000
