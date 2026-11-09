"""Wyeast culture/strain pages -> one markdown document per culture.

Source: the public Wyeast culture catalog,
``https://wyeastlab.com/product-sitemap.xml`` (Yoast product sitemap, ~101
``/product/`` URLs). Each culture links to a WooCommerce product page
``https://wyeastlab.com/product/<slug>/`` with the strain's fermentation
specs, recommended styles and a narrative profile.

Structure (verified 2026-11-08): the product page carries

- ``<h2 class="headline">Strain: 3068</h2>`` -- the strain number (the
  ``Strain:`` prefix is also the non-culture-product discriminator);
- ``<h3 class="subheadline">Weihenstephan Weizen</h3>`` -- the culture name;
- ``<div class="description">`` with ``<p><strong>Species:</strong> ...</p>``
  and ``<p><strong>Profile: </strong> ...</p>`` pairs;
- ``<div class="tabs-nav">`` (one ``div.tab-nav-item > span`` per beverage)
  and one ``ul.features`` per beverage tab with
  ``<li><h6>Flocculation</h6><span>Low</span></li>`` rows;
- ``<section class="content-block template-related-styles">`` with one
  ``<h3 class="title">`` per recommended style.

Data quirks handled:
- non-culture products (yeast hulls, Brewtan B, Tanal A, wine nutrient
  blends, yeast extract, ...) have no ``Strain:`` headline -- they are
  skipped; every actual culture is kept, including the three bacteria
  cultures (``Lactobacillus buchneri`` 5335, ``Lactobacillus brevis`` 5223,
  ``Pediococcus damnosus`` 5733);
- spec labels are open-ended (wine strain 4028 adds ``Residual Sugars``,
  ``Rate of Fermentation``, ``Foam Production``, ``Volatile Acid Production``,
  ``SO2 Production``, ``H2S Production``) -- common labels render first in
  canonical order, extras follow in source order, and raw values are
  preserved verbatim (``Low Medium``, ``Low - High``, ...);
- multi-beverage pages repeat the whole feature list per tab, so spec pairs
  are deduplicated by label (first non-empty value wins) while ``Used in``
  keeps every beverage label;
- temperature is published F-first with the Celsius value in parentheses
  (``64-75°F (18-24°C)``) and is normalized to ``18–24 °C (64–75 °F)``;
- private-collection strains carry a ``-PC`` part-number suffix
  (``2035-PC``) and get an availability line.

The ``yeast-`` filename prefix is the **ingest retrieval bucket**
(``ingest.infer_type()`` classifies by filename prefix), not a taxonomy
claim: the three bacteria cultures also live under ``yeast-*.md`` so that
sour-beer questions retrieve every culture consistently. The rendered
``Type:`` line is species-aware and says ``Ingredient (Bacteria culture)``
for those pages.

License note: product data is © Wyeast Laboratories. Pages are fetched once
for personal, local RAG use; rendered files are written to ``collect_dest``
(outside git) and are never committed or redistributed from this repository.

Enumeration fallback: if the product sitemap disappears, fail soft (the
runner reports SKIP). The server-rendered ``https://wyeastlab.com/yeast-cultures/``
listing (93 strain cards, FacetWP, no pagination) is the documented manual /
alternative source; see ``PHASE_2_PLAN.md`` §8.

Output contract (collectors/base.py): ``dest/ingredients/yeast-*.md``
(ingest.infer_type() classifies by the ``yeast-`` filename prefix).
"""
from __future__ import annotations

import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from .base import DataCollector, SourceUnavailableError, register
from .web import fetch, fetch_text

SITEMAP_URL = "https://wyeastlab.com/product-sitemap.xml"
PRODUCT_URL_MARKER = "/product/"
MAX_PRODUCT_URLS = 200          # safety cap; real sitemap has 101
PROFILE_MAX_CHARS = 800         # keeps one culture in one ingest chunk
MAX_STYLES = 25
MAX_DOC_CHARS = 1900            # ingest CHUNK_SIZE is 2000

# spec key (upper-case, from the <h6>) -> bullet label, in canonical order
SPEC_LABELS = {
    "FLOCCULATION": "Flocculation",
    "APPARENT ATTENUATION": "Apparent attenuation",
    "APPARENT ABV TOLERANCE": "Apparent ABV tolerance",
    "TEMPERATURE RANGE": "Recommended temperature",
    "RESIDUAL SUGARS": "Residual sugars",
    "RATE OF FERMENTATION": "Rate of fermentation",
    "FOAM PRODUCTION": "Foam production",
    "VOLATILE ACID PRODUCTION": "Volatile acid production",
}
SPEC_ORDER = tuple(SPEC_LABELS)
BACTERIA_GENERA = ("Lactobacillus", "Pediococcus")

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_WS_RE = re.compile(r"\s+")
_TEMP_FC_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*°?\s*[-\u2013]\s*(\d+(?:\.\d+)?)\s*°?\s*F\s*"
    r"\(\s*(\d+(?:\.\d+)?)\s*°?\s*[-\u2013]\s*(\d+(?:\.\d+)?)\s*°?\s*C\s*\)",
    re.IGNORECASE,
)
_TEMP_CF_RE = re.compile(   # C-first variant, with or without parentheses
    r"(\d+(?:\.\d+)?)\s*°?\s*[-\u2013]\s*(\d+(?:\.\d+)?)\s*°?\s*C\s*"
    r"(?:\(\s*(\d+(?:\.\d+)?)\s*°?\s*[-\u2013]\s*(\d+(?:\.\d+)?)\s*°?\s*F\s*\))?",
    re.IGNORECASE,
)


@dataclass
class Strain:
    """One Wyeast culture page, normalized for rendering."""

    part: str = ""               # "3068", "2035-PC", "4766"
    name: str = ""               # "Weihenstephan Weizen", "Cider"
    species: str = ""            # "Saccharomyces cerevisiae" (may be "")
    profile: str = ""            # plain text, squashed, already truncated
    specs: dict[str, str] = field(default_factory=dict)  # upper label -> raw value
    used_in: list[str] = field(default_factory=list)     # ["Beer"], ["Cider", "Mead"]
    styles: list[str] = field(default_factory=list)      # recommended style names
    source_url: str = ""         # culture page URL, for the citation line


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _squash(text: str) -> str:
    """Collapse all whitespace (incl. ``&nbsp;``) into single spaces."""
    return _WS_RE.sub(" ", text.replace("\xa0", " ")).strip()


def _slug(text: str) -> str:
    """ASCII filename slug: fold accents/trademarks, lowercase, '-' runs."""
    text = text.replace("®", "").replace("™", "")  # NFKD would expand ™ -> "TM"
    folded = unicodedata.normalize("NFKD", text)
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


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


def _normalize_temp(raw: str) -> str:
    """``64-75°F (18-24°C)`` -> ``18–24 °C (64–75 °F)``.

    Falls back to the whitespace-squashed raw text when the pattern does not
    match, so unusual values survive unchanged.
    """
    text = _squash(raw)
    match = _TEMP_FC_RE.search(text)
    if match:
        f_lo, f_hi, c_lo, c_hi = match.groups()
        return f"{c_lo}–{c_hi} °C ({f_lo}–{f_hi} °F)"
    match = _TEMP_CF_RE.search(text)
    if match:
        c_lo, c_hi, f_lo, f_hi = match.groups()
        return f"{c_lo}–{c_hi} °C ({f_lo}–{f_hi} °F)"
    return text


def _spec_key(label: str) -> str:
    """Normalize an upstream spec label to its lookup key."""
    return _squash(label).rstrip(":").strip().upper()


def _spec_label(key: str) -> str:
    """Canonical bullet label; fallback title-cases unknown labels.

    Acronym-ish tokens that contain digits (``SO2``, ``H2S``) keep their
    upper-case spelling.
    """
    mapped = SPEC_LABELS.get(key)
    if mapped:
        return mapped
    words = [word if any(ch.isdigit() for ch in word) else word.lower()
             for word in key.split()]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def _is_bacteria(strain: Strain) -> bool:
    """True when the species (or, failing that, the name) is a bacteria genus."""
    for candidate in (strain.species, strain.name):
        folded = candidate.casefold()
        if any(folded.startswith(genus.casefold()) for genus in BACTERIA_GENERA):
            return True
    return False


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

class _StrainParser(HTMLParser):
    """Stateful, scoped parser for one Wyeast culture detail page.

    Captures the strain headline, subheadline, description field pairs,
    beverage tab labels, spec rows from ``ul.features`` lists and style
    titles inside ``section.template-related-styles``. Scripts, styles,
    SVGs and templates never contribute text; ``img`` alt text is ignored.
    """

    _SKIP_TAGS = {"script", "style", "svg", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headline = ""
        self.subheadline = ""
        self.fields: dict[str, str] = {}
        self.specs: dict[str, str] = {}
        self.used_in: list[str] = []
        self.styles: list[str] = []

        self._skip_depth = 0

        self._in_headline = False
        self._headline_parts: list[str] = []
        self._in_subheadline = False
        self._subheadline_parts: list[str] = []

        # div.description -> <p><strong>Label:</strong> value</p>
        self._in_desc = False
        self._desc_depth = 0
        self._in_desc_p = False
        self._desc_label_parts: list[str] = []
        self._desc_value_parts: list[str] = []
        self._desc_strong_depth = 0

        # div.tabs-nav -> div.tab-nav-item -> span
        self._nav_depth = 0
        self._nav_item_depth = 0
        self._in_nav_span = False
        self._nav_span_parts: list[str] = []
        self._nav_label = ""

        # ul.features -> li -> h6 label / span value
        self._features_depth = 0
        self._in_feature_li = False
        self._in_feature_h6 = False
        self._in_feature_span = False
        self._feature_label_parts: list[str] = []
        self._feature_value_parts: list[str] = []

        # section.template-related-styles -> h3.title
        self._styles_section_depth = 0
        self._in_style_title = False
        self._style_parts: list[str] = []

    @staticmethod
    def _classes(attrs: list) -> set[str]:
        return set((dict(attrs).get("class") or "").split())

    # -- HTMLParser hooks ------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        classes = self._classes(attrs)

        if tag == "div":
            if self._in_desc:
                self._desc_depth += 1
            elif "description" in classes:
                self._in_desc = True
                self._desc_depth = 1

            if self._nav_depth:
                self._nav_depth += 1
                if "tab-nav-item" in classes and not self._nav_item_depth:
                    self._nav_item_depth = 1
                    self._nav_label = ""
                    self._nav_span_parts = []
                elif self._nav_item_depth:
                    self._nav_item_depth += 1
            elif "tabs-nav" in classes:
                self._nav_depth = 1

        elif tag == "h2":
            if "headline" in classes and not self.headline and not self._in_headline:
                self._in_headline = True
                self._headline_parts = []

        elif tag == "h3":
            if (
                "subheadline" in classes
                and not self.subheadline
                and not self._in_subheadline
            ):
                self._in_subheadline = True
                self._subheadline_parts = []
            elif (
                "title" in classes
                and self._styles_section_depth
                and not self._in_style_title
            ):
                self._in_style_title = True
                self._style_parts = []

        elif tag == "section":
            if self._styles_section_depth:
                self._styles_section_depth += 1
            elif "template-related-styles" in classes:
                self._styles_section_depth = 1

        elif tag == "p":
            if self._in_desc and not self._in_desc_p:
                self._in_desc_p = True
                self._desc_label_parts = []
                self._desc_value_parts = []
                self._desc_strong_depth = 0

        elif tag == "strong":
            if self._in_desc_p:
                self._desc_strong_depth += 1

        elif tag == "br":
            if self._in_desc_p:
                self._desc_value_parts.append(" ")

        elif tag == "ul":
            if self._features_depth:
                self._features_depth += 1
            elif "features" in classes:
                self._features_depth = 1

        elif tag == "li":
            if self._features_depth and not self._in_feature_li:
                self._in_feature_li = True
                self._in_feature_h6 = False
                self._in_feature_span = False
                self._feature_label_parts = []
                self._feature_value_parts = []

        elif tag == "h6":
            if self._in_feature_li:
                self._in_feature_h6 = True

        elif tag == "span":
            if self._in_feature_li:
                self._in_feature_span = True
            elif self._nav_item_depth and not self._nav_label:
                self._in_nav_span = True
                self._nav_span_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag == "div":
            if self._in_desc:
                self._desc_depth -= 1
                if self._desc_depth <= 0:
                    self._in_desc = False
            if self._nav_depth:
                self._nav_depth -= 1
                if self._nav_item_depth:
                    self._nav_item_depth -= 1
                    if self._nav_item_depth == 0:
                        label = _squash(self._nav_label)
                        if label:
                            self.used_in.append(label)
                        self._nav_label = ""

        elif tag == "h2":
            if self._in_headline:
                self._in_headline = False
                self.headline = _squash("".join(self._headline_parts))

        elif tag == "h3":
            if self._in_subheadline:
                self._in_subheadline = False
                self.subheadline = _squash("".join(self._subheadline_parts))
            elif self._in_style_title:
                self._in_style_title = False
                style = _squash("".join(self._style_parts))
                if style:
                    self.styles.append(style)

        elif tag == "section":
            if self._styles_section_depth:
                self._styles_section_depth -= 1

        elif tag == "p":
            if self._in_desc_p:
                self._in_desc_p = False
                label = _squash("".join(self._desc_label_parts)).rstrip(":").strip()
                value = _squash("".join(self._desc_value_parts))
                if label and value:
                    self.fields[label] = value
                self._desc_label_parts = []
                self._desc_value_parts = []
                self._desc_strong_depth = 0

        elif tag == "strong":
            if self._desc_strong_depth:
                self._desc_strong_depth -= 1

        elif tag == "ul":
            if self._features_depth:
                self._features_depth -= 1

        elif tag == "li":
            if self._in_feature_li:
                self._in_feature_li = False
                label = _squash("".join(self._feature_label_parts)).rstrip(":").strip()
                value = _squash("".join(self._feature_value_parts))
                key = _spec_key(label)
                if key and value and key not in self.specs:
                    self.specs[key] = value
                self._in_feature_h6 = False
                self._in_feature_span = False

        elif tag == "h6":
            self._in_feature_h6 = False

        elif tag == "span":
            if self._in_feature_span:
                self._in_feature_span = False
            elif self._in_nav_span:
                self._in_nav_span = False
                label = _squash("".join(self._nav_span_parts))
                if label:
                    self._nav_label = label
                self._nav_span_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_desc_p:
            if self._desc_strong_depth:
                self._desc_label_parts.append(data)
            else:
                self._desc_value_parts.append(data)
        elif self._in_headline:
            self._headline_parts.append(data)
        elif self._in_subheadline:
            self._subheadline_parts.append(data)
        elif self._in_nav_span:
            self._nav_span_parts.append(data)
        elif self._in_feature_li:
            if self._in_feature_h6:
                self._feature_label_parts.append(data)
            elif self._in_feature_span:
                self._feature_value_parts.append(data)
        elif self._in_style_title:
            self._style_parts.append(data)


def parse_strain(html: str, *, source_url: str = "") -> Strain | None:
    """Parse one product page; return ``None`` when it is not a culture page.

    A page is rejected when its ``h2.headline`` does not start with
    ``Strain:`` or it has no subheadline (yeast hulls, Brewtan B, Tanal A,
    nutrient blends and other catalog products). Partial culture documents
    are kept: missing species/profile/specs/styles are simply omitted.
    """
    parser = _StrainParser()
    parser.feed(html)
    parser.close()

    headline = _squash(parser.headline)
    name = _squash(parser.subheadline)
    if not headline.lower().startswith("strain:") or not name:
        return None

    part = _squash(headline.split(":", 1)[1])
    species = _squash(parser.fields.get("Species", ""))
    profile = _truncate(_squash(parser.fields.get("Profile", "")), PROFILE_MAX_CHARS)

    specs = dict(parser.specs)
    if "TEMPERATURE RANGE" in specs:
        specs["TEMPERATURE RANGE"] = _normalize_temp(specs["TEMPERATURE RANGE"])

    styles = _dedupe([style for style in (_squash(s) for s in parser.styles) if style])
    used_in = _dedupe([item for item in (_squash(s) for s in parser.used_in) if item])

    if not (species or profile or specs or styles):
        return None

    return Strain(
        part=part,
        name=name,
        species=species,
        profile=profile,
        specs=specs,
        used_in=used_in,
        styles=styles,
        source_url=source_url,
    )


# ---------------------------------------------------------------------------
# Sitemap enumeration
# ---------------------------------------------------------------------------

def product_urls(sitemap_xml: bytes) -> list[str]:
    """All ``/product/`` URLs from the Yoast product sitemap, in document order.

    Accepts both the sitemap namespace and bare ``loc`` elements. Returns an
    empty list for unparseable XML (the collector turns that into a
    :class:`SourceUnavailableError`).
    """
    try:
        root = ElementTree.fromstring(sitemap_xml)
    except ElementTree.ParseError:
        return []

    urls: list[str] = []
    seen: set[str] = set()
    for element in root.iter():
        if element.tag not in (_SITEMAP_NS + "loc", "loc"):
            continue
        url = (element.text or "").strip()
        if PRODUCT_URL_MARKER not in url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= MAX_PRODUCT_URLS:
            break
    return urls


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_strain(strain: Strain) -> str:
    """Render one culture as a compact, one-chunk markdown document.

    Order is retrieval-driven (same reasoning as ``whitelabs_collector``):
    name, lab, product, species, specifications and style names must land
    inside the embedding model's ~1000-char window; the narrative profile
    follows.
    """
    title = f"# {strain.part} {strain.name}" if strain.part else f"# {strain.name}"
    lines = [f"{title} (Wyeast)", ""]

    if _is_bacteria(strain):
        type_label = "Ingredient (Bacteria culture)"
    else:
        type_label = "Ingredient (Yeast)"
    meta = [f"**Type:** {type_label}", "**Lab:** Wyeast Laboratories"]
    if strain.part:
        meta.append(f"**Product:** {strain.part}")
    if strain.part.upper().endswith("-PC"):
        meta.append("**Availability:** Private Collection (seasonal release)")
    if strain.species:
        meta.append(f"**Species:** {strain.species}")
    if strain.used_in:
        meta.append(f"**Used in:** {', '.join(strain.used_in)}")
    if strain.source_url:
        meta.append(f"**Source:** {strain.source_url}")
    lines += meta + [""]

    spec_lines = []
    for key in SPEC_ORDER:
        value = _squash(strain.specs.get(key, ""))
        if value:
            spec_lines.append(f"- {SPEC_LABELS[key]}: {value}")
    for key, raw_value in strain.specs.items():
        if key in SPEC_LABELS:
            continue
        value = _squash(raw_value)
        if value:
            spec_lines.append(f"- {_spec_label(key)}: {value}")
    if spec_lines:
        lines += ["## Key Specs", *spec_lines, ""]

    styles = strain.styles[:MAX_STYLES]
    if styles:
        lines += ["## Suggested Styles", ", ".join(styles), ""]

    header = "\n".join(lines)
    budget = MAX_DOC_CHARS - len(header) - len("\n## Profile\n") - 1
    budget = max(300, min(PROFILE_MAX_CHARS, budget))
    profile = _truncate(strain.profile, budget)
    if profile:
        lines += ["## Profile", profile, ""]

    # no trailing whitespace anywhere; exactly one final newline
    text = "\n".join(line.rstrip() for line in "\n".join(lines).splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"


def strain_filename(strain: Strain) -> str:
    """``yeast-<part>-<name>.md`` (``yeast-3068-weihenstephan-weizen.md``).

    The ``yeast-`` prefix is the ingest contract: ``infer_type()`` classifies
    by filename prefix. Accents and (R)/(TM) marks are ASCII-folded
    (``Kölsch II`` -> ``kolsch-ii``), and the part number is removed from the
    name to avoid duplication.
    """
    part_slug = _slug(strain.part)
    name = strain.name
    if strain.part:
        name = re.sub(re.escape(strain.part), " ", name, flags=re.IGNORECASE)
    name_slug = _slug(name)
    if part_slug and name_slug:
        return f"yeast-{part_slug}-{name_slug}.md"
    if part_slug:
        return f"yeast-{part_slug}.md"
    if name_slug:
        return f"yeast-{name_slug}.md"
    return "yeast-culture.md"


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
class WyeastCollector(DataCollector):
    id = "wyeast"
    description = (
        "Wyeast culture/strain pages "
        "(attenuation, flocculation, temperature, styles, profile)"
    )

    def collect(self, dest: Path) -> list[Path]:
        # Fetch + parse everything BEFORE clearing dest: a failed run must
        # never wipe previously collected culture files.
        try:
            sitemap_xml = fetch(SITEMAP_URL)
        except SourceUnavailableError:
            raise
        urls = product_urls(sitemap_xml)
        if not urls:
            raise SourceUnavailableError(
                f"wyeast: no product URLs found in {SITEMAP_URL} "
                "(site restructured or blocking automated access?)"
            )

        strains, failures = self._fetch_strains(urls)
        if not strains:
            raise SourceUnavailableError(
                "wyeast: no culture pages parsed "
                f"({failures} page failures) - site restructured or blocking "
                "automated access"
            )
        failure_limit = max(3, len(urls) // 10)  # 10% of URLs, floor 3
        if failures > failure_limit:
            raise SourceUnavailableError(
                f"wyeast: {failures}/{len(urls)} product pages failed "
                f"(limit {failure_limit}) - site restructured or blocking "
                "automated access"
            )

        rendered: dict[str, str] = {}
        for strain in strains:
            filename = strain_filename(strain)
            if filename in rendered:  # deterministic collision suffix
                stem = filename[:-3]
                suffix = 2
                while f"{stem}-{suffix}.md" in rendered:
                    suffix += 1
                filename = f"{stem}-{suffix}.md"
            rendered[filename] = render_strain(strain)

        skipped = len(urls) - failures - len(strains)
        print(
            f"wyeast: {len(rendered)} cultures, {skipped} skipped, "
            f"{failures} page failures"
        )
        return write_documents(rendered, dest)

    # -- fetching --------------------------------------------------------
    @staticmethod
    def _fetch_strains(urls: list[str]) -> tuple[list[Strain], int]:
        """Fetch + parse every product page, tolerating individual failures."""
        strains: list[Strain] = []
        failures = 0
        for url in urls:
            try:
                html = fetch_text(url)
            except SourceUnavailableError as exc:
                print(f"wyeast: warning: {url} failed ({exc})")
                failures += 1
                continue
            strain = parse_strain(html, source_url=url)
            if strain is not None:
                strains.append(strain)
        return strains, failures
