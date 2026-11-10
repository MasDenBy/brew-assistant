"""Offline checks for no-answer refusal detection (rag.is_refusal).

The UI must hide the retrieved sources whenever the model declines to answer
(the chunks did not contain the answer), so the detector must:
- accept the canonical phrase in all the shapes the model produces
  (case, trailing punctuation, typographic apostrophe, markdown prefixes,
  trailing elaboration)
- reject answers that merely mention the phrase mid-answer, normal answers,
  and empty output

Run: .venv\\Scripts\\python.exe tests\\test_refusal.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag import REFUSAL_PHRASE, is_refusal  # noqa: E402


def test_canonical_shapes() -> None:
    # exact phrase, bare and with punctuation
    assert is_refusal("I don't have that information.")
    assert is_refusal("I don't have that information")
    assert is_refusal("I don't have that information.\n")

    # case-insensitive
    assert is_refusal("I DON'T HAVE THAT INFORMATION.")

    # typographic apostrophe
    assert is_refusal("I don\u2019t have that information.")

    # leading markdown decoration (list, quote, bold)
    assert is_refusal("- I don't have that information.")
    assert is_refusal("> I don't have that information.")
    assert is_refusal("* **I don't have that information.**")

    # trailing elaboration on the first line is allowed
    assert is_refusal(
        "I don't have that information. The context documents cover beer "
        "styles and ingredients, but not wine."
    )

    # continuation on a second line still counts (first line is the refusal)
    assert is_refusal("I don't have that information.\nTry asking about hops.")


def test_not_refusals() -> None:
    # normal grounded answers
    assert not is_refusal("The attenuation of WLP029 is 72% - 78%.")
    assert not is_refusal(
        "Munich malt can be substituted with Vienna malt "
        "[source: tables/ingredient-substitutions.csv]."
    )

    # phrase merely mentioned mid-answer: not a refusal
    assert not is_refusal(
        "If the documents lack an answer I reply \"I don't have that "
        "information\", but here the answer is present."
    )
    assert not is_refusal(
        "Unlike a refusal such as I don't have that information, this "
        "answer grounds every claim."
    )

    # refusal phrase not at the start of the first line
    assert not is_refusal("Sadly, I don't have that information.")

    # empty / whitespace-only output
    assert not is_refusal("")
    assert not is_refusal("   \n  ")
    assert not is_refusal(None)


def test_phrase_constant() -> None:
    # the detector and the prompt must stay in sync through this constant
    assert REFUSAL_PHRASE == "i don't have that information"
    assert is_refusal(REFUSAL_PHRASE.capitalize())


def main() -> None:
    test_canonical_shapes()
    print("PASS: is_refusal accepts canonical shapes (case, apostrophe, markdown, elaboration)")
    test_not_refusals()
    print("PASS: is_refusal rejects real answers, mid-answer mentions, and empty output")
    test_phrase_constant()
    print("PASS: REFUSAL_PHRASE constant matches the detector")


if __name__ == "__main__":
    main()
