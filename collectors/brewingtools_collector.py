"""brewing.tools open ingredient database (hops + fermentables) -> markdown docs.

Source: the brewing.tools open ingredient database -- two JSON documents from
one dataset version (0.7.0), ODbL 1.0:

* ``https://brewing.tools/data/hops.json`` -- 123 hop varieties.
* ``https://brewing.tools/data/fermentables.json`` -- 383 malt/adjunct records,
  of which the 2 liquid ``extract`` records are excluded by decision, leaving
  381 rendered malts.

Every record names the manufacturer document it was transcribed from
(Hopsteiner / John I. Haas / Hop Products Australia / Crosby Hops / BarthHaas /
Weyermann / Simpsons / Briess / ...).

Naming convention: this module is ``brewingtools_collector.py`` and its registry
id is ``brewingtools`` -- ``<resource>_collector.py`` with ``id = "<resource>"``
(see ``collectors/base.py``). Namespace: ``collect_dest/brewingtools/``.

The malt dataset publishes no substitutes, no pairings and no PPG, so malt docs
carry only what the dataset publishes -- nothing is derived or converted.

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

Output contract (collectors/base.py): ``dest/ingredients/hop-<id>.md`` and
``dest/ingredients/malt-<id>.md`` (``ingest.infer_type()`` classifies by the
``hop-`` / ``malt-`` filename prefix).
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
FERMENTABLES_URL = "https://brewing.tools/data/fermentables.json"
DATASET_LABEL = "brewing.tools open ingredient database"
DATASET_PAGE = "https://brewing.tools/charts"
DEFAULT_VERSION = "0.7.0"
MIN_RECORDS = 50          # hops sanity floor: real dataset has 123
MIN_MALT_RECORDS = 200    # malts sanity floor: real filtered count is 381
MAX_DOC_CHARS = 1900      # ingest CHUNK_SIZE is 2000
PROFILE_MAX_CHARS = 700   # pedigree/notes budget
MAX_SUBSTITUTES = 12
MAX_AROMA = 12
MAX_ALIASES = 6           # max aliases in the real data is 2

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

#: Malt.ranges key -> dataset field (published units only, never converted)
MALT_RANGE_KEYS = (
    ("colour_l", "colourLovibond"),
    ("colour_ebc", "colourEbc"),
    ("extract", "extractFineGrindDryPercent"),
    ("moisture", "moisturePercent"),
    ("max_batch", "maxInBatchPercent"),
    ("protein", "proteinPercent"),
    ("total_nitrogen", "totalNitrogenPercent"),
    ("soluble_n_ratio", "solubleNitrogenRatioPercent"),
    ("coarse_extract", "extractCoarseGrindDryPercent"),
    ("coarse_fine_diff", "coarseFineDifferencePercent"),
)

#: Malt.diastatic entries: (label, dataset field)
MALT_DIASTATIC_KEYS = (
    ("°Lintner", "diastaticPowerLintner"),
    ("°WK", "diastaticPowerWindischKolbach"),
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


@dataclass
class Malt:
    """One brewing.tools fermentable record, normalized for rendering."""

    id: str = ""                        # "weyermann-abbey-malt" (dataset slug -> filename)
    name: str = ""                      # "Abbey Malt®"
    maltster: str = ""                  # "Weyermann Specialty Malts"
    origin: str = ""                    # "Germany, EU" (may be "")
    group: str = ""                     # grainGroup, falling back to dataset type
    aliases: list[str] = field(default_factory=list)
    ranges: dict[str, dict] = field(default_factory=dict)
    diastatic: list[tuple[str, dict]] = field(default_factory=list)  # (label, range)
    description: str = ""
    notes: str = ""
    publisher: str = ""
    document: str = ""
    document_url: str = ""
    retrieved: str = ""
    dataset_version: str = DEFAULT_VERSION


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


def _apply_provenance(record: dict, target) -> None:
    """Copy ``provenance.primary`` fields onto a hop or malt record."""
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return
    primary = provenance.get("primary")
    if not isinstance(primary, dict):
        return
    target.publisher = _squash(str(primary.get("publisher") or ""))
    target.document = _squash(str(primary.get("document") or ""))
    target.document_url = _squash(str(primary.get("url") or ""))
    target.retrieved = _squash(str(primary.get("retrieved") or ""))


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

    _apply_provenance(item, hop)
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


def _parse_malt(item: dict, malt_id: str, version: str) -> Malt:
    """Map one fermentables record to a :class:`Malt` (missing fields stay empty)."""
    malt_type = _squash(str(item.get("type") or ""))
    group = _squash(str(item.get("grainGroup") or ""))
    if not group and malt_type.casefold() in ("grain", "adjunct"):
        group = malt_type

    malt = Malt(
        id=malt_id,
        name=_squash(str(item.get("name") or "")) or _id_to_name(malt_id),
        maltster=_squash(str(item.get("maltster") or "")),
        origin=_squash(str(item.get("origin") or "")),
        group=group,
        aliases=_dedupe(_text_list(item.get("aliases"))),
        description=_squash(str(item.get("description") or "")),
        notes=_squash(str(item.get("notes") or "")),
        dataset_version=version,
    )

    for key, source_key in MALT_RANGE_KEYS:
        copied = _copy_range(item.get(source_key))
        if copied:
            malt.ranges[key] = copied
    for label, source_key in MALT_DIASTATIC_KEYS:
        copied = _copy_range(item.get(source_key))
        if copied:
            malt.diastatic.append((label, copied))

    _apply_provenance(item, malt)
    return malt


def _load_malts(payload: bytes) -> tuple[list[Malt], int, int]:
    """Parse a fermentables.json payload -> ``(malts, skipped, filtered_extracts)``."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SourceUnavailableError(
            f"brewing.tools: payload is not JSON ({FERMENTABLES_URL}) - "
            "source moved or blocking automated access?"
        )
    if not isinstance(data, dict):
        raise SourceUnavailableError(
            f"brewing.tools: payload is not a JSON object ({FERMENTABLES_URL})"
        )

    items = data.get("fermentables")
    if not isinstance(items, list) or not items:
        raise SourceUnavailableError(
            f"brewing.tools: payload has no 'fermentables' list ({FERMENTABLES_URL}) - "
            "dataset restructured?"
        )

    version = _squash(str(data.get("version") or "")) or DEFAULT_VERSION

    malts: list[Malt] = []
    skipped = 0
    filtered_extracts = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        malt_id = _squash(str(item.get("id") or ""))
        if not malt_id:
            skipped += 1
            continue
        if _squash(str(item.get("type") or "")).casefold() == "extract":
            filtered_extracts += 1  # extracts are out of scope by decision
            continue
        malts.append(_parse_malt(item, malt_id, version))
    return malts, skipped, filtered_extracts


def parse_malts(payload: bytes) -> list[Malt]:
    """Pure parser for one brewing.tools fermentables.json payload (fully offline)."""
    return _load_malts(payload)[0]


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


def _provenance_lines(record: Hop | Malt) -> list[str]:
    """``"{publisher} — {document} (retrieved {date})"`` + URL (whatever exists)."""
    attribution = " — ".join(
        part for part in (record.publisher, record.document) if part
    )
    if record.retrieved:
        attribution = (
            f"{attribution} (retrieved {record.retrieved})"
            if attribution
            else f"Retrieved {record.retrieved}"
        )
    lines = [attribution] if attribution else []
    if record.document_url:
        lines.append(record.document_url)
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


def _malt_range(ranges: dict[str, dict], key: str, unit: str) -> str | None:
    """``_range`` for one malt band, skipping the dataset's 0..0 marker."""
    obj = ranges.get(key)
    if _is_zero_pair(obj):
        return None
    return _range(obj, unit)


def _malt_spec_lines(malt: Malt) -> list[str]:
    """Key-spec bullets, published bands only, in retrieval order."""
    spec_lines: list[str] = []

    colour_l = _malt_range(malt.ranges, "colour_l", " °L")
    colour_ebc = _malt_range(malt.ranges, "colour_ebc", " EBC")
    if colour_l and colour_ebc:
        spec_lines.append(f"- Colour: {colour_l} ({colour_ebc})")
    elif colour_l or colour_ebc:
        spec_lines.append(f"- Colour: {colour_l or colour_ebc}")

    extract = _malt_range(malt.ranges, "extract", "%")
    if extract:
        spec_lines.append(f"- Extract (fine grind, dry basis): {extract}")

    moisture = _malt_range(malt.ranges, "moisture", "%")
    if moisture:
        spec_lines.append(f"- Moisture: {moisture}")

    max_batch = _malt_range(malt.ranges, "max_batch", "%")
    if max_batch:
        spec_lines.append(f"- Max in batch: {max_batch}")

    diastatic: list[str] = []
    for label, obj in malt.diastatic:
        if _is_zero_pair(obj):
            continue
        value = _range(obj, f" {label}")
        if value:
            diastatic.append(value)
    if diastatic:
        combined = " (".join(diastatic) + (")" if len(diastatic) > 1 else "")
        spec_lines.append(f"- Diastatic power: {combined}")

    protein = _malt_range(malt.ranges, "protein", "%")
    if protein:
        spec_lines.append(f"- Protein: {protein}")

    total_nitrogen = _malt_range(malt.ranges, "total_nitrogen", "%")
    if total_nitrogen:
        spec_lines.append(f"- Total nitrogen: {total_nitrogen}")

    soluble_n_ratio = _malt_range(malt.ranges, "soluble_n_ratio", "%")
    if soluble_n_ratio:
        spec_lines.append(f"- Soluble nitrogen ratio: {soluble_n_ratio}")

    coarse_extract = _malt_range(malt.ranges, "coarse_extract", "%")
    if coarse_extract:
        spec_lines.append(f"- Extract (coarse grind, dry basis): {coarse_extract}")

    coarse_fine_diff = _malt_range(malt.ranges, "coarse_fine_diff", "%")
    if coarse_fine_diff:
        spec_lines.append(f"- Coarse/fine extract difference: {coarse_fine_diff}")

    return spec_lines


def _malt_profile_text(malt: Malt) -> str:
    """``description`` then ``notes``, joined into one narrative."""
    return _squash(" ".join(part for part in (malt.description, malt.notes) if part))


def render_malt(malt: Malt) -> str:
    """Render one malt/adjunct as a compact, one-chunk markdown document.

    Order is retrieval-driven: title, type/maltster/origin/group/aliases and
    the key specs land inside the embedding window; the narrative profile and
    provenance follow. The title stays the bare name -- the ``malt-`` filename
    prefix and the Type line carry the entity type.
    """
    lines = [f"# {malt.name}", ""]

    meta = ["**Type:** Ingredient (Malt)"]
    if malt.maltster:
        meta.append(f"**Maltster:** {malt.maltster}")
    if malt.origin:
        meta.append(f"**Origin:** {malt.origin}")
    if malt.group:
        meta.append(f"**Group:** {malt.group}")
    if malt.aliases:
        meta.append(f"**Also known as:** {_capped(malt.aliases, MAX_ALIASES)}")
    meta.append(
        f"**Source:** {DATASET_LABEL} v{malt.dataset_version} (ODbL 1.0)"
        f" — {DATASET_PAGE}"
    )
    lines += meta + [""]

    spec_lines = _malt_spec_lines(malt)
    if spec_lines:
        lines += ["## Key Specs", *spec_lines, ""]

    header = "\n".join(lines)  # ends with "\n" (the trailing blank element)
    provenance_lines = _provenance_lines(malt)
    reserve = 0
    if provenance_lines:
        # "\n## Provenance\n" + body + "\n"
        reserve = len("\n## Provenance\n") + len("\n".join(provenance_lines)) + 1

    profile_source = _malt_profile_text(malt)
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


def malt_filename(malt: Malt) -> str:
    """``malt-<dataset-id>.md`` -- the ``malt-`` prefix is the ingest contract."""
    slug = _slug(malt.id) if malt.id else ""
    return f"malt-{slug}.md" if slug else "malt-grain.md"


def _unique_filename(filename: str, used: dict[str, str]) -> str:
    """Append ``-2``, ``-3``, ... to a filename already taken in ``used``."""
    if filename not in used:
        return filename
    stem = filename[:-3]
    suffix = 2
    while f"{stem}-{suffix}.md" in used:
        suffix += 1
    return f"{stem}-{suffix}.md"


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
        "brewing.tools open ingredient database "
        "(hops: alpha/beta acids, oils, aroma, substitutes; "
        "malts: colour, extract, diastatic power)"
    )

    def collect(self, dest: Path) -> list[Path]:
        # Fetch + parse + render everything BEFORE clearing dest: a failed run
        # must never wipe previously collected files.
        hops_payload = fetch(SOURCE_URL)          # SourceUnavailableError propagates
        malts_payload = fetch(FERMENTABLES_URL)   # same source -> all-or-nothing
        hops, skipped_hops = _load_dataset(hops_payload)
        malts, skipped_malts, filtered_extracts = _load_malts(malts_payload)

        if skipped_hops:
            print(f"brewingtools: {skipped_hops} hop records skipped (malformed)")
        if skipped_malts:
            print(f"brewingtools: {skipped_malts} malt records skipped (malformed)")
        if filtered_extracts:
            print(f"brewingtools: {filtered_extracts} extract records excluded (no extracts)")

        if len(hops) < MIN_RECORDS:
            raise SourceUnavailableError(
                f"brewingtools: only {len(hops)} hop records in {SOURCE_URL} "
                f"(expected >= {MIN_RECORDS}) - dataset restructured?"
            )
        if len(malts) < MIN_MALT_RECORDS:
            raise SourceUnavailableError(
                f"brewingtools: only {len(malts)} malt records in {FERMENTABLES_URL} "
                f"(expected >= {MIN_MALT_RECORDS}) - dataset restructured?"
            )

        rendered: dict[str, str] = {}
        for hop in hops:
            filename = _unique_filename(hop_filename(hop), rendered)
            rendered[filename] = render_hop(hop)
        for malt in malts:
            filename = _unique_filename(malt_filename(malt), rendered)
            rendered[filename] = render_malt(malt)

        print(f"brewingtools: {len(hops)} hop varieties, {len(malts)} malts")
        return write_documents(rendered, dest)
