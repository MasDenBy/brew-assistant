"""Smoke test for the ingestion contract (offline, fixtures only).

Pins the collector <-> ingest contract from PHASE_2_PLAN.md §3.1:
- fixtures under data/ load with the expected per-type counts
- one chunk per <RECIPE> (recipes are never split, even oversized ones)
- CSV rows: `type` column > filename prefix > tables/ dir precedence
- missing data dirs are skipped with a warning, not a crash
- every chunk carries source/type/title metadata

Run: .venv\\Scripts\\python.exe tests\\smoke_ingest.py
"""
import contextlib
import io
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langchain_core.documents import Document  # noqa: E402

from config import load_config  # noqa: E402
from ingest import chunk_documents, load_documents  # noqa: E402

FIXTURE_DIR = ROOT / "data"


def _counts(chunks: list[Document]) -> dict[str, int]:
    return dict(Counter(c.metadata["type"] for c in chunks))


def main() -> None:
    # --- default config: both dirs present and absolute ---
    cfg = load_config()
    assert len(cfg["data_dirs"]) == 2, cfg["data_dirs"]
    assert all(Path(d).is_absolute() for d in cfg["data_dirs"]), cfg["data_dirs"]
    assert cfg["top_k"] == 6, cfg["top_k"]

    # --- fixtures only: deterministic regardless of collected_data/ ---
    docs = load_documents([FIXTURE_DIR])
    chunks, n_split = chunk_documents(docs)

    counts = _counts(chunks)
    expected = {
        "style": 10,       # Phase 1 style docs
        "hop": 10,         # 6 ingredient docs + 4 substitution rows (type column)
        "malt": 4,         # 2 ingredient docs + 2 substitution rows (type column)
        "yeast": 2,        # ingredient docs
        "recipe": 3,       # 1 XML file with 1 recipe + 1 XML file with 2 recipes
        "table": 4,        # brewing-water rows (tables/ dir, no type column)
    }
    assert counts == expected, f"per-type counts: {counts} != {expected}"
    assert n_split == 0, f"expected no splits for fixtures, got {n_split}"

    # --- one chunk per recipe; multi-recipe file yields one chunk each ---
    recipe_chunks = [c for c in chunks if c.metadata["type"] == "recipe"]
    per_source = Counter(Path(c.metadata["source"]).as_posix() for c in recipe_chunks)
    assert per_source == {
        "recipes/sample-american-ipa.xml": 1,
        "recipes/sample-stout-pack.xml": 2,
    }, per_source

    # --- recipe rendering: real content, not raw XML ---
    ipa = next(c for c in recipe_chunks if "american-ipa" in c.metadata["source"])
    text = ipa.page_content
    assert text.startswith("# Cascade Single-Hop IPA (recipe)"), text[:80]
    assert "Style: American IPA (21A)" in text
    assert "OG 1.064" in text and "IBU 60" in text
    assert "- Cascade, 40 g, boil 60 min, alpha 5.5%" in text
    assert "- Pale 2-Row, 4500 g, 3 SRM" in text
    assert "<RECIPE>" not in text, "raw XML leaked into chunk"
    assert ipa.metadata["title"] == "Cascade Single-Hop IPA"
    assert ipa.metadata["style"] == "American IPA"

    # --- metadata contract on every chunk ---
    for c in chunks:
        assert c.metadata.get("source"), f"missing source: {c}"
        assert c.metadata.get("type"), f"missing type: {c}"
        assert c.metadata.get("title"), f"missing title: {c}"

    # --- CSV type mechanism: type column wins (hop/malt rows) ---
    subs = [c for c in chunks if "ingredient-substitutions" in c.metadata["source"]]
    assert len(subs) == 6
    sub_types = Counter(c.metadata["type"] for c in subs)
    assert sub_types == {"hop": 4, "malt": 2}, sub_types
    hop_sub = next(c for c in subs if c.metadata["title"] == "Cascade")
    assert "- substitute: Centennial" in hop_sub.page_content
    assert "# ingredient substitutions: Cascade" in hop_sub.page_content

    # --- CSV without type column + non-entity filename -> table ---
    water = [c for c in chunks if "brewing-water" in c.metadata["source"]]
    assert len(water) == 4
    assert all(c.metadata["type"] == "table" for c in water)
    ca = next(c for c in water if c.metadata["title"] == "calcium")
    assert "- target_range: 50-150 ppm" in ca.page_content

    # --- missing dir: warned, not fatal; same docs returned ---
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        docs_again = load_documents([FIXTURE_DIR, ROOT / "no_such_dir_xyz"])
    warning = buf.getvalue()
    assert "skipping" in warning and "no_such_dir_xyz" in warning, warning
    assert len(docs_again) == len(docs)

    # --- chunking rules: oversized recipe never splits, oversized md does ---
    big_recipe = Document(
        page_content="x" * (5000),
        metadata={"source": "recipes/big.xml", "type": "recipe", "title": "big"},
    )
    big_style = Document(
        page_content="y" * 5000,
        metadata={"source": "styles/big.md", "type": "style", "title": "big"},
    )
    out, split_count = chunk_documents([big_recipe, big_style])
    recipe_parts = [d for d in out if d.metadata["type"] == "recipe"]
    style_parts = [d for d in out if d.metadata["type"] == "style"]
    assert len(recipe_parts) == 1, "oversized recipe was split"
    assert len(style_parts) > 1, "oversized style doc should be split"
    assert split_count == 1, split_count

    print("PASS: fixture counts, one-chunk-per-recipe, CSV type precedence,")
    print("      metadata contract, missing-dir warning, split rules")


if __name__ == "__main__":
    main()
