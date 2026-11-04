"""Ingestion: load brewing docs from config `data_dirs`, chunk, embed, store in ChromaDB.

Reads the canonical formats produced by data collectors (contract:
collectors/base.py), from every directory in `config.yaml: data_dirs`:

    .md / .txt   style & ingredient docs        -> one doc per file
    .xml         BeerXML recipes                -> one doc per <RECIPE> (never split)
    .csv         reference tables               -> one doc per row (never split)

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


def render_recipe(recipe: ET.Element) -> tuple[str, str, str]:
    """BeerXML <RECIPE> -> (markdown text, recipe name, style name).

    Renders the fields a home brewer asks about: style, batch stats,
    fermentables, hop schedule, yeast, notes. Kept compact so one recipe
    stays one chunk (length guard added in Step 5).
    """
    name = _el_text(recipe, "NAME")
    style_el = recipe.find("STYLE")
    style = _el_text(style_el, "NAME")
    number = _el_text(style_el, "CATEGORY_NUMBER")
    letter = _el_text(style_el, "STYLE_LETTER")

    lines = [f"# {name or 'Unnamed recipe'} (recipe)", ""]
    if style:
        suffix = f" ({number}{letter})" if number else ""
        lines += [f"Style: {style}{suffix}", ""]

    facts = []
    for tag, unit in (
        ("BATCH_SIZE", " L"),
        ("BOIL_SIZE", " L"),
        ("BOIL_TIME", " min"),
        ("EFFICIENCY", "%"),
    ):
        value = _el_text(recipe, tag)
        if value:
            facts.append(f"{tag.replace('_', ' ').lower()} {value}{unit}")
    for tag in ("OG", "FG", "IBU"):
        value = _el_text(recipe, tag)
        if value:
            facts.append(f"{tag} {value}")
    abv = _el_text(recipe, "ABV")
    if abv:
        facts.append(f"ABV {abv}%")
    color = _el_text(recipe, "COLOR")
    if color:
        facts.append(f"COLOR {color} SRM")
    if facts:
        lines += [", ".join(facts), ""]

    fermentables = recipe.findall(".//FERMENTABLE")
    if fermentables:
        lines += ["## Fermentables"]
        for f in fermentables:
            fields = [_el_text(f, "NAME") or "unnamed"]
            amount = _el_text(f, "AMOUNT")
            if amount:
                fields.append(f"{amount} g")
            fcolor = _el_text(f, "COLOR")
            if fcolor:
                fields.append(f"{fcolor} SRM")
            lines.append("- " + ", ".join(fields))
        lines.append("")

    hops = recipe.findall(".//HOP")
    if hops:
        lines += ["## Hops"]
        for h in hops:
            fields = [_el_text(h, "NAME") or "unnamed"]
            amount = _el_text(h, "AMOUNT")
            if amount:
                fields.append(f"{amount} g")
            use = _el_text(h, "USE")
            time_min = _el_text(h, "TIME")
            if use:
                fields.append(f"{use.lower()} {time_min} min" if time_min else use.lower())
            alpha = _el_text(h, "ALPHA")
            if alpha:
                fields.append(f"alpha {alpha}%")
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
            atten = _el_text(y, "ATTENUATION")
            if atten:
                fields.append(f"attenuation {atten}%")
            lines.append("- " + ", ".join(fields))
        lines.append("")

    notes = _el_text(recipe, "NOTES")
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


def chunk_documents(docs: list[Document]) -> tuple[list[Document], int]:
    """Chunk docs by entity type; returns (chunks, n_docs_split).

    Types in NOSPLIT_TYPES stay whole (the entity IS the chunk); everything
    else goes through the text splitter (split only if oversized).
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    chunks: list[Document] = []
    n_split = 0
    for doc in docs:
        if doc.metadata.get("type") in NOSPLIT_TYPES:
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
    print("By type: " + ", ".join(f"{t}={n}" for t, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
