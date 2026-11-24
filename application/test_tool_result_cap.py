"""Unit tests for ToolMessage size caps (LLM context protection)."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import langgraph_agent as lg


def test_truncate_under_limit_unchanged():
    text = "hello " * 100
    assert lg.truncate_tool_result_text(text) == text


def test_truncate_over_per_result_max():
    text = "x" * 50_000
    out = lg.truncate_tool_result_text(text, max_chars=1_000)
    assert len(out) <= 1_000
    assert "truncated tool result" in out
    assert "50000" in out


def test_truncate_respects_budget_remaining():
    text = "y" * 10_000
    out = lg.truncate_tool_result_text(
        text, max_chars=32_000, budget_remaining=500
    )
    assert len(out) <= 500
    assert "truncated tool result" in out


def test_cap_tool_messages_per_result_and_turn_budget():
    msgs = [
        HumanMessage(content="q"),
        ToolMessage(content="a" * 40_000, tool_call_id="t1", name="get_raw_text"),
        ToolMessage(content="b" * 40_000, tool_call_id="t2", name="get_raw_text"),
        ToolMessage(content="c" * 40_000, tool_call_id="t3", name="get_raw_text"),
        ToolMessage(content="d" * 40_000, tool_call_id="t4", name="get_raw_text"),
        AIMessage(content="ok"),
    ]
    capped = lg.cap_tool_messages(
        msgs, per_result_max=32_000, turn_max=80_000, summarize=False
    )
    assert isinstance(capped[0], HumanMessage)
    assert isinstance(capped[-1], AIMessage)

    tool_msgs = [m for m in capped if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 4
    assert all(len(m.content) <= 32_000 for m in tool_msgs)
    # First tools consume the turn budget; later ones are exhausted notices only.
    assert sum(len(m.content) for m in tool_msgs[:2]) <= 80_000
    assert any("truncated tool result" in m.content for m in tool_msgs)
    assert any("budget exhausted" in m.content for m in tool_msgs)


def test_cap_tool_node_result_dict():
    result = {
        "messages": [
            ToolMessage(content="z" * 50_000, tool_call_id="tid"),
        ],
        "artifacts": [],
    }
    out = lg._cap_tool_node_result(result, summarize=False)
    assert len(out["messages"][0].content) <= lg.TOOL_RESULT_MAX_CHARS
    assert "truncated tool result" in out["messages"][0].content
    assert out["artifacts"] == []


def test_list_content_normalized():
    msg = ToolMessage(
        content=[{"type": "text", "text": "p" * 40_000}],
        tool_call_id="tid",
    )
    capped = lg.cap_tool_messages(
        [msg], per_result_max=1_000, turn_max=5_000, summarize=False
    )
    assert isinstance(capped[0].content, str)
    assert len(capped[0].content) <= 1_000


def test_chunk_text_for_summary_respects_size():
    text = ("para one.\n\n" * 100) + ("para two.\n\n" * 100)
    chunks = lg._chunk_text_for_summary(text, chunk_chars=200)
    assert len(chunks) >= 2
    assert all(len(c) <= 200 or c == chunks[-1] for c in chunks[:-1])
    assert "".join(chunks) == text


def test_summarize_path_used_when_available(monkeypatch):
    original = "BODY " * 10_000  # ~50k
    summary_body = "key facts only"

    def fake_summarize(content, *, target_chars, tool_name=None, chunk_chars=None):
        assert content == original
        assert tool_name == "get_raw_text"
        assert target_chars > 0
        return (
            f"{lg._TOOL_RESULT_SUMMARY_MARKER} {len(content)}→{len(summary_body)} chars, "
            f"tool={tool_name}]\n\n{summary_body}"
        )

    monkeypatch.setattr(lg, "summarize_oversized_tool_result", fake_summarize)

    msg = ToolMessage(
        content=original, tool_call_id="t1", name="get_raw_text"
    )
    capped = lg.cap_tool_messages(
        [msg], per_result_max=32_000, turn_max=80_000, summarize=True
    )
    assert len(capped) == 1
    assert lg._TOOL_RESULT_SUMMARY_MARKER in capped[0].content
    assert "key facts only" in capped[0].content
    assert "truncated tool result" not in capped[0].content
    assert len(capped[0].content) < len(original)


def test_summarize_failure_falls_back_to_truncate(monkeypatch):
    def boom(*_args, **_kwargs):
        return None

    monkeypatch.setattr(lg, "summarize_oversized_tool_result", boom)

    msg = ToolMessage(content="x" * 40_000, tool_call_id="t1", name="web_fetch")
    capped = lg.cap_tool_messages(
        [msg], per_result_max=1_000, turn_max=5_000, summarize=True
    )
    assert len(capped[0].content) <= 1_000
    assert "truncated tool result" in capped[0].content


def test_already_summarized_skips_llm():
    already = (
        f"{lg._TOOL_RESULT_SUMMARY_MARKER} 99999→100 chars]\n\n" + ("s" * 50_000)
    )
    # Marker present → do not re-summarize (caller hard-truncates).
    assert lg.summarize_oversized_tool_result(already, target_chars=500) is None

    capped = lg.cap_tool_messages(
        [ToolMessage(content=already, tool_call_id="t1")],
        per_result_max=1_000,
        turn_max=5_000,
        summarize=True,
    )
    assert len(capped[0].content) <= 1_000
    assert capped[0].content.startswith(lg._TOOL_RESULT_SUMMARY_MARKER)
    assert "truncated tool result" in capped[0].content


def test_reduce_tool_result_summarized_method(monkeypatch):
    monkeypatch.setattr(
        lg,
        "summarize_oversized_tool_result",
        lambda content, *, target_chars, tool_name=None, chunk_chars=None: (
            f"{lg._TOOL_RESULT_SUMMARY_MARKER} x]\n\nshort"
        ),
    )
    text, method = lg._reduce_tool_result_text(
        "z" * 40_000,
        max_chars=32_000,
        budget_remaining=32_000,
        tool_name="bash",
        summarize=True,
    )
    assert method == "summarized"
    assert "short" in text
