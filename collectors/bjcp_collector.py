"""BJCP 2021 beer style guidelines -> one markdown file per style.

Source: the community JSON export linked from bjcp.org
("JSON Version of the 2021 beer guidelines"):
https://github.com/beerjson/bjcp-json/blob/main/styles/bjcp_styleguide-2021.json

Structure (verified): ``{"beerjson": {"version": 1.0, "styles": [...]}}`` --
110 styles, each with ``style_id`` (e.g. "21A"), ``name``, ``category``,
narrative fields and stat ranges ``{"minimum": {"unit", "value"},
"maximum": {...}}`` for OG, FG, IBU, ABV and color.

Rendered per style (each section only when the field exists): vital
statistics, characteristic ingredients, overall impression, aroma,
appearance, flavor, mouthfeel, comparison, origin/history, notes,
examples -- plus a ``Type:`` line (ale/lager/wild derived from the
fermentation tag, raw ``type`` field as fallback) and a ``Tags:`` line
(fermentation, strength, color, region; region tags are the compact
origin signal). Section order is retrieval-driven: ingredients sit
early so their yeast/hop/malt words fall inside the embedding window.

Data quirks handled:
- 17 styles (mead/cider) have no numeric stats, only a
  ``vital_statistics`` text -- rendered instead of the stat bullets;
- 4 styles miss ``flavor`` (for Altbier the flavor text lives in
  ``notes``) -- ``notes`` is rendered as its own section;
- ~3 entries are Spanish/Portuguese translations of the guidelines --
  field aliases (``sabor``/``aspecto``/``impresion_general``/
  ``historia``/...) are used as fallbacks so no style loses its
  narrative;
- there is no ``origin`` field in the export: origin lives in
  ``history`` (rendered as "Origin and history").

License note: BJCP style guidelines are copyrighted by the BJCP (copies
permitted for judging/education); the JSON export is community-maintained.
Rendered files are written to ``collect_dest`` (outside git) for personal
local use only -- never committed or redistributed from this repository.

Output contract (collectors/base.py): ``dest/bjcp/*.md``
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .base import DataCollector, SourceUnavailableError, register
from .web import fetch

JSON_URL = (
    "https://raw.githubusercontent.com/beerjson/bjcp-json/"
    "main/styles/bjcp_styleguide-2021.json"
)

# stat field -> (label, unit suffix rendered after the values)
STAT_SPECS = (
    ("OG", "original_gravity", ""),
    ("FG", "final_gravity", ""),
    ("ABV", "alcohol_by_volume", "%"),
    ("IBU", "international_bitterness_units", ""),
    ("Color", "color", "SRM"),
)

# fields measured in specific gravity; some entries store these x1000
# (X2 IPA Argenta: OG 1055 instead of 1.055)
GRAVITY_FIELDS = {"original_gravity", "final_gravity"}

# fermentation tag -> human "Type" line (top-fermented = ale, etc.)
FERMENTATION_TYPES = {
    "top-fermented": "ale (top-fermented)",
    "bottom-fermented": "lager (bottom-fermented)",
    "wild-fermented": "wild (wild-fermented)",
    "wild-fermentation": "wild (wild-fermentation)",
    "any-fermentation": "any fermentation",
}

# narrative field -> localized fallbacks used by the ~3 translated entries
# (Spanish/Portuguese BJCP renderings of the same guidelines)
FIELD_ALIASES = {
    "overall_impression": ("impresion_general", "impressao_geral"),
    "appearance": ("aspecto", "aparencia"),
    "flavor": ("sabor",),
    "mouthfeel": ("sensacion_en_boca", "sensacao_de_boca"),
    "history": ("historia",),
    "style_comparison": ("comparacoes_de_estilo",),
    "ingredients": ("ingredientes",),
    "examples": ("ejemplos_comerciales", "exemplos_comerciais"),
    "tags": ("marcacoes",),
}


def _first_text(style: dict, field: str) -> str:
    """First non-empty string among a field and its localized aliases."""
    for key in (field, *FIELD_ALIASES.get(field, ())):
        value = style.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _type_text(style: dict, tags: list[str]) -> str | None:
    """Human style type: ale/lager/wild from the fermentation tag,
    falling back to the raw ``type`` field ('beer')."""
    for tag in tags:
        mapped = FERMENTATION_TYPES.get(tag)
        if mapped:
            return mapped
    raw = str(style.get("type") or "").strip()
    return raw or None


def _slug(text: str) -> str:
    """URL/filename-safe slug: lowercase, non-alphanumeric runs -> '-'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def style_filename(style: dict) -> str:
    """``{"style_id": "21A", "name": "American IPA"}`` -> ``21a-american-ipa.md``."""
    style_id = _slug(str(style.get("style_id") or ""))
    name = _slug(str(style.get("name") or "")) or "style"
    return f"{style_id}-{name}.md" if style_id else f"{name}.md"


def _fmt_range(stat: object, suffix: str = "", *, gravity: bool = False) -> str | None:
    """``{"minimum": {"value": lo}, "maximum": {"value": hi}}`` -> ``lo - hi``.

    ``gravity=True`` repairs entries that store specific gravity x1000
    (1055 -> 1.055); real SG values never exceed 10.
    """
    if not isinstance(stat, dict):
        return None
    lo = (stat.get("minimum") or {}).get("value")
    hi = (stat.get("maximum") or {}).get("value")
    if gravity:
        lo = lo / 1000 if isinstance(lo, (int, float)) and lo > 10 else lo
        hi = hi / 1000 if isinstance(hi, (int, float)) and hi > 10 else hi
    if lo is None and hi is None:
        return None
    if lo is None:
        return f"up to {hi}{suffix}"
    if hi is None:
        return f"{lo}{suffix} and up"
    if lo == hi:
        return f"{lo}{suffix}"
    if suffix == "%":  # per-value unit: 5.5% - 7.5%
        return f"{lo}% - {hi}%"
    sep = f" {suffix}" if suffix else ""
    return f"{lo} - {hi}{sep}"


def render_style(style: dict) -> str:
    """Render one style entry as a compact markdown document.

    Front-loads what retrieval needs (heading, category, type, tags, vital
    stats and characteristic ingredients fit the embedding model's
    ~1000-char window -- yeast/hop/malt words must co-occur with the
    style name or ingredient queries miss this doc), then the full
    narrative for generation. Missing sections are omitted.
    """
    style_id = str(style.get("style_id") or "").strip()
    name = str(style.get("name") or "").strip() or "Unnamed style"
    heading = f"{style_id} {name}".strip()

    lines = [f"# {heading} (BJCP style)", ""]

    meta_lines = []
    category = _first_text(style, "category")
    if category:
        meta = f"Category: {category}"
        guide = _first_text(style, "style_guide")
        if guide:
            meta += f" ({guide})"
        meta_lines.append(meta)

    tags_text = _first_text(style, "tags")
    tags = [t.strip() for t in tags_text.split(",") if t.strip()]
    type_text = _type_text(style, tags)
    if type_text:
        meta_lines.append(f"Type: {type_text}")
    if tags:
        meta_lines.append(f"Tags: {', '.join(tags)}")
    if meta_lines:
        lines += meta_lines + [""]

    stats = []
    for label, field, suffix in STAT_SPECS:
        text = _fmt_range(style.get(field), suffix, gravity=field in GRAVITY_FIELDS)
        if text:
            stats.append(f"{label}: {text}")
    vital_text = _first_text(style, "vital_statistics")
    if stats or vital_text:
        lines += ["## Vital statistics"]
        if stats:
            # ONE dense line: multi-line bullets push the narrative past the
            # embedding window (~1000 chars) and break aroma/flavor queries
            lines.append(" | ".join(stats))
        if vital_text:
            lines.append(vital_text)
        lines.append("")

    def add_section(title: str, field: str) -> None:
        text = _first_text(style, field)
        if text:
            lines.extend([f"## {title}", text, ""])

    # Order is retrieval-driven: the embedding model only sees roughly the
    # first ~1000 chars, so style name + type/tags + vital stats +
    # characteristic ingredients (yeast/hop/malt words) must land inside
    # that window; the narrative follows for generation.
    add_section("Characteristic ingredients", "ingredients")
    add_section("Overall impression", "overall_impression")
    add_section("Aroma", "aroma")
    add_section("Appearance", "appearance")
    add_section("Flavor", "flavor")
    add_section("Mouthfeel", "mouthfeel")
    add_section("Comparison with other styles", "style_comparison")
    add_section("Origin and history", "history")
    add_section("Notes", "notes")
    add_section("Examples", "examples")

    return "\n".join(lines).rstrip() + "\n"


def parse_styles(payload: bytes) -> list[dict]:
    """Validate the beerjson payload and return its style entries.

    Raises SourceUnavailableError on any unexpected structure so the runner
    reports SKIP instead of writing garbage into the knowledge base.
    """
    try:
        data = json.loads(payload)
        styles = data["beerjson"]["styles"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SourceUnavailableError(f"unexpected BJCP JSON structure: {exc}") from exc
    if not isinstance(styles, list):
        raise SourceUnavailableError("beerjson.styles is not a list")
    entries = [
        s
        for s in styles
        if isinstance(s, dict) and str(s.get("name") or "").strip()
    ]
    if not entries:
        raise SourceUnavailableError("BJCP JSON export contains no named styles")
    return entries


@register
class BjcpCollector(DataCollector):
    id = "bjcp"
    description = "BJCP 2021 style guidelines (110 styles, beerjson export)"

    def collect(self, dest: Path) -> list[Path]:
        # Download + parse BEFORE clearing dest: a failed fetch must not
        # wipe previously collected guidelines.
        payload = fetch(JSON_URL)
        styles = parse_styles(payload)

        rendered: dict[str, str] = {}
        for style in styles:
            rendered[style_filename(style)] = render_style(style)

        bjcp_dir = dest / "bjcp"
        if dest.exists():
            shutil.rmtree(dest)  # namespace rule: clear own subtree only
        bjcp_dir.mkdir(parents=True, exist_ok=True)

        written = []
        for filename, text in rendered.items():
            target = bjcp_dir / filename
            target.write_text(text, encoding="utf-8")
            written.append(target)
        return written
