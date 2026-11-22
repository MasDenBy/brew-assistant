"""Offline tests for retrieval query expansion across dialogue turns
(rag.expand_query).

Dialogue is the app's core interaction: the user discusses a style, then
asks "Which yeasts might be good for this style?". Retrieval needs the
entity names from previous user turns; assistant turns stay out on purpose
(their wording biases retrieval toward what was already shown).

Run: .venv\\Scripts\\python.exe tests\\test_expand_query.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag import MAX_QUERY_TURNS, expand_query  # noqa: E402


def test_no_history_returns_query():
    assert expand_query("Tell me about Saison.", None) == "Tell me about Saison."
    assert expand_query("Tell me about Saison.", []) == "Tell me about Saison."


def test_last_user_turn_is_prepended():
    history = [
        ("user", "Tell me about Belgian Saison."),
        ("assistant", "Belgian Saison is a spicy, highly attenuated ale."),
    ]
    assert expand_query(
        "Which yeasts might be good for this style?", history
    ) == (
        "Tell me about Belgian Saison. "
        "Which yeasts might be good for this style?"
    )


def test_deep_anaphora_uses_several_user_turns():
    # "this style" was named two user turns ago; the turn in between is
    # itself anaphoric ("its") — the entity must still reach retrieval
    history = [
        ("user", "Tell me about Belgian Saison."),
        ("assistant", "Belgian Saison is a spicy, highly attenuated ale."),
        ("user", "How long does its fermentation usually take?"),
        ("assistant", "Typically 1-2 weeks at warm temperatures."),
    ]
    assert expand_query(
        "Which yeasts might be good for this style?", history
    ) == (
        "Tell me about Belgian Saison. "
        "How long does its fermentation usually take? "
        "Which yeasts might be good for this style?"
    )


def test_turn_cap_limits_injected_turns():
    history = [(f"user", f"question number {i}?") for i in range(10)]
    assert history[-1][1] == "question number 9?"
    expanded = expand_query("Which yeasts might be good for this style?", history)
    injected = [f"question number {i}?" for i in range(10 - MAX_QUERY_TURNS, 10)]
    assert expanded == " ".join([*injected, "Which yeasts might be good for this style?"])


def test_repeated_question_not_duplicated():
    history = [("user", "Tell me about Belgian Saison.")]
    # same question repeated: nothing to inject, query passes through as-is
    assert (
        expand_query("Tell me about Belgian Saison.", history)
        == "Tell me about Belgian Saison."
    )
    # case/whitespace-insensitive dedupe: the history turn still counts as
    # a repeat, so the query is returned unchanged rather than doubled
    assert (
        expand_query("  tell me about Belgian saison. ", history)
        == "  tell me about Belgian saison. "
    )


def test_short_follow_up_replaced_by_last_user_turn():
    history = [
        ("user", "Which style guideline covers American Light Lager?"),
        ("assistant", "Both BJCP and BA list it — which one?"),
    ]
    assert expand_query("The BJCP one.", history) == (
        "Which style guideline covers American Light Lager?"
    )


def test_empty_user_content_skipped():
    history = [("user", ""), ("user", "Tell me about Belgian Saison.")]
    # short follow-up: replaced by the last non-empty user turn
    assert (
        expand_query("What temperature for it?", history)
        == "Tell me about Belgian Saison."
    )
    # longer follow-up: last non-empty user turn prepended
    assert expand_query(
        "Which yeasts might be good for this style?", history
    ) == (
        "Tell me about Belgian Saison. "
        "Which yeasts might be good for this style?"
    )


def main() -> None:
    test_no_history_returns_query()
    print("PASS: no history -> query unchanged")
    test_last_user_turn_is_prepended()
    print("PASS: last user turn prepended for anaphora one turn back")
    test_deep_anaphora_uses_several_user_turns()
    print("PASS: deep anaphora re-injects several user turns")
    test_turn_cap_limits_injected_turns()
    print("PASS: injected turns capped at MAX_QUERY_TURNS")
    test_repeated_question_not_duplicated()
    print("PASS: repeated question not duplicated (case/whitespace-insensitive)")
    test_short_follow_up_replaced_by_last_user_turn()
    print("PASS: short selector follow-up replaced by last user turn")
    test_empty_user_content_skipped()
    print("PASS: empty user turns skipped")
    print(f"ALL 7 PASSED")


if __name__ == "__main__":
    main()
