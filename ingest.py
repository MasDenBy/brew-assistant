"""Ingestion: load brewing docs from config `data_dirs`, chunk, embed, store in ChromaDB.

Reads the canonical formats produced by data collectors (contract:
collectors/base.py), from every directory in `config.yaml: data_dirs`:

    .md / .txt   style & ingredient docs        -> one doc per file
    .xml         BeerXML recipes                -> one doc per <RECIPE> (never split)
    .csv         reference tables               -> one doc per row (never split)

Chunking policy (AGENTS.md section 6.6): the entity is the chunk. Recipes,
BJCP style guidelines and table rows are never split; oversized documents
among them are capped at MAX_ENTITY_CHARS with a truncation marker, because
the embedding model only sees its first ~1000 characters and an unbounded
document would also blow the prompt budget. Everything else (hop/malt/yeast
docs) uses the text splitter, which only splits when oversized. The 1900-char
caps inside the hop/malt/yeast collectors exist to avoid *splitting* them;
this guard bounds the entities that are *never* split.

Missing directories (e.g. collected_data/ before the first collector run)
are skipped with a warning, so fixtures alone keep the app usable.

Run: python ingest.py
Idempotent: re-running drops and rebuilds the collection.
"""
from __future__ import annotations

import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import load_config

COLLECTION_NAME = "brew_docs"
# Docs are entity-sized (one hop / one style per file), so chunks are large
# enough that a normal doc stays a single chunk; only oversized docs split.
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200

# Entity types that are NEVER split: the entity itself IS the chunk
# (AGENTS.md section 6.6: one recipe / one style guideline / one table row
# per chunk).
NOSPLIT_TYPES = {"recipe", "table", "bjcp_style"}

# Length guard for never-split entities. Data-driven: 2600 chars caps 75 of
# the 110 BJCP docs while keeping flavor/comparison sections intact for most.
# Beyond it the embedding model (all-MiniLM-L6-v2, 256 tokens ~ first 1000
# chars) is blind to the tail and top_k chunks overflow Ollama's context.
MAX_ENTITY_CHARS = 2600
TRUNCATION_NOTE = "\n\n(truncated: document continues beyond this chunk)\n"

# Authoritative type directories, recognized at any depth so collector
# output like collected_data/<collector>/recipes/*.xml classifies correctly.
TYPE_DIRS = {"recipes": "recipe", "bjcp": "bjcp_style", "styles": "style"}

# Filename-prefix entity types (Phase 1 convention): hop-*.md, malt-*.md, ...
PREFIX_TYPES = ("hop", "malt", "yeast")

# CSV columns whose value names the entity (used for the chunk title)
NAME_COLUMNS = ("name", "ingredient", "item", "style", "hop", "malt", "yeast")

# ---------------------------------------------------------------------------


def infer_type(path: Path, explicit: str | None = None) -> str:
    """Entity type for a file or CSV row.

    Precedence:
    1. explicit value          - CSV `type` column (per-row override)
    2. authoritative directory - recipes/ -> recipe, bjcp/ -> bjcp_style, styles/ -> style
    3. filename prefix         - hop-*, malt-*, yeast-*
    4. inside a tables/ dir    - table
    5. fallback                - ingredient
    """
    if explicit and explicit.strip():
        return explicit.strip().lower()
    parts = {p.lower() for p in path.parts}
    for dirname, dtype in TYPE_DIRS.items():
        if dirname in parts:
            return dtype
    prefix = path.stem.split("-", 1)[0].lower()
    if prefix in PREFIX_TYPES:
        return prefix
    if "tables" in parts:
        return "table"
    return "ingredient"


def extract_title(text: str, fallback: str) -> str:
    """Return the first markdown H1 heading, or a fallback."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


# ---------------------------------------------------------------------------
# .md / .txt
# ---------------------------------------------------------------------------

def _text_doc(path: Path, data_dir: Path) -> Document:
    text = path.read_text(encoding="utf-8")
    return Document(
        page_content=text,
        metadata={
            "source": str(path.relative_to(data_dir)),
            "type": infer_type(path),
            "title": extract_title(text, path.stem),
        },
    )


# ---------------------------------------------------------------------------
# .xml (BeerXML) -> one Document per <RECIPE>
# ---------------------------------------------------------------------------

def _el_text(parent: ET.Element | None, tag: str) -> str:
    if parent is None:
        return ""
    el = parent.find(tag)
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _num(text: str) -> float | None:
    """Parse a BeerXML numeric field; None when blank or non-numeric."""
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _positive(text: str) -> float | None:
    """Field value, or None when blank/zero (BeerXML exports pad with zeros)."""
    value = _num(text)
    return value if value is not None and value > 0 else None


def _fmt_num(value: float) -> str:
    """Trim float noise: 20.0 -> '20', 1.064 -> '1.064', 4.38 -> '4.38'."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_amount(kg: float) -> str:
    """BeerXML AMOUNT is kilograms; render grams below 1 kg for readability."""
    if kg < 1:
        return f"{_fmt_num(kg * 1000)} g"
    return f"{_fmt_num(kg)} kg"


NOTES_MAX_CHARS = 400
NOTES_TRUNCATION = " [...]"
_NOTES_NOISE_PREFIXES = ("contributed by:", "punkapi id:")


def _clean_notes(notes: str) -> str:
    """First useful paragraph of NOTES, noise stripped and length-capped.

    BeerXML exports (PunkAPI/DIY Dog) carry contributor/api metadata lines
    and a title paragraph repeating the beer name; both add no brewing
    information and eat the chunk budget.
    """
    lines = [
        line.strip()
        for line in notes.splitlines()
        if not line.strip().lower().startswith(_NOTES_NOISE_PREFIXES)
    ]
    paragraphs = [
        " ".join(p.split()) for p in "\n".join(lines).split("\n\n") if p.strip()
    ]
    # Drop a leading bare title ("Brewdog 10 Heads High") when real text follows.
    while (
        len(paragraphs) > 1
        and len(paragraphs[0]) < 80
        and not paragraphs[0].rstrip().endswith((".", "!", "?"))
    ):
        paragraphs.pop(0)
    if not paragraphs:
        return ""
    text = paragraphs[0]
    if len(text) > NOTES_MAX_CHARS:
        text = text[:NOTES_MAX_CHARS].rsplit(" ", 1)[0].rstrip() + NOTES_TRUNCATION
    return text


def render_recipe(recipe: ET.Element) -> tuple[str, str, str]:
    """BeerXML <RECIPE> -> (markdown text, recipe name, style name).

    Renders the fields a home brewer asks about: style, batch stats,
    fermentables, hop schedule, yeast, notes. Amounts follow BeerXML (kg)
    and are rendered as kg/g; zero-valued placeholders are omitted. Kept
    compact so one recipe stays one chunk; the ingest guard is the backstop.
    """
    name = _el_text(recipe, "NAME")
    style_el = recipe.find("STYLE")
    style = _el_text(style_el, "NAME")
    number = _el_text(style_el, "CATEGORY_NUMBER")
    letter = _el_text(style_el, "STYLE_LETTER")

    lines = [f"# {name or 'Unnamed recipe'} (recipe)", ""]
    if style:
        suffix = f" ({number}{letter})" if _positive(number) else ""
        lines += [f"Style: {style}{suffix}", ""]

    facts = []
    for tag, unit in (
        ("BATCH_SIZE", " L"),
        ("BOIL_SIZE", " L"),
        ("BOIL_TIME", " min"),
        ("EFFICIENCY", "%"),
    ):
        value = _positive(_el_text(recipe, tag))
        if value is not None:
            facts.append(f"{tag.replace('_', ' ').lower()} {_fmt_num(value)}{unit}")
    for tag in ("OG", "FG", "IBU"):
        value = _positive(_el_text(recipe, tag))
        if value is not None:
            facts.append(f"{tag} {_fmt_num(value)}")
    abv = _positive(_el_text(recipe, "ABV"))
    if abv is not None:
        facts.append(f"ABV {_fmt_num(abv)}%")
    color = _positive(_el_text(recipe, "COLOR"))
    if color is not None:
        facts.append(f"COLOR {_fmt_num(color)} SRM")
    if facts:
        lines += [", ".join(facts), ""]

    fermentables = recipe.findall(".//FERMENTABLE")
    if fermentables:
        lines += ["## Fermentables"]
        for f in fermentables:
            fields = [_el_text(f, "NAME") or "unnamed"]
            amount = _positive(_el_text(f, "AMOUNT"))
            if amount is not None:
                fields.append(_fmt_amount(amount))
            fcolor = _positive(_el_text(f, "COLOR"))
            if fcolor is not None:
                fields.append(f"{_fmt_num(fcolor)} SRM")
            lines.append("- " + ", ".join(fields))
        lines.append("")

    hops = recipe.findall(".//HOP")
    if hops:
        lines += ["## Hops"]
        for h in hops:
            fields = [_el_text(h, "NAME") or "unnamed"]
            amount = _positive(_el_text(h, "AMOUNT"))
            if amount is not None:
                fields.append(_fmt_amount(amount))
            use = _el_text(h, "USE")
            time_min = _positive(_el_text(h, "TIME"))
            if use and time_min is not None:
                fields.append(f"{use.lower()} {_fmt_num(time_min)} min")
            elif use:
                fields.append(use.lower())
            alpha = _positive(_el_text(h, "ALPHA"))
            if alpha is not None:
                fields.append(f"alpha {_fmt_num(alpha)}%")
            lines.append("- " + ", ".join(fields))
        lines.append("")

    yeasts = recipe.findall(".//YEAST")
    if yeasts:
        lines += ["## Yeast"]
        for y in yeasts:
            fields = [_el_text(y, "NAME") or "unnamed"]
            form = _el_text(y, "FORM")
            if form:
                fields.append(form.lower())
            atten = _positive(_el_text(y, "ATTENUATION"))
            if atten is not None:
                fields.append(f"attenuation {_fmt_num(atten)}%")
            lines.append("- " + ", ".join(fields))
        lines.append("")

    notes = _clean_notes(_el_text(recipe, "NOTES"))
    if notes:
        lines += ["## Notes", notes]

    return "\n".join(lines).strip() + "\n", name, style


def load_recipe_file(path: Path, data_dir: Path) -> list[Document]:
    """BeerXML file -> one Document per <RECIPE>; plain-text fallback."""
    source = str(path.relative_to(data_dir))
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8-sig"))
    except (ET.ParseError, OSError) as exc:
        print(f"Warning: cannot parse {path.name} as XML ({exc}); loading as text")
        return [_text_doc(path, data_dir)]

    recipes = root.findall(".//RECIPE")
    if not recipes:
        print(f"Warning: no <RECIPE> elements in {path.name}; loading as text")
        return [_text_doc(path, data_dir)]

    docs = []
    for recipe in recipes:
        content, name, style = render_recipe(recipe)
        docs.append(
            Document(
                page_content=content,
                metadata={
                    "source": source,
                    "type": "recipe",
                    "title": name or path.stem,
                    "name": name,
                    "style": style,
                },
            )
        )
    return docs


# ---------------------------------------------------------------------------
# .csv -> one Document per row
# ---------------------------------------------------------------------------

def _row_title(row: dict[str, str]) -> str:
    """Name for a CSV row: preferred name columns, else first non-type value."""
    for col in NAME_COLUMNS:
        value = row.get(col, "")
        if value:
            return value
    for key, value in row.items():
        if key.lower() != "type" and value:
            return value
    return ""


def load_table_rows(path: Path, data_dir: Path) -> list[Document]:
    """Reference table -> one Document per row (never split)."""
    source = str(path.relative_to(data_dir))
    table_label = path.stem.replace("-", " ")
    docs: list[Document] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            print(f"Warning: no header row in {path.name}; skipping")
            return []
        for row_num, raw_row in enumerate(reader, 1):
            row = {
                (k or "").strip(): (v or "").strip()
                for k, v in raw_row.items()
                if k and (v or "").strip()
            }
            if not row:
                continue
            explicit = next(
                (v for k, v in row.items() if k.lower() == "type"), None
            )
            cells = [(k, v) for k, v in row.items() if k.lower() != "type"]
            if not cells:
                continue
            title = _row_title(row) or f"{table_label} row {row_num}"
            content = (
                f"# {table_label}: {title}\n"
                + "\n".join(f"- {k}: {v}" for k, v in cells)
            )
            docs.append(
                Document(
                    page_content=content,
                    metadata={
                        "source": source,
                        "type": infer_type(path, explicit),
                        "title": title,
                    },
                )
            )
    return docs


# ---------------------------------------------------------------------------

def load_documents(data_dirs: list[Path]) -> list[Document]:
    """Load all canonical files from every dir in data_dirs.

    Directories are skipped with a warning when missing (collected_data/
    may not exist on a fresh clone). `source` is relative to the data dir
    the file was found in (collector namespaces are preserved).
    """
    docs: list[Document] = []
    for data_dir in data_dirs:
        if not data_dir.is_dir():
            print(f"Warning: data dir not found, skipping: {data_dir}")
            continue
        for pattern in ("**/*.md", "**/*.txt", "**/*.xml", "**/*.csv"):
            for path in sorted(data_dir.glob(pattern)):
                suffix = path.suffix.lower()
                if suffix == ".csv":
                    docs.extend(load_table_rows(path, data_dir))
                elif suffix == ".xml":
                    docs.extend(load_recipe_file(path, data_dir))
                else:
                    docs.append(_text_doc(path, data_dir))
    return docs


def cap_entity_text(text: str) -> tuple[str, bool]:
    """Bound a never-split entity; returns (text, was_capped).

    Prefers cutting at the last section boundary (``\\n## ``) in the second
    half of the budget so a style doc keeps whole sections; falls back to the
    last newline, then to a hard cut. The truncation marker tells the model
    (and the reader) that the document continues.
    """
    if len(text) <= MAX_ENTITY_CHARS:
        return text, False
    # Reserve room for the marker so the final chunk never exceeds the guard.
    budget = MAX_ENTITY_CHARS - len(TRUNCATION_NOTE)
    cut = text[:budget]
    idx = cut.rfind("\n## ")
    if idx >= budget // 2:
        cut = cut[:idx]
    else:
        nl = cut.rfind("\n")
        cut = cut[:nl] if nl > 0 else cut
    return cut.rstrip() + TRUNCATION_NOTE, True


def chunk_documents(docs: list[Document]) -> tuple[list[Document], int]:
    """Chunk docs by entity type; returns (chunks, n_docs_split).

    Types in NOSPLIT_TYPES stay whole (the entity IS the chunk), bounded by
    ``MAX_ENTITY_CHARS`` with ``truncated``/``chars`` metadata when capped;
    everything else goes through the text splitter (split only if oversized).
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    chunks: list[Document] = []
    n_split = 0
    for doc in docs:
        if doc.metadata.get("type") in NOSPLIT_TYPES:
            text, capped = cap_entity_text(doc.page_content)
            if capped:
                chunks.append(
                    Document(
                        page_content=text,
                        metadata={
                            **doc.metadata,
                            "truncated": True,
                            "chars": len(doc.page_content),
                        },
                    )
                )
            else:
                chunks.append(doc)
            continue
        parts = splitter.split_documents([doc])
        if len(parts) > 1:
            n_split += 1
        chunks.extend(parts)
    return chunks, n_split


def main() -> None:
    cfg = load_config()
    data_dirs: list[Path] = cfg["data_dirs"]
    chroma_dir: Path = cfg["chroma_dir"]

    docs = load_documents(data_dirs)
    if not docs:
        sys.exit(
            "No .md/.txt/.xml/.csv documents found under: "
            + ", ".join(str(d) for d in data_dirs)
        )
    print(f"Loaded {len(docs)} documents from {len(data_dirs)} data dir(s)")

    chunks, n_split = chunk_documents(docs)
    print(
        f"Chunked into {len(chunks)} chunks "
        f"(chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}; "
        f"{n_split} docs were split; recipe/table/bjcp_style never split)"
    )

    embeddings = HuggingFaceEmbeddings(model_name=cfg["embedding_model"])
    print(f"Embedding model: {cfg['embedding_model']} (first run downloads it)")

    client = chromadb.PersistentClient(path=str(chroma_dir))
    existing = [c.name for c in client.list_collections()]
    if COLLECTION_NAME in existing:
        client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing collection '{COLLECTION_NAME}' (re-ingest)")

    vectorstore = Chroma(
        client=client,
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
    )
    ids = vectorstore.add_documents(chunks)
    print(f"Stored {len(ids)} chunks in '{COLLECTION_NAME}' at {chroma_dir}")

    # Quick sanity summary by entity type
    counts: dict[str, int] = {}
    for d in chunks:
        counts[d.metadata["type"]] = counts.get(d.metadata["type"], 0) + 1
    capped = sum(1 for d in chunks if d.metadata.get("truncated"))
    longest = max(len(d.page_content) for d in chunks)
    print(
        "By type: "
        + ", ".join(f"{t}={n}" for t, n in sorted(counts.items()))
        + f" | capped={capped} at {MAX_ENTITY_CHARS} chars | longest={longest}"
    )


if __name__ == "__main__":
    main()
