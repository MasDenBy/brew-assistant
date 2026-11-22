"""Fermentis (Lesaffre) active dry beer yeasts -> one markdown document per yeast.

Source: the Fermentis beer & brewing catalog,
``https://fermentis.com/en/fermentation/beer-brewing-solutions/active-dry-yeast-beer/``
(23 SafAle / SafLager / SafBrew products). The site is WordPress with an open
REST API, so the collector never scrapes HTML listings: one GET of

    https://fermentis.com/wp-json/wp/v2/product?product_cat=60&per_page=100

returns every product; ``product_cat=60`` is the "Active dry yeast beer"
category (verified 2026-11-15: exactly the 23 beer yeasts). Each item carries
``title.rendered``, ``link`` and the full product content in
``content.rendered`` — no per-product page fetches are needed.

Content structure (verified across all 23 products; sections are optional and
their titles vary):

- ``<h2 class="section-title..."><span class="section-title-main">`` — the
  tagline ("The strong & robust lager yeast, ideal for neutral beers");
- description ``<p>`` paragraphs, optionally an ``Ingredients: ...`` one;
- an optional "``<name> overview``" section of stat banner pairs — a label
  ``<p><strong>Total esters</strong></p>`` plus the value inside
  ``div.text-box-content`` (labels may wrap with ``<br/>``, e.g. "Total sup.
  alcohols"; values may be empty, e.g. SafAle US-05 phenolic off-flavor);
- an optional "``<name> sensory profile``" section (one sentence or a bullet
  list, sometimes repeating the description verbatim);
- a "``<name> technical features``" tabbed section with three ``div.panel``
  tabs (ids ``tab_dosage-&-usage``, ``tab_typical-analysis``,
  ``tab_storage-&-shelf-life``). The dosage & usage panel mixes
  product-specific facts (dosage/temperature line, rehydration temperature,
  special instructions for blends and conditioning yeasts) with Lesaffre
  boilerplate repeated verbatim on every page ("Lesaffre know-how ...", the
  long direct-pitching paragraph, ①/② markers) — the boilerplate is replaced
  by one canonical compressed line so each yeast fits the one-chunk budget;
- trailing marketing paragraphs (universal, dropped) and "Related content"
  link sliders (dropped).

Data quirks handled: section order/titles vary (some products skip the
overview section or add a sensory profile); the dosage & usage panel comes in
three layouts (①/② markers, ``o``-bulleted fragments, all-caps ``<p>``
headers) and the direct-pitching boilerplate appears in several wordings,
some split across paragraphs — all variants are normalized or dropped so
only product-specific facts remain; the typical-analysis list nests the
facts one or two levels deep, with or without layout-only
``<li style="list-style-type: none">`` wrappers, and some products inline
the microbiology bullets after ``<br/>`` — bullets are routed by label
prefix ("Viable yeast", "Purity"); stat labels and ``<sup>`` exponents are
normalized (``10^9``); en-dash/non-breaking-hyphen marks in names fold to
ASCII for filenames; the source contains unclosed ``<p>``/``<li>`` tags, so
the parser closes the nearest matching open tag (HTML5-style recovery).

License note: product data is © Fermentis (Lesaffre). Pages are fetched once
for personal, local RAG use; rendered files are written to ``collect_dest``
(outside git) and are never committed or redistributed from this repository.

Output contract (collectors/base.py): ``dest/ingredients/yeast-*.md``
(ingest.infer_type() classifies by the ``yeast-`` filename prefix).
"""
from __future__ import annotations

import html
import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .base import DataCollector, SourceUnavailableError, register
from .web import fetch, fetch_text, html_to_markdown

API_URL = (
    "https://fermentis.com/wp-json/wp/v2/product"
    "?product_cat=60&per_page=100&page={page}"
)
MAX_API_PAGES = 3           # safety cap; 100 per page covers the 23 products
MAX_PRODUCTS = 100          # safety cap on the catalog size
MAX_DOC_CHARS = 1900        # ingest CHUNK_SIZE is 2000 (one yeast = one chunk)
PROFILE_MAX_CHARS = 800     # cap for the narrative description
USAGE_MAX_CHARS = 600       # cap for product-specific usage instructions
SENSORY_MAX_CHARS = 400     # cap for the sensory profile section
STORAGE_MAX_CHARS = 400     # cap for the storage/shelf-life section

BRANDS = ("SafAle", "SafLager", "SafBrew")

# overview stat key (letters/digits only, upper-case) -> bullet label, in
# canonical render order; unknown labels fall back to title-cased text
SPEC_LABELS = {
    "APPARENTATTENUATION": "Apparent attenuation",
    "ALCOHOLTOLERANCE": "Alcohol tolerance",
    "SEDIMENTATIONTIME": "Sedimentation",
    "TOTALESTERS": "Total esters",
    "TOTALSUPALCOHOLS": "Total sup. alcohols",
    "PHENOLICOFFFLAVOR": "Phenolic off-flavor (POF)",
}
SPEC_ORDER = tuple(SPEC_LABELS)

# universal Lesaffre boilerplate (identical on every product page); the
# direct-pitching paragraph is replaced by the canonical compressed line
_BOILERPLATE_PREFIXES = (
    "lesaffre know-how",
    "each fermentis yeast is developed under a specific production scheme",
    "fermentis dry brewing yeasts are well known",
)
# the direct-pitching paragraph appears in several wordings (and split across
# paragraphs); all variants are boilerplate, the marker sets direct_pitching
_DIRECT_PITCH_RE = re.compile(
    r"^(pitch the (dry )?yeast directly (in|into) the fermentation vessel"
    r"|progressively sprinkle the (dry )?yeast"
    r"|ideally, the yeast will be added during the first part)"
)
DIRECT_PITCHING_LINE = (
    "Direct pitching: sprinkle the dry yeast onto the wort surface at or "
    "above fermentation temperature."
)
# pack-size line near the page header ("Available in 11,5g – 100g – 500g and
# 10kg packaging."); the captured group keeps the sizes in page wording
_PACKAGING_RE = re.compile(r"available in\s+(.+?)\s+packaging", re.IGNORECASE)
# universal storage paragraph (identical on every standard product page)
_CANONICAL_STORAGE_PREFIX = "for less than 6 months"
_CANONICAL_STORAGE = (
    "Below 24 °C for up to 6 months; below 15 °C for longer storage."
)

_WS_RE = re.compile(r"\s+")
_MARK_RE = re.compile("[™®]")
_DASHES_RE = re.compile("[‐‑‒–—]")
_LABEL_RE = re.compile(r"^(viable yeast|purity)\s*:?\s*", re.IGNORECASE)
_SHELF_MONTHS_RE = re.compile(r"(\d+)\s*months?\s*from production", re.IGNORECASE)


@dataclass
class Yeast:
    """One Fermentis product, normalized for rendering."""

    name: str = ""            # "SafLager W-34/70" (marks and entities removed)
    part: str = ""            # "W-34/70" — name without the brand
    brand: str = ""           # "SafAle" | "SafLager" | "SafBrew" (may be "")
    tagline: str = ""         # marketing one-liner under the page H2
    description: str = ""     # squashed narrative paragraphs, truncated
    ingredients: str = ""     # "Yeast (Saccharomyces pastorianus), ..."
    specs: dict[str, str] = field(default_factory=dict)  # normalized key -> value
    spec_labels: dict[str, str] = field(default_factory=dict)  # key -> raw label
    sensory: str = ""         # sensory-profile text distinct from description
    dosage: str = ""          # "80 to 120 g/hl at ideally 12°C – 18°C ..."
    usage: str = ""           # product-specific usage instructions, squashed
    direct_pitching: bool = False   # dosage panel had the ① Direct pitching marker
    packaging: str = ""       # "11,5g – 100g – 500g and 10kg" (pack sizes)
    viability: str = ""       # "> 6.0 *10^9 cfu/g"
    purity: str = ""          # "> 99.9 %"
    storage: str = ""         # storage temperature rules (canonical or raw)
    shelf_life: str = ""      # "Shelf life: 36 months." (or first sentence)
    source_url: str = ""      # product page URL, for the citation line


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _squash(text: str) -> str:
    """Collapse all whitespace (incl. ``&nbsp;``) into single spaces."""
    return _WS_RE.sub(" ", text.replace("\xa0", " ")).strip()


def _clean(text: str) -> str:
    """Squash + fold trademark marks and Unicode dashes to plain ASCII."""
    text = _squash(text)
    text = _MARK_RE.sub("", text)
    return _DASHES_RE.sub("-", text).strip()


def _slug(text: str) -> str:
    """ASCII filename slug: fold accents/marks, lowercase, '-' runs."""
    text = _MARK_RE.sub("", text)
    text = _DASHES_RE.sub("-", text)
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


def _is_boilerplate(paragraph: str) -> bool:
    """True for the Lesaffre marketing text repeated on every product page."""
    folded = paragraph.casefold()
    return any(folded.startswith(prefix) for prefix in _BOILERPLATE_PREFIXES) or bool(
        _DIRECT_PITCH_RE.match(folded)
    )


def _spec_key(label: str) -> str:
    """Normalize an overview stat label to its letters-only lookup key."""
    return re.sub(r"[^A-Z0-9]+", "", label.upper())


def _spec_label(key: str) -> str:
    """Canonical bullet label; unknown keys are title-cased."""
    mapped = SPEC_LABELS.get(key)
    if mapped:
        return mapped
    text = " ".join(word.lower() for word in key.split())
    return text[:1].upper() + text[1:]


def brand_and_part(name: str) -> tuple[str, str]:
    """Split ``SafLager W-34/70`` into ``("SafLager", "W-34/70")``."""
    for brand in BRANDS:
        if brand.casefold() in name.casefold():
            part = re.sub(re.escape(brand), " ", name, flags=re.IGNORECASE)
            part = _clean(part).strip(" ,;-")
            return brand, part
    return "", _clean(name)


# ---------------------------------------------------------------------------
# REST payload enumeration
# ---------------------------------------------------------------------------

def products_from_payload(payload: bytes) -> list[dict[str, str]]:
    """REST items as ``{slug, title, link, content}`` string dicts.

    Keeps only beer-yeast products (slug prefix ``safale-|saflager-|
    safbrew-``). Tolerates garbage: an unparseable payload (or one without a
    product list) yields ``[]`` — the collector turns that into a
    :class:`SourceUnavailableError`.
    """
    try:
        data = json.loads(payload.decode("utf-8-sig", errors="replace"))
    except (ValueError, UnicodeError):
        return []
    if not isinstance(data, list):
        return []

    prefix_re = re.compile(r"^(safale|saflager|safbrew)-")
    products: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip().lower()
        if not prefix_re.match(slug):
            continue
        title = item.get("title")
        content = item.get("content")
        body = content.get("rendered", "") if isinstance(content, dict) else ""
        products.append(
            {
                "slug": slug,
                "title": str(title.get("rendered") or "") if isinstance(title, dict) else "",
                "link": str(item.get("link") or "").strip(),
                "content": body if isinstance(body, str) else "",
            }
        )
    return products


# ---------------------------------------------------------------------------
# content.rendered parsing
# ---------------------------------------------------------------------------

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _ProductParser(HTMLParser):
    """Stateful parser for one Fermentis product ``content.rendered``.

    Walks the page section by section, keyed on the
    ``span.section-title-main`` headings ("... overview", "... sensory
    profile", "... technical features", "Related content"): description
    paragraphs first, then the overview stat banner pairs, then the three
    technical tab panels. Scripts, styles and everything after a "Related"
    heading never contribute text.

    A tag/attrs stack makes the depth tracking robust against the nested
    wrapper ``div``s (``div.text-box-content`` value banners, the
    ``div.tab-panels`` region and its ``div.panel`` tabs) even though
    ``handle_endtag`` carries no attributes.
    """

    _SKIP_TAGS = {"script", "style", "svg", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # collected output
        self.tagline = ""
        self.desc_parts: list[str] = []
        self.ingredients = ""
        self.pairs: list[tuple[str, str]] = []   # overview stat (label, value)
        self.sensory_parts: list[str] = []
        self.dosage_parts: list[str] = []        # dosage panel: first kept p
        self.usage_parts: list[str] = []         # dosage panel: product-specific
        self.direct_pitching = False
        self.packaging = ""                      # pack sizes line, if present
        self.viability = ""
        self.purity = ""
        self.storage_parts: list[str] = []
        self.shelf_parts: list[str] = []

        # state
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._skip_depth = 0
        self._section = "before"   # before|description|overview|sensory|technical|related
        self._tagline_done = False

        self._in_title = False
        self._title_parts: list[str] = []

        self._in_p = False
        self._p_parts: list[str] = []

        # overview banners
        self._value_depth = 0      # inside div.text-box-content
        self._pending_label = ""
        self._label_parts: list[str] = []
        self._in_value_p = False
        self._value_parts: list[str] = []

        # technical tab region / panels
        self._panels_depth = 0     # inside div.tab-panels
        self._panel = ""           # ""|dosage|analysis|storage
        self._panel_depth = 0

        # list bullets (sensory section and the typical-analysis panel)
        self._in_li = False
        self._li_parts: list[str] = []
        self._li_stack: list[list[str]] = []   # suspended parent bullets (nested)

        # storage panel routing
        self._target = ""          # ""|storage|shelf_life
        self._in_h4 = False
        self._h4_parts: list[str] = []

    # -- classification helpers -----------------------------------------
    def _classify_title(self, text: str) -> None:
        folded = text.casefold()
        if folded.startswith("related"):
            self._section = "related"
        elif "overview" in folded:
            self._section = "overview"
        elif "sensory" in folded:
            self._section = "sensory"
        elif "technical" in folded:
            self._section = "technical"
        elif not self._tagline_done:
            self.tagline = text
            self._tagline_done = True
            self._section = "description"
        else:
            self._section = "related"   # unknown trailing heading -> ignore rest

    @staticmethod
    def _panel_of(div_id: str) -> str:
        if "dosage" in div_id:
            return "dosage"
        if "analysis" in div_id:
            return "analysis"
        if "storage" in div_id:
            return "storage"
        return ""

    def _route_target(self, heading: str) -> None:
        folded = heading.casefold()
        if "shelf" in folded:
            self._target = "shelf_life"
        elif "storage" in folded:
            self._target = "storage"

    # -- paragraph routing ----------------------------------------------
    def _finish_paragraph(self, raw: str) -> None:
        text = _squash(raw)
        if not text:
            return
        # pack-size line ("Available in 11,5g – 100g – 500g and 10kg
        # packaging.") sits near the header, outside every section panel
        m = _PACKAGING_RE.search(text)
        if m:
            self.packaging = m.group(1).strip(" .")
            return
        if self._section == "description":
            folded = text.casefold()
            if folded.startswith("ingredients"):
                self.ingredients = _clean(
                    re.sub(r"^ingredients\s*:?", "", text, flags=re.IGNORECASE)
                )
            else:
                self.desc_parts.append(text)
        elif self._section == "sensory":
            self.sensory_parts.append(text)
        elif self._section == "technical" and self._panel == "dosage":
            folded = text.casefold()
            if len(text) <= 2 or (len(text) <= 30 and text.isupper()):
                return    # stray "." fragments and all-caps "<p>" headers
            if folded.startswith("①") or folded.startswith("direct pitching"):
                self.direct_pitching = True
            elif folded.startswith("②") or folded.startswith("with prior rehydration"):
                pass    # the rehydration recipe paragraph itself is kept below
            elif _is_boilerplate(text):
                if _DIRECT_PITCH_RE.match(folded):
                    self.direct_pitching = True   # variant without the ① marker
            elif not self.dosage_parts and not self.usage_parts:
                self.dosage_parts.append(text)   # first fact = dosage/temperature
            else:
                self.usage_parts.append(text)
        elif self._section == "technical" and self._panel == "storage":
            if _is_boilerplate(text):
                pass
            elif self._target == "storage":
                self.storage_parts.append(text)
            elif self._target == "shelf_life":
                self.shelf_parts.append(text)

    def _finish_analysis_line(self, raw: str) -> None:
        """Route a top-level analysis bullet by its label prefix.

        Products order/wrap the list differently (some wrap everything in a
        transparent ``<li style="list-style-type: none">``, some keep the
        microbiology bullets inline after ``<br/>``), so only the label
        prefix decides where a bullet lands; everything else (microbiology
        boilerplate) is ignored.
        """
        text = _squash(raw)
        if not text:
            return
        text = text.split("•")[0]   # inline microbiology bullets (E-30 style)
        folded = text.casefold()
        if folded.startswith("viable yeast"):
            if not self.viability:
                self.viability = _analysis_value(text)
        elif folded.startswith("purity"):
            if not self.purity:
                self.purity = _analysis_value(text)

    # -- HTMLParser hooks ------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list) -> None:
        parsed = dict(attrs)

        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _VOID_TAGS:
            return    # never closed: keep the stack balanced
        self._stack.append((tag, parsed))
        if self._skip_depth:
            return

        classes = set((parsed.get("class") or "").split())

        if tag == "span":
            if "section-title-main" in classes:
                self._in_title = True
                self._title_parts = []
        elif tag == "p":
            self._in_p = True
            self._p_parts = []
            if self._section == "overview" and self._value_depth:
                self._in_value_p = True
        elif tag == "div":
            if "text-box-content" in classes:
                self._value_depth += 1
            if self._panels_depth:
                self._panels_depth += 1
            elif "tab-panels" in classes:
                self._panels_depth = 1
            if self._panels_depth:
                if not self._panel and "panel" in classes:
                    self._panel = self._panel_of((parsed.get("id") or "").lower())
                    self._panel_depth = 1
                elif self._panel:
                    self._panel_depth += 1
        elif tag == "h4":
            if self._panel == "storage":
                self._in_h4 = True
                self._h4_parts = []
        elif tag == "li":
            if self._in_li:
                # nested bullet: suspend the parent, capture the child's own
                # text (products nest the facts one or two levels deep, with
                # or without layout-only "list-style-type: none" wrappers)
                self._li_stack.append(self._li_parts)
            else:
                self._in_li = True
            self._li_parts = []
        elif tag == "sup":
            if self._in_li:
                self._li_parts.append("^")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return    # void tags are never on the stack
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return    # skip tags are never pushed onto the stack
        # HTML5-style recovery: the source has unclosed <p>/<li> tags (e.g.
        # "</li>" closing a "<p>"), so an end tag closes the NEAREST matching
        # open tag and every unclosed element above it.
        index = next(
            (i for i in range(len(self._stack) - 1, -1, -1) if self._stack[i][0] == tag),
            None,
        )
        if index is None:
            return    # stray close with nothing open: ignore
        while len(self._stack) > index:
            closed_tag, attrs = self._stack.pop()
            self._on_end(closed_tag, attrs)

    def _on_end(self, tag: str, attrs: dict[str, str]) -> None:
        if self._skip_depth:
            return

        classes = set((attrs.get("class") or "").split())

        if tag == "span":
            if self._in_title and "section-title-main" in classes:
                self._in_title = False
                self._classify_title(_squash("".join(self._title_parts)))
        elif tag == "p":
            self._in_p = False
            if self._section == "overview":
                if self._in_value_p:
                    self._in_value_p = False
                    value = _squash("".join(self._value_parts))
                    self._value_parts = []
                    if self._pending_label and value:
                        self.pairs.append((self._pending_label, value))
                    self._pending_label = ""
                else:
                    label = _squash("".join(self._label_parts))
                    if label:
                        self._pending_label = label
                    self._label_parts = []
            else:
                self._finish_paragraph("".join(self._p_parts))
        elif tag == "div":
            if "text-box-content" in classes:
                self._value_depth = max(0, self._value_depth - 1)
            if self._panels_depth:
                self._panels_depth -= 1
                if self._panels_depth == 0:
                    self._panel = ""
                    self._panel_depth = 0
            if self._panel:
                self._panel_depth -= 1
                if self._panel_depth <= 0:
                    self._panel = ""
        elif tag == "h4":
            if self._in_h4:
                self._in_h4 = False
                self._route_target(_squash("".join(self._h4_parts)))
        elif tag == "li":
            if self._in_li:
                text = "".join(self._li_parts)
                if self._li_stack:
                    self._li_parts = self._li_stack.pop()   # resume the parent bullet
                else:
                    self._in_li = False
                    self._li_parts = []
                if self._section == "sensory":
                    squashed = _squash(text)
                    if squashed:
                        self.sensory_parts.append(squashed)
                elif self._section == "technical" and self._panel == "analysis":
                    self._finish_analysis_line(text)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        if tag == "br":
            self._emit_space()

    def _emit_space(self) -> None:
        """``<br/>`` inside a captured buffer becomes a plain space."""
        if self._in_li:
            self._li_parts.append(" ")
        elif self._in_p:
            if self._section == "overview":
                if self._value_depth:
                    self._value_parts.append(" ")
                else:
                    self._label_parts.append(" ")
            else:
                self._p_parts.append(" ")
        elif self._in_h4:
            self._h4_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_li:
            self._li_parts.append(data)
        elif self._in_p:
            if self._section == "overview":
                if self._value_depth:
                    self._value_parts.append(data)
                else:
                    self._label_parts.append(data)
            else:
                self._p_parts.append(data)
        elif self._in_h4:
            self._h4_parts.append(data)


def parse_item(item: dict[str, str]) -> Yeast | None:
    """Parse one REST product item; ``None`` when it is not a yeast page.

    A page is rejected when the title or content is missing/empty. Partial
    documents are kept: missing specs/sensory/storage are simply omitted.
    """
    name = _clean(html.unescape(str(item.get("title") or "")))
    content = str(item.get("content") or "")
    if not name or not content.strip():
        return None

    parser = _ProductParser()
    parser.feed(content)
    parser.close()

    description = _truncate(" ".join(parser.desc_parts), PROFILE_MAX_CHARS)

    specs: dict[str, str] = {}
    spec_labels: dict[str, str] = {}
    for label, value in parser.pairs:
        key = _spec_key(label)
        if not key or key in specs:
            continue
        specs[key] = _clean(value)
        spec_labels[key] = _squash(label).rstrip(":")

    sensory = " ".join(parser.sensory_parts)
    if sensory and sensory.casefold() in description.casefold():
        sensory = ""    # some products repeat the description as "sensory"

    brand, part = brand_and_part(name)

    dosage = _squash(" ".join(parser.dosage_parts))
    usage = _squash(_join_usage(parser.usage_parts))

    storage_raw = " ".join(parser.storage_parts)
    if storage_raw.casefold().startswith(_CANONICAL_STORAGE_PREFIX):
        storage = _CANONICAL_STORAGE
    else:
        storage = _truncate(storage_raw, STORAGE_MAX_CHARS)

    shelf_raw = " ".join(parser.shelf_parts)
    months = _SHELF_MONTHS_RE.search(shelf_raw)
    if months:
        shelf_life = f"Shelf life: {months.group(1)} months."
    elif shelf_raw:
        shelf_life = _truncate(shelf_raw.split(". ")[0], 120)
    else:
        shelf_life = ""

    if not (description or specs or dosage):
        return None

    return Yeast(
        name=name,
        part=part,
        brand=brand,
        tagline=_clean(parser.tagline),
        description=description,
        ingredients=_clean(parser.ingredients),
        specs=specs,
        spec_labels=spec_labels,
        sensory=_truncate(_clean(sensory), SENSORY_MAX_CHARS),
        dosage=dosage,
        usage=_truncate(usage, USAGE_MAX_CHARS),
        direct_pitching=parser.direct_pitching,
        packaging=parser.packaging,
        viability=_analysis_value(parser.viability),
        purity=_analysis_value(parser.purity),
        storage=storage,
        shelf_life=shelf_life,
        source_url=str(item.get("link") or ""),
    )


def _analysis_value(text: str) -> str:
    """``Viable yeast > 6.0 *10^9 cfu/g`` -> ``> 6.0 *10^9 cfu/g``."""
    stripped = _LABEL_RE.sub("", text).strip(" :")
    return re.sub(r"\^(\d+)cfu", r"^\1 cfu", stripped)


def packaging_from_page(page_html: str) -> str:
    """Extract the pack-size line from a rendered product page.

    "Available in 11,5g - 100g - 500g and 10kg packaging." is theme-rendered
    and absent from the REST payload (verified 2026-11-22), so it needs a
    page fetch. Empty when the line is absent or reworded.
    """
    m = _PACKAGING_RE.search(html_to_markdown(page_html))
    return m.group(1).strip(" .") if m else ""


def _join_usage(parts: list[str]) -> str:
    """Join dosage-panel paragraphs; ``o ``-bullet fragments get ``; ``.

    Several blends render their instructions as ``o``-bulleted fragments
    ("o Sprinkle the yeast in 10 times its volume ..."), which read better
    joined with semicolons than with plain spaces.
    """
    joined: list[str] = []
    for part in parts:
        if part.startswith("o "):
            if joined and joined[-1].startswith("o "):
                joined[-1] = joined[-1].rstrip().rstrip(".").rstrip() + "; " + part[2:]
            else:
                joined.append("o " + part[2:])
        else:
            joined.append(part)
    return " ".join(
        fragment[2:] if fragment.startswith("o ") else fragment for fragment in joined
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_yeast(yeast: Yeast) -> str:
    """Render one yeast as a compact, one-chunk markdown document.

    Order is retrieval-driven (same reasoning as ``omega_collector``): name,
    lab, product code, key specs and dosage must land inside the embedding
    model's ~1000-char window; the narrative profile, storage and sensory
    sections follow, truncated to whatever budget remains.
    """
    title = f"# {yeast.name} (Fermentis)"
    lines = [title, ""]

    meta = ["**Type:** Ingredient (Yeast)", "**Lab:** Fermentis (Lesaffre)"]
    if yeast.part:
        meta.append(f"**Product:** {yeast.part}")
    if yeast.tagline:
        meta.append(f"**Use case:** {yeast.tagline}")
    if yeast.ingredients:
        meta.append(f"**Ingredients:** {yeast.ingredients}")
    if yeast.source_url:
        meta.append(f"**Source:** {yeast.source_url}")
    lines += meta + [""]

    spec_lines = []
    for key in SPEC_ORDER:
        value = yeast.specs.get(key, "")
        if value:
            spec_lines.append(f"- {SPEC_LABELS[key]}: {value}")
    for key, raw_value in yeast.specs.items():
        if key in SPEC_LABELS:
            continue
        if raw_value:
            label = yeast.spec_labels.get(key) or _spec_label(key)
            spec_lines.append(f"- {label}: {raw_value}")
    if yeast.viability:
        spec_lines.append(f"- Viability: {yeast.viability}")
    if yeast.purity:
        spec_lines.append(f"- Purity: {yeast.purity}")
    if yeast.packaging:
        spec_lines.append(f"- Packaging: {yeast.packaging}")
    if spec_lines:
        lines += ["## Key Specs", *spec_lines, ""]

    usage_lines = []
    if yeast.dosage:
        usage_lines.append(yeast.dosage)
    if yeast.direct_pitching:
        usage_lines.append(DIRECT_PITCHING_LINE)
    if yeast.usage:
        usage_lines.append(yeast.usage)
    if usage_lines:
        lines += ["## Dosage & Usage", " ".join(usage_lines), ""]

    fixed = "\n".join(lines)
    total = len(fixed)

    # optional sections in priority order, each capped by the remaining budget
    sections = (
        ("## Profile", yeast.description, PROFILE_MAX_CHARS),
        ("## Storage", _storage_text(yeast), STORAGE_MAX_CHARS),
        ("## Sensory", yeast.sensory, SENSORY_MAX_CHARS),
    )
    for heading, text, cap in sections:
        remaining = MAX_DOC_CHARS - total - len(heading) - 12
        if remaining < 120:
            break
        text = _truncate(text, min(cap, remaining))
        if not text:
            continue
        lines += [heading, text, ""]
        total = len("\n".join(lines))

    # no trailing whitespace anywhere; exactly one final newline
    text = "\n".join(line.rstrip() for line in "\n".join(lines).splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"


def _storage_text(yeast: Yeast) -> str:
    parts = [part for part in (yeast.storage, yeast.shelf_life) if part]
    return " ".join(parts)


def yeast_filename(yeast: Yeast) -> str:
    """``yeast-fermentis-<brand>-<part>.md``.

    The ``yeast-`` prefix is the ingest contract: ``infer_type()`` classifies
    by filename prefix; the ``fermentis`` infix keeps these docs distinct
    from same-strain files from other labs (e.g. the ``yeast-us-05.md``
    fixture). Marks and Unicode dashes are ASCII-folded.
    """
    brand_slug = _slug(yeast.brand)
    part_slug = _slug(yeast.part) or _slug(yeast.name)
    if brand_slug and part_slug:
        return f"yeast-fermentis-{brand_slug}-{part_slug}.md"
    if part_slug:
        return f"yeast-fermentis-{part_slug}.md"
    return "yeast-fermentis-yeast.md"


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
class FermentisCollector(DataCollector):
    id = "fermentis"
    description = (
        "Fermentis (Lesaffre) active dry beer yeasts "
        "(attenuation, alcohol tolerance, dosage, storage)"
    )

    def collect(self, dest: Path) -> list[Path]:
        # Fetch + parse everything BEFORE clearing dest: a failed run must
        # never wipe previously collected yeast files.
        items = self._list_products()
        if not items:
            raise SourceUnavailableError(
                f"fermentis: no beer yeast products found at {API_URL} "
                "(site restructured or blocking automated access?)"
            )

        yeasts, failures = self._parse_items(items)
        if not yeasts:
            raise SourceUnavailableError(
                "fermentis: no product pages parsed "
                f"({failures} failures) - site restructured or blocking "
                "automated access"
            )
        failure_limit = max(2, len(items) // 10)  # 10% of items, floor 2
        if failures > failure_limit:
            raise SourceUnavailableError(
                f"fermentis: {failures}/{len(items)} products failed "
                f"(limit {failure_limit}) - site restructured or blocking "
                "automated access"
            )

        # Pack sizes live only on the rendered product pages; best effort —
        # a failed page fetch leaves packaging empty rather than failing
        # the whole run (dosage/specs from the REST payload still land).
        for yeast in yeasts:
            yeast.packaging = self._fetch_packaging(yeast)

        rendered: dict[str, str] = {}
        for yeast in yeasts:
            filename = yeast_filename(yeast)
            if filename in rendered:  # deterministic collision suffix
                stem = filename[:-3]
                suffix = 2
                while f"{stem}-{suffix}.md" in rendered:
                    suffix += 1
                filename = f"{stem}-{suffix}.md"
            rendered[filename] = render_yeast(yeast)

        skipped = len(items) - failures - len(yeasts)
        print(
            f"fermentis: {len(rendered)} yeasts, {skipped} skipped, "
            f"{failures} product failures"
        )
        return write_documents(rendered, dest)

    # -- fetching --------------------------------------------------------
    @staticmethod
    def _fetch_packaging(yeast: Yeast) -> str:
        """Fetch the rendered product page for the pack-size line (fail-soft)."""
        if not yeast.source_url:
            return yeast.packaging
        try:
            page = fetch_text(yeast.source_url)
        except SourceUnavailableError:
            return yeast.packaging
        return packaging_from_page(page) or yeast.packaging

    @staticmethod
    def _list_products() -> list[dict[str, str]]:
        """Walk the REST API pages until one returns fewer than a full page."""
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for page in range(1, MAX_API_PAGES + 1):
            payload = fetch(API_URL.format(page=page))
            new = [
                product
                for product in products_from_payload(payload)
                if product["slug"] not in seen
            ]
            if not new:
                break
            for product in new:
                seen.add(product["slug"])
                items.append(product)
            if len(new) < 100 or len(items) >= MAX_PRODUCTS:
                break
        return items

    @staticmethod
    def _parse_items(items: list[dict[str, str]]) -> tuple[list[Yeast], int]:
        """Parse every product item, tolerating individual failures."""
        yeasts: list[Yeast] = []
        failures = 0
        for item in items:
            yeast = parse_item(item)
            if yeast is None:
                print(f"fermentis: warning: {item.get('slug')} not parseable")
                failures += 1
                continue
            yeasts.append(yeast)
        return yeasts, failures
