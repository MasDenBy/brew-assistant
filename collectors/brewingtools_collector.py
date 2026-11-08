"""brewing.tools open ingredient database (hops) -> one markdown doc per variety.

Source: https://brewing.tools/data/hops.json -- a single JSON document (no HTML),
123 hop varieties, ODbL 1.0, generated 2026-09-28. Every record names the
manufacturer document it was transcribed from (Hopsteiner / John I. Haas /
Hop Products Australia / Crosby Hops / BarthHaas).

Naming convention: this module is ``brewingtools_collector.py`` and its registry
id is ``brewingtools`` -- ``<resource>_collector.py`` with ``id = "<resource>"``
(see ``collectors/base.py``). Namespace: ``collect_dest/brewingtools/``.

Why not beerMaverick: https://beermaverick.com/hops/ is behind a Cloudflare
managed challenge (HTTP 403, ``Cf-Mitigated: challenge``) for every path,
including robots.txt and its internal /api/js/ endpoint. A requests-based
collector cannot read it; bypassing needs a headless browser. See
PHASE_2_PLAN.md §8.

License note: data is © the publishers named in each record, redistributed by
brewing.tools under ODbL 1.0. Rendered files are written to ``collect_dest``
(outside git) for personal local RAG use and are never committed. ODbL
share-alike applies to derived *databases*, not to answers or recipes produced
from them.

Output contract (collectors/base.py): ``dest/ingredients/hop-<id>.md``
(``ingest.infer_type()`` classifies by the ``hop-`` filename prefix).
"""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .base import DataCollector, SourceUnavailableError, register
from .web import fetch

SOURCE_URL = "https://brewing.tools/data/hops.json"
DATASET_LABEL = "brewing.tools open ingredient database"
DATASET_PAGE = "https://brewing.tools/charts"
DEFAULT_VERSION = "0.7.0"
MIN_RECORDS = 50          # sanity floor: real dataset has 123
MAX_DOC_CHARS = 1900      # ingest CHUNK_SIZE is 2000
PROFILE_MAX_CHARS = 700   # pedigree/notes budget
MAX_SUBSTITUTES = 12
MAX_AROMA = 12

OIL_ORDER = (
    ("myrcenePercent", "myrcene"),
    ("humulenePercent", "humulene"),
    ("caryophyllenePercent", "caryophyllene"),
    ("farnesenePercent", "farnesene"),
    ("linaloolPercent", "linalool"),
    ("geraniolPercent", "geraniol"),
)

#: Hop.ranges key -> dataset field
RANGE_KEYS = (
    ("alpha", "alphaAcidPercent"),
    ("beta", "betaAcidPercent"),
    ("cohumulone", "cohumulonePercentOfAlpha"),
    ("oil", "totalOilMlPer100g"),
)

_WS_RE = re.compile(r"\s+")


@dataclass
class Hop:
    """One brewing.tools hop record, normalized for rendering."""

    id: str = ""                       # "citra-hbc-394" (dataset slug; used for the filename)
    name: str = ""                     # "Citra HBC 394"
    origin: str = ""                   # "USA" (may be "")
    aliases: list[str] = field(default_factory=list)
    aroma: list[str] = field(default_factory=list)
    substitutes: list[str] = field(default_factory=list)   # resolved display names
    pairings: list[str] = field(default_factory=list)      # resolved display names
    ranges: dict[str, dict] = field(default_factory=dict)  # alpha/beta/cohumulone/oil objects
    oils: list[tuple[str, dict]] = field(default_factory=list)  # (label, range object)
    pedigree: str = ""
    notes: str = ""
    publisher: str = ""
    document: str = ""
    document_url: str = ""
    retrieved: str = ""
    dataset_version: str = DEFAULT_VERSION  # payload "version" (rendered in the Source line)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _squash(text: str) -> str:
    """Collapse all whitespace (incl. ``&nbsp;``) into single spaces."""
    return _WS_RE.sub(" ", text.replace("\xa0", " ")).strip()


def _slug(text: str) -> str:
    """ASCII filename slug: fold accents, lowercase, '-' runs."""
    text = text.replace("®", "").replace("™", "")  # NFKD would expand ™ -> "TM"
    folded = unicodedata.normalize("NFKD", text)
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def _truncate(text: str, limit: int) -> str:
    """Trim ``text`` to ``limit`` chars at a sentence/word boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: max(0, limit - 1)]
    for boundary in (". ", "! ", "? ", "\n\n", "\n", " "):
        index = cut.rfind(boundary)
        if index >= limit // 2:
            return cut[: index + len(boundary)].rstrip() + " …"
    return cut.rstrip(" ,;:-") + " …"


def _dedupe(items: list[str]) -> list[str]:
    """Case-insensitively deduplicate, preserving first-seen order."""
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        key = item.casefold()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _num(value) -> str:
    """``11.0 -> "11"``, ``14.3 -> "14.3"``, ``0 -> "0"``."""
    if isinstance(value, str):
        return value.strip()
    text = f"{float(value)}"
    return text[:-2] if text.endswith(".0") else text


def _range(obj, unit: str = "") -> str | None:
    """Format one published numeric band; ``None`` when nothing is published.

    ``{"min":4.5,"max":7}`` -> ``"4.5–7"``; ``{"typical":12.1}`` -> ``"12.1"``;
    ``{"max":2.8}`` -> ``"up to 2.8"``; ``{"min":1.5}`` -> ``"from 1.5"``.
    """
    if not isinstance(obj, dict) or not obj:
        return None
    low = obj.get("min")
    high = obj.get("max")
    typical = obj.get("typical")
    if low is not None and high is not None:
        return f"{_num(low)}–{_num(high)}{unit}"
    if typical is not None:
        return f"{_num(typical)}{unit}"
    if high is not None:
        return f"up to {_num(high)}{unit}"
    if low is not None:
        return f"from {_num(low)}{unit}"
    return None


def _is_zero_pair(obj) -> bool:
    """True for ``{"min":0,"max":0}`` -- the dataset's "not published" marker."""
    return (
        isinstance(obj, dict)
        and "min" in obj
        and "max" in obj
        and "typical" not in obj
        and obj["min"] == 0
        and obj["max"] == 0
    )


def _copy_range(value) -> dict:
    """Defensive copy of one dataset range object (never mutate the payload)."""
    return dict(value) if isinstance(value, dict) and value else {}


def _text_list(value) -> list[str]:
    """Non-empty strings from a JSON list, whitespace-squashed."""
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        if isinstance(entry, str):
            text = _squash(entry)
        elif isinstance(entry, (int, float)) and not isinstance(entry, bool):
            text = _num(entry)
        else:
            continue
        if text:
            items.append(text)
    return items


def _id_to_name(hop_id: str) -> str:
    """Fallback display name from a dataset id: ``sla-dek`` -> ``Sla Dek``."""
    words = [w for w in re.split(r"[-_\s]+", _squash(hop_id)) if w]
    return " ".join(
        w if any(ch.isdigit() for ch in w) else w[:1].upper() + w[1:]
        for w in words
    ) or hop_id


def _capped(items: list[str], limit: int) -> str:
    """Comma-separated list capped at ``limit`` items, with ``, …`` when cut."""
    text = ", ".join(items[:limit])
    if len(items) > limit:
        text += ", …"
    return text


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _resolve_refs(refs, hop: Hop, names: dict[str, str]) -> list[str]:
    """Resolve dataset ids to display names, dropping self-refs and duplicates."""
    resolved: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        name = names.get(ref) or _id_to_name(ref)
        key = name.casefold()
        if ref == hop.id or key == hop.name.casefold() or key in seen:
            continue
        seen.add(key)
        resolved.append(name)
    return resolved


def _parse_hop(item: dict, hop_id: str, names: dict[str, str], version: str) -> Hop:
    """Map one dataset record to a :class:`Hop` (missing fields stay empty)."""
    name = _squash(str(item.get("name") or "")) or names.get(hop_id) or _id_to_name(hop_id)
    hop = Hop(
        id=hop_id,
        name=name,
        origin=_squash(str(item.get("origin") or "")),
        aliases=_dedupe(_text_list(item.get("aliases"))),
        aroma=_dedupe(_text_list(item.get("aromaDescriptors"))),
        pedigree=_squash(str(item.get("pedigree") or "")),
        notes=_squash(str(item.get("notes") or "")),
        dataset_version=version,
    )

    hop.substitutes = _resolve_refs(_dedupe(_text_list(item.get("substitutes"))), hop, names)
    hop.pairings = _resolve_refs(_dedupe(_text_list(item.get("pairings"))), hop, names)

    for key, source_key in RANGE_KEYS:
        copied = _copy_range(item.get(source_key))
        if copied:
            hop.ranges[key] = copied

    composition = item.get("oilComposition")
    if isinstance(composition, dict):
        for source_key, label in OIL_ORDER:
            copied = _copy_range(composition.get(source_key))
            if copied:
                hop.oils.append((label, copied))

    provenance = item.get("provenance")
    if isinstance(provenance, dict):
        primary = provenance.get("primary")
        if isinstance(primary, dict):
            hop.publisher = _squash(str(primary.get("publisher") or ""))
            hop.document = _squash(str(primary.get("document") or ""))
            hop.document_url = _squash(str(primary.get("url") or ""))
            hop.retrieved = _squash(str(primary.get("retrieved") or ""))
    return hop


def _load_dataset(payload: bytes) -> tuple[list[Hop], int]:
    """Parse a hops.json payload -> ``(hops, skipped_malformed_records)``."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SourceUnavailableError(
            f"brewing.tools: payload is not JSON ({SOURCE_URL}) - "
            "source moved or blocking automated access?"
        )
    if not isinstance(data, dict):
        raise SourceUnavailableError(
            f"brewing.tools: payload is not a JSON object ({SOURCE_URL})"
        )

    items = data.get("hops")
    if not isinstance(items, list) or not items:
        raise SourceUnavailableError(
            f"brewing.tools: payload has no 'hops' list ({SOURCE_URL}) - "
            "dataset restructured?"
        )

    version = _squash(str(data.get("version") or "")) or DEFAULT_VERSION

    # Build the id -> display-name map for the WHOLE dataset first, so
    # substitutes/pairings resolve even when the target record comes later.
    names: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        hop_id = _squash(str(item.get("id") or ""))
        if not hop_id:
            continue
        names[hop_id] = _squash(str(item.get("name") or "")) or _id_to_name(hop_id)

    hops: list[Hop] = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        hop_id = _squash(str(item.get("id") or ""))
        if not hop_id:
            skipped += 1
            continue
        hops.append(_parse_hop(item, hop_id, names, version))
    return hops, skipped


def parse_dataset(payload: bytes) -> list[Hop]:
    """Pure parser for one brewing.tools hops.json payload (fully offline)."""
    return _load_dataset(payload)[0]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _title(name: str) -> str:
    """``# {name} Hops`` -- no "Hops Hops" when the name already says hops."""
    suffix = "" if name.casefold().rstrip().endswith(("hops", "hop")) else " Hops"
    return f"# {name}{suffix}"


def _profile_text(hop: Hop) -> str:
    """``pedigree`` then ``notes``, joined into one narrative."""
    return _squash(" ".join(part for part in (hop.pedigree, hop.notes) if part))


def _provenance_lines(hop: Hop) -> list[str]:
    """``"{publisher} — {document} (retrieved {date})"`` + URL (whatever exists)."""
    attribution = " — ".join(
        part for part in (hop.publisher, hop.document) if part
    )
    if hop.retrieved:
        attribution = (
            f"{attribution} (retrieved {hop.retrieved})"
            if attribution
            else f"Retrieved {hop.retrieved}"
        )
    lines = [attribution] if attribution else []
    if hop.document_url:
        lines.append(hop.document_url)
    return lines


def _finish(lines: list[str]) -> str:
    """Output hygiene: no trailing whitespace, max one blank line, one newline."""
    text = "\n".join(line.rstrip() for line in "\n".join(lines).splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"


def render_hop(hop: Hop) -> str:
    """Render one hop variety as a compact, one-chunk markdown document.

    Order is retrieval-driven: title, type, origin, alpha/beta acids,
    substitutes and aroma land inside the embedding window; the narrative
    profile and provenance follow.
    """
    lines = [_title(hop.name), ""]

    meta = ["**Type:** Ingredient (Hop)"]
    if hop.origin:
        meta.append(f"**Origin:** {hop.origin}")
    if hop.aliases:
        meta.append(f"**Also known as:** {_capped(hop.aliases, MAX_SUBSTITUTES)}")
    meta.append(
        f"**Source:** {DATASET_LABEL} v{hop.dataset_version} (ODbL 1.0)"
        f" — {DATASET_PAGE}"
    )
    lines += meta + [""]

    spec_lines: list[str] = []
    alpha = _range(hop.ranges.get("alpha"), "%")
    if alpha:
        spec_lines.append(f"- Alpha acid: {alpha}")
    beta = _range(hop.ranges.get("beta"), "%")
    if beta:
        spec_lines.append(f"- Beta acid: {beta}")
    cohumulone = _range(hop.ranges.get("cohumulone"), "%")
    if cohumulone:
        spec_lines.append(f"- Cohumulone: {cohumulone} of alpha")
    total_oil = _range(hop.ranges.get("oil"), " mL/100g")
    if total_oil:
        spec_lines.append(f"- Total oils: {total_oil}")

    oil_parts: list[str] = []
    for label, obj in hop.oils:
        if _is_zero_pair(obj):
            continue  # dataset marker for "not published"
        value = _range(obj, "%")
        if value:
            oil_parts.append(f"{label} {value}")
    if oil_parts:
        spec_lines.append(f"- Oil composition: {', '.join(oil_parts)}")

    if spec_lines:
        lines += ["## Key Specs", *spec_lines, ""]

    if hop.substitutes:
        lines += ["## Substitutions", _capped(hop.substitutes, MAX_SUBSTITUTES), ""]
    if hop.aroma:
        lines += ["## Aroma", _capped(hop.aroma, MAX_AROMA), ""]
    if hop.pairings:
        lines += ["## Pairs well with", _capped(hop.pairings, MAX_SUBSTITUTES), ""]

    header = "\n".join(lines)  # ends with "\n" (the trailing blank element)
    provenance_lines = _provenance_lines(hop)
    reserve = 0
    if provenance_lines:
        # "\n## Provenance\n" + body + "\n"
        reserve = len("\n## Provenance\n") + len("\n".join(provenance_lines)) + 1

    profile_source = _profile_text(hop)
    budget = MAX_DOC_CHARS - len(header) - len("\n## Profile\n") - 1 - reserve
    budget = max(300, min(PROFILE_MAX_CHARS, budget))
    profile = _truncate(profile_source, budget) if profile_source else ""

    body = list(lines)
    if profile:
        body += ["## Profile", profile, ""]
    if provenance_lines:
        body += ["## Provenance", *provenance_lines, ""]
    text = _finish(body)

    if len(text) > MAX_DOC_CHARS and profile:
        # Unreachable for real data (header is bounded); safety net for
        # pathological payloads: shrink the narrative by the overflow.
        overflow = len(text) - MAX_DOC_CHARS
        profile = _truncate(profile, max(0, len(profile) - overflow - 1))
        body = list(lines)
        if profile:
            body += ["## Profile", profile, ""]
        if provenance_lines:
            body += ["## Provenance", *provenance_lines, ""]
        text = _finish(body)
    if len(text) > MAX_DOC_CHARS:  # absolute cap, never exceed one chunk
        text = text[:MAX_DOC_CHARS].rstrip() + "\n"
    return text


def hop_filename(hop: Hop) -> str:
    """``hop-<dataset-id>.md`` -- the ``hop-`` prefix is the ingest contract.

    The dataset ids are already URL-safe kebab-case, so they are used verbatim
    (``_slug`` only guards against unexpected characters).
    """
    slug = _slug(hop.id) if hop.id else ""
    return f"hop-{slug}.md" if slug else "hop-variety.md"


def write_documents(docs: dict[str, str], dest: Path) -> list[Path]:
    """Write rendered docs into ``dest/ingredients/`` and return their paths.

    Idempotent: clears only the collector's own subtree first, so stale files
    from previous runs never survive. Never writes outside ``dest``.
    """
    ingredients_dir = dest / "ingredients"
    if dest.exists():
        shutil.rmtree(dest)  # namespace rule: clear own subtree only
    ingredients_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for filename, text in docs.items():
        target = ingredients_dir / filename
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

@register
class BrewingToolsCollector(DataCollector):
    id = "brewingtools"
    description = (
        "brewing.tools open hop database "
        "(alpha/beta acids, oils, aroma, substitutes)"
    )

    def collect(self, dest: Path) -> list[Path]:
        # Fetch + parse + render everything BEFORE clearing dest: a failed run
        # must never wipe previously collected hop files.
        payload = fetch(SOURCE_URL)          # SourceUnavailableError propagates
        hops, skipped = _load_dataset(payload)
        if skipped:
            print(f"brewingtools: {skipped} records skipped (malformed)")
        if len(hops) < MIN_RECORDS:
            raise SourceUnavailableError(
                f"brewingtools: only {len(hops)} hop records in {SOURCE_URL} "
                f"(expected >= {MIN_RECORDS}) - dataset restructured?"
            )

        rendered: dict[str, str] = {}
        for hop in hops:
            filename = hop_filename(hop)
            if filename in rendered:  # deterministic collision suffix
                stem = filename[:-3]
                suffix = 2
                while f"{stem}-{suffix}.md" in rendered:
                    suffix += 1
                filename = f"{stem}-{suffix}.md"
            rendered[filename] = render_hop(hop)

        print(f"brewingtools: {len(rendered)} hop varieties")
        return write_documents(rendered, dest)
