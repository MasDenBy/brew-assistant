"""Brewers Association beer style guidelines -> one markdown file per style.

Source: https://www.brewersassociation.org/edu/brewers-association-beer-style-guidelines/
(single page, current edition only). The page embeds the full guideline text
as structured HTML -- no pagination, no JS rendering:

    <h1 id='Ale Styles'>Ale Styles</h1>                       style family
    <h2 class='origin block-heading'>British Origin ...</h2>  origin group
    <div class="beer-style">
      <ul class="list-with-heading" id="941">                 stable numeric style id
        <li>Ordinary Bitter</li>                              first li = name
        <li><strong>Color:</strong> Gold to copper</li>       label/value attribute li
        ...
      </ul>
      <ul class="horizontal wider">                            vital statistics
        <li><strong>Original Gravity (°Plato)</strong> 1.033-1.038 (8.3-9.5 °P)</li>
        ...
      </ul>
    </div>

Parsed with the stdlib HTML parser (no new dependencies), scoped to
``<section id="beer-styles">``. Whitespace collapses wholesale because live
``li`` texts contain huge tab runs.

Rendered per style: heading ``(BA style)`` to distinguish these docs from
BJCP style docs at retrieval time, meta line (family/origin/id/source),
then vital statistics on ONE dense line, then the attribute sections in
source order (Color, Clarity, malt/hop/bitterness/fermentation/body,
Additional notes). Vital statistics sit early on purpose: the embedding
model sees roughly the first ~1000 chars, so style name + type context +
OG/FG/ABV/IBU/EBC must land inside that window.

Sanity guard: fewer than ``MIN_STYLES`` parsed styles means the page
layout changed -- SKIP, never write garbage into the knowledge base.

License note: Brewers Association style guidelines are copyrighted by the
Brewers Association. Rendered files are written to ``collect_dest``
(gitignored) for personal local use only -- never committed or
redistributed from this repository.

Output contract (collectors/base.py): ``dest/ba/styles/*.md``
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .base import DataCollector, SourceUnavailableError, register
from .web import fetch_text

PAGE_URL = (
    "https://www.brewersassociation.org/edu/"
    "brewers-association-beer-style-guidelines/"
)

EDITION = "2026"

# minimum parsed styles for the payload to be considered intact (~170 today)
MIN_STYLES = 50

# vital-statistics label -> compact label rendered in the dense stats line
STAT_ALIASES = {
    "original gravity (°plato)": "OG",
    "apparent extract/final gravity (°plato)": "FG",
    "alcohol by weight (volume)": "Alcohol",
    "bitterness (ibu)": "IBU",
    "color srm (ebc)": "Color",
}


@dataclass
class Style:
    """One style entry from the guidelines page."""

    style_id: str
    name: str = ""
    family: str = ""  # Ale Styles / Lager Styles / Hybrid/Mixed Lagers or Ale
    origin: str = ""  # e.g. "British Origin Ale Styles"
    # label -> value pairs in source order (attributes and vital statistics)
    attributes: list[tuple[str, str]] = field(default_factory=list)
    stats: list[tuple[str, str]] = field(default_factory=list)


def _collapse(text: str) -> str:
    """Collapse any whitespace run (incl. the huge tab runs in live li)."""
    return " ".join(text.split())


class _StylesParser(HTMLParser):
    """Walk the guidelines page and collect one :class:`Style` per block."""

    _SKIP = {"script", "style", "head", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.styles: list[Style] = []
        self._active = False  # inside <section id="beer-styles">
        self._section_depth = 0
        self._skip_depth = 0
        self._family = ""
        self._origin = ""
        self._cur: Style | None = None
        self._stage = ""  # "", "attributes" (name/li labels), "stats"
        self._h1_pending = False  # inside a family heading
        self._h2_pending = False  # inside an origin heading
        self._label_parts: list[str] = []
        self._value_parts: list[str] = []
        self._in_strong = False
        self._strong_done = False

    # -- tag handling -------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _StylesParser._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attrs_d = {k: (v or "") for k, v in attrs}
        classes = set(attrs_d.get("class", "").split())

        if tag == "section":
            self._section_depth += 1
            if self._section_depth == 1 and attrs_d.get("id") == "beer-styles":
                self._active = True
            return
        if not self._active:
            return

        if tag == "h1":
            self._h1_pending = True
            return
        if tag == "h2" and "origin" in classes:
            self._h2_pending = True
            return

        if tag == "ul":
            uid = attrs_d.get("id", "").strip()
            if "list-with-heading" in classes and uid.isdigit():
                self._flush_style()
                self._cur = Style(style_id=uid, family=self._family, origin=self._origin)
                self._stage = "attributes"
            elif self._cur is not None and self._stage == "attributes" and classes == {
                "horizontal",
                "wider",
            }:
                self._stage = "stats"
            return

        if tag == "li" and self._cur is not None and self._stage in (
            "attributes",
            "stats",
        ):
            self._label_parts = []
            self._value_parts = []
            self._in_strong = False
            self._strong_done = False
            return

        if tag == "strong" and self._in_li():
            self._in_strong = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _StylesParser._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag == "section":
            self._section_depth -= 1
            if self._section_depth == 0:
                self._active = False
            return
        if not self._active:
            return

        if tag == "h1" and self._take_pending("_h1_pending"):
            self._family = _collapse("".join(self._value_parts))
            self._value_parts = []
            return
        if tag == "h2" and self._take_pending("_h2_pending"):
            self._origin = _collapse("".join(self._value_parts))
            self._value_parts = []
            return

        if tag == "strong" and self._in_li():
            self._in_strong = False
            self._strong_done = True
            return

        if tag == "li" and self._in_li():
            self._consume_li()
            return

        if tag == "ul":
            if self._stage == "attributes":
                self._stage = "stats"  # expect the vital-statistics ul next
            elif self._stage == "stats":
                self._stage = "done"

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._active:
            return
        if self._in_li():
            if self._in_strong:
                self._label_parts.append(data)
            else:
                self._value_parts.append(data)
        elif self._h1_pending or self._h2_pending:
            self._value_parts.append(data)

    # -- helpers ------------------------------------------------------------

    def _in_li(self) -> bool:
        """Text capture runs only while a tracked li is open."""
        return self._cur is not None and self._stage in ("attributes", "stats")

    def _take_pending(self, attr: str) -> bool:
        if getattr(self, attr, False):
            self._h1_pending = False
            self._h2_pending = False
            return True
        return False

    def _consume_li(self) -> None:
        label = _collapse("".join(self._label_parts)).rstrip(":").strip()
        value = _collapse("".join(self._value_parts)).strip()
        cur = self._cur
        assert cur is not None
        if self._stage == "stats":
            if label:
                cur.stats.append((label, value))
        elif label and cur.name:
            cur.attributes.append((label, value))
        elif label:
            # attribute li before the name li — treat the text as the name
            cur.name = value
        else:
            cur.name = value or label
        self._label_parts = []
        self._value_parts = []

    def _flush_style(self) -> None:
        if self._cur is not None and self._cur.name:
            self.styles.append(self._cur)
        self._cur = None
        self._stage = ""

    def finish(self) -> None:
        """Close the last open style block (called after feed())."""
        self._flush_style()


def parse_guidelines(html: str) -> list[Style]:
    """Parse the guidelines page -> style records.

    Raises :class:`SourceUnavailableError` on parser failure or when no
    styles were found at all (garbage page); the ``MIN_STYLES`` sanity
    guard is enforced by ``collect()`` because small fixtures also go
    through :func:`parse_guidelines`.
    """
    parser = _StylesParser()
    try:
        parser.feed(html)
        parser.close()
        parser.finish()
    except Exception as exc:  # noqa: BLE001 — malformed HTML must SKIP, not crash
        raise SourceUnavailableError(f"could not parse BA guidelines: {exc}") from exc

    styles = [s for s in parser.styles if s.name.strip()]
    if not styles:
        raise SourceUnavailableError(
            "no styles parsed from the BA guidelines page — layout changed?"
        )
    return styles


def _slug(text: str) -> str:
    """URL/filename-safe slug: lowercase, non-alphanumeric runs -> '-'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def style_filename(style: Style) -> str:
    """``Style(id=941, name='Ordinary Bitter')`` -> ``941-ordinary-bitter.md``."""
    slug = _slug(style.name) or "style"
    return f"{style.style_id}-{slug}.md" if style.style_id else f"{slug}.md"


def render_style(style: Style) -> str:
    """Render one style entry as retrieval-first markdown."""
    heading = f"# {style.name} (BA style)"

    meta = [f"Family: {style.family}"] if style.family else []
    if style.origin:
        meta.append(f"Origin: {style.origin}")
    if style.style_id:
        meta.append(f"BA style id: {style.style_id}")
    meta.append(f"Source: Brewers Association Beer Style Guidelines ({EDITION})")

    lines = [heading, "", " | ".join(meta), ""]

    if style.stats:
        dense = []
        for label, value in style.stats:
            compact = STAT_ALIASES.get(label.lower(), label)
            dense.append(f"{compact}: {value}" if value else compact)
        lines += ["## Vital statistics", " | ".join(dense), ""]

    for label, value in style.attributes:
        if value:
            lines += [f"## {label}", value, ""]

    return "\n".join(lines).rstrip() + "\n"


def write_documents(rendered: dict[str, str], dest: Path) -> list[Path]:
    """Write rendered styles under ``dest/styles/``; clear own subtree first."""
    styles_dir = dest / "styles"
    if dest.exists():
        shutil.rmtree(dest)  # namespace rule: clear own subtree only
    styles_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, text in rendered.items():
        target = styles_dir / filename
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


@register
class BrewersAssociationCollector(DataCollector):
    id = "ba"
    description = (
        f"Brewers Association {EDITION} style guidelines "
        "(~170 styles, current edition page)"
    )

    def collect(self, dest: Path) -> list[Path]:
        # Fetch + parse BEFORE clearing dest: a failed/unexpected payload
        # must not wipe previously collected guidelines.
        payload = fetch_text(PAGE_URL)
        styles = parse_guidelines(payload)
        if len(styles) < MIN_STYLES:
            # sanity guard: a half-rendered or restructured page must SKIP
            # instead of writing garbage into the knowledge base.
            raise SourceUnavailableError(
                f"only {len(styles)} style(s) parsed from the BA guidelines "
                f"page (expected >= {MIN_STYLES}) — page layout changed?"
            )

        rendered: dict[str, str] = {}
        for style in styles:
            rendered[style_filename(style)] = render_style(style)
        return write_documents(rendered, dest)
