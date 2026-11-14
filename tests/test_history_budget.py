"""Unit tests for the chat-history budget (rag.trim_history).

No ChromaDB, no embeddings, no LLM. Run:
    .venv\\Scripts\\python.exe tests\\test_history_budget.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag import trim_history  # noqa: E402


def _make_history(turns: int, chars_per_turn: int = 100) -> list[tuple[str, str]]:
    return [
        ("user" if i % 2 == 0 else "assistant", f"turn{i:02d}" + "x" * chars_per_turn)
        for i in range(turns)
    ]


def test_history_that_fits_is_preserved():
    history = _make_history(3, chars_per_turn=40)  # 3 x ~10 tokens = 30 tokens
    result = trim_history(
        history,
        context_text="short context",
        question="hello",
        num_ctx=2048,
        reserved_response_tokens=64,
    )
    assert result == history


def test_long_history_trimmed_from_oldest():
    history = _make_history(10, chars_per_turn=800)  # ~200 tokens each
    result = trim_history(
        history,
        context_text="x" * 200,  # ~50 tokens
        question="hello" * 20,  # ~100 tokens
        num_ctx=2048,
        reserved_response_tokens=64,
    )
    # A suffix of the newest turns is kept, oldest ones dropped.
    assert len(result) < len(history)
    assert result[0] in history
    assert result[-1] == history[-1]
    # Ordering within the suffix is unchanged.
    assert [content for _, content in result] == [
        content for _, content in history[-len(result):]
    ]


def test_first_user_message_kept_when_it_fits():
    history = [
        ("user", "first question small"),
        ("assistant", "answer " * 200),  # large assistant turn
    ]
    result = trim_history(
        history,
        context_text="context",
        question="follow-up",
        num_ctx=2048,
        reserved_response_tokens=64,
    )
    assert result[0] == history[0]


def test_first_user_message_dropped_when_too_large():
    history = [
        ("user", "first " * 400),  # ~600 tokens, exceeds budget alone
        ("assistant", "small"),
        ("user", "latest question"),
    ]
    result = trim_history(
        history,
        context_text="ctx",
        question="q",
        num_ctx=1024,
        reserved_response_tokens=32,
    )
    # The newest turn is always kept; the oversized first turn is dropped.
    assert result[-1] == history[-1]
    assert history[0] not in result


def test_max_history_turns_caps_budget():
    history = _make_history(10, chars_per_turn=20)
    result = trim_history(
        history,
        context_text="",
        question="hi",
        num_ctx=4096,
        reserved_response_tokens=64,
        max_history_turns=3,
    )
    assert len(result) == 3
    assert result == history[-3:]


def test_max_history_turns_zero_disabled():
    history = _make_history(4, chars_per_turn=20)
    result = trim_history(
        history,
        context_text="",
        question="hi",
        num_ctx=4096,
        reserved_response_tokens=64,
        max_history_turns=0,
    )
    assert len(result) == 4


def test_empty_history():
    assert trim_history([], "ctx", "q", 512) == []


def test_disambiguation_reply_does_not_blow_budget():
    """A long prior disambiguation reply is handled like any other turn."""
    history = [
        ("user", "Tell me about American Light Lager."),
        ("assistant", "style A [1]\n\nstyle B [2]\n\nWhich style are you interested?" + "x" * 2000),
        ("user", "The BJCP one."),
    ]
    result = trim_history(
        history,
        context_text="BJCP style doc\n\nBA style doc",
        question="The BJCP one.",
        num_ctx=1024,
        reserved_response_tokens=256,
        max_history_turns=6,
    )
    # The latest user turn is always kept; the oversized disambiguation reply
    # may be dropped if the budget is exhausted.
    assert result[-1] == history[-1]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"ALL {len(tests)} PASSED")
