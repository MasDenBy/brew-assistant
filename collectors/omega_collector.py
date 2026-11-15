"""Omega Yeast strain pages -> one markdown document per strain.

Source: the public Omega Yeast catalog,
``https://omegayeast.com/collections/yeast`` (Shopify storefront, ~40
strains). Each strain has a product page
``https://omegayeast.com/products/<handle>/`` with the strain's fermentation
specs and a narrative blurb.

Enumeration: the open Shopify collection JSON endpoint
``https://omegayeast.com/collections/yeast/products.json?limit=250&page=N``
returns every published product in the collection with ``handle`` and the
``OYL-xxx`` SKU (``variants[0].sku``) — one or two calls total. If Shopify
ever closes the JSON endpoint, the documented fallback is scraping
``/products/<handle>`` links from the paginated HTML collection; not
implemented (see the collector docstring history).

Structure (verified 2026-11-15): the product page carries

- ``<h1 class="t-h2">West Coast II</h1>`` -- the strain name;
- a breadcrumb ``<span class="t-tag">OYL-009</span>`` -- the part number
  (also taken from the JSON SKU, which wins);
- ``<div class="t-b2-bold">`` -- the one-paragraph marketing description;
- a ``<dl>`` of ``dt``/``dd`` spec pairs: Tasting Notes, Strain Type,
  Flocculation, Attenuation, Temperature Range, Alcohol Tolerance, Hazy,
  Diastatic, Phenolic, Availability. ``dd`` values may be plain text or one
  or more pill ``<span>``s (multi-word tasting notes render as one pill per
  word, joined here with ", ").

Data quirks handled:
- spec labels are open-ended (Lacto has no Flocculation/Attenuation/Alcohol
  Tolerance; a strain type can add rows) -- common labels render first in
  canonical order, extras follow in source order, raw values preserved;
- ``Strain Type`` distinguishes yeasts from bacteria (``Bacteria``) and
  Brett strains (``Brett``): bacteria get a ``Ingredient (Bacteria culture)``
  Type line (consistent with the Wyeast collector), Brett stays a yeast;
- temperature is published F-first with the Celsius value in parentheses
  (``64-72°F (18-22°C)``) and is normalized to ``18–22 °C (64–72 °F)``;
- the only page with no ``<dl>`` specs is not a strain page -> skipped.

License note: product data is © Omega Yeast Labs. Pages are fetched once
for personal, local RAG use; rendered files are written to ``collect_dest``
(outside git) and are never committed or redistributed from this repository.

Output contract (collectors/base.py): ``dest/ingredients/yeast-*.md``
(ingest.infer_type() classifies by the ``yeast-`` filename prefix).
"""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .base import DataCollector, SourceUnavailableError, register
from .web import fetch, fetch_text

COLLECTION_URL = "https://omegayeast.com/collections/yeast"
LISTING_URL = COLLECTION_URL + "/products.json?limit=250&page={page}"
PRODUCT_URL = "https://omegayeast.com/products/{handle}"
MAX_LISTING_PAGES = 5           # safety cap; 250 per page covers ~40 products
MAX_PRODUCTS = 200              # safety cap; real catalog is ~40
PROFILE_MAX_CHARS = 800         # keeps one strain in one ingest chunk
MAX_DOC_CHARS = 1900            # ingest CHUNK_SIZE is 2000

# spec key (upper-case, from the <dt>) -> bullet label, in canonical order
SPEC_LABELS = {
    "FLOCCULATION": "Flocculation",
    "ATTENUATION": "Attenuation",
    "TEMPERATURE RANGE": "Recommended temperature",
    "ALCOHOL TOLERANCE": "Alcohol tolerance",
    "TASTING NOTES": "Tasting notes",
    "STRAIN TYPE": "Strain type",
    "HAZY": "Hazy",
    "DIASTATIC": "Diastatic",
    "PHENOLIC": "Phenolic",
}
SPEC_ORDER = tuple(SPEC_LABELS)
# rendered as a meta line (render_strain), never as a Key Specs bullet
META_LABELS = {"AVAILABILITY"}
BACTERIA_NAME_MARKERS = ("lactobacillus", "pediococcus", "lacto", "pedio")

_WS_RE = re.compile(r"\s+")
_SKU_RE = re.compile(r"\bOYL-\d+\b", re.IGNORECASE)
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
    """One Omega strain page, normalized for rendering."""

    name: str = ""               # H1 text ("West Coast II", "Lacto")
    part: str = ""               # "OYL-009" (may be "")
    strain_type: str = ""        # "Ale", "Lager", "Bacteria", "Brett", ...
    tasting_notes: str = ""      # pill texts joined with ", " (may be "")
    specs: dict[str, str] = field(default_factory=dict)  # upper label -> value
    availability: str = ""       # "Homebrew, Commercial" (may be "")
    description: str = ""        # markdown, already truncated
    source_url: str = ""         # product page URL, for the citation line


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
    """``64-72°F (18-22°C)`` -> ``18–22 °C (64–72 °F)``.

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
    """Canonical bullet label; fallback title-cases unknown labels."""
    mapped = SPEC_LABELS.get(key)
    if mapped:
        return mapped
    text = " ".join(word.lower() for word in key.split())
    return text[:1].upper() + text[1:]


def _is_bacteria(strain: Strain) -> bool:
    """True when Strain Type (or, failing that, the name) marks bacteria."""
    if _squash(strain.strain_type).casefold() == "bacteria":
        return True
    folded = strain.name.casefold()
    return any(marker in folded for marker in BACTERIA_NAME_MARKERS)


# ---------------------------------------------------------------------------
# JSON enumeration
# ---------------------------------------------------------------------------

def products_from_json(payload: bytes) -> list[dict[str, str]]:
    """``{handle, title, sku}`` dicts from a Shopify collection JSON page.

    Tolerates garbage: an unparseable payload (or one without a product
    list) yields ``[]`` — the collector turns that into a
    :class:`SourceUnavailableError`.
    """
    try:
        data = json.loads(payload.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        return []

    products: list[dict[str, str]] = []
    for item in data["products"]:
        if not isinstance(item, dict):
            continue
        handle = str(item.get("handle") or "").strip()
        if not handle:
            continue
        variants = item.get("variants")
        sku = ""
        if isinstance(variants, list) and variants and isinstance(variants[0], dict):
            sku = str(variants[0].get("sku") or "").strip()
        products.append(
            {
                "handle": handle,
                "title": str(item.get("title") or "").strip(),
                "sku": sku,
            }
        )
    return products


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

class _ProductParser(HTMLParser):
    """Stateful, scoped parser for one Omega product page.

    Captures the first ``<h1>``, the first ``OYL-<digits>`` breadcrumb tag
    (SKU fallback), the text of the first ``div.t-b2-bold`` (the marketing
    description) and every ``dt``/``dd`` pair inside the specs ``<dl>``.
    Scripts, styles, SVGs and templates never contribute text; inside a
    ``dd``, pill ``<span>`` texts are joined with ", " so multi-word tasting
    notes stay readable.
    """

    _SKIP_TAGS = {"script", "style", "svg", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h1 = ""
        self.breadcrumb_sku = ""
        self.description = ""
        self.pairs: list[tuple[str, str]] = []

        self._skip_depth = 0

        self._in_h1 = False
        self._h1_parts: list[str] = []

        self._in_desc = False
        self._desc_depth = 0
        self._desc_parts: list[str] = []

        self._in_dl = False
        self._in_dt = False
        self._dt_parts: list[str] = []
        self._in_dd = False
        self._dd_parts: list[str] = []

        self._in_span = False     # breadcrumb t-tag span
        self._span_parts: list[str] = []

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

        if tag == "h1":
            if not self.h1 and not self._in_h1:
                self._in_h1 = True
                self._h1_parts = []
        elif tag == "div":
            if self._in_desc:
                self._desc_depth += 1
            elif "t-b2-bold" in classes and not self.description:
                self._in_desc = True
                self._desc_depth = 1
                self._desc_parts = []
        elif tag == "dl":
            self._in_dl = True
        elif tag == "dt":
            if self._in_dl:
                self._in_dt = True
                self._dt_parts = []
        elif tag == "dd":
            if self._in_dl:
                self._in_dd = True
                self._dd_parts = []
        elif tag == "br":
            if self._in_dd:
                self._dd_parts.append(" ")
        elif tag == "span":
            if self._in_dd:
                # pill lists: separate spans with ", " (tasting notes)
                if "".join(self._dd_parts).strip():
                    self._dd_parts.append(", ")
                self._in_span = True
                self._span_parts = []
            elif "t-tag" in classes:
                self._in_span = True
                self._span_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag == "h1":
            if self._in_h1:
                self._in_h1 = False
                self.h1 = _squash("".join(self._h1_parts))
        elif tag == "div":
            if self._in_desc:
                self._desc_depth -= 1
                if self._desc_depth <= 0:
                    self._in_desc = False
                    self.description = _squash("".join(self._desc_parts))
        elif tag == "dl":
            self._in_dl = False
            self._in_dt = False
            self._in_dd = False
        elif tag == "dt":
            if self._in_dt:
                self._in_dt = False
        elif tag == "dd":
            if self._in_dd:
                self._in_dd = False
                label = _squash("".join(self._dt_parts))
                value = _squash("".join(self._dd_parts))
                value = re.sub(r"\s+,", ",", value)  # "fruity , tart" -> "fruity, tart"
                if label and value:
                    self.pairs.append((label, value))
                self._dd_parts = []
        elif tag == "span":
            if self._in_span:
                self._in_span = False
                text = _squash("".join(self._span_parts))
                if self._in_dd:
                    self._dd_parts.append(text)
                elif text:
                    match = _SKU_RE.search(text)
                    if match and not self.breadcrumb_sku:
                        self.breadcrumb_sku = match.group(0)
                self._span_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_span:
            self._span_parts.append(data)
        elif self._in_h1:
            self._h1_parts.append(data)
        elif self._in_desc:
            self._desc_parts.append(data)
        elif self._in_dt:
            self._dt_parts.append(data)
        elif self._in_dd:
            self._dd_parts.append(data)


def parse_product(
    html: str,
    *,
    part: str = "",
    source_url: str = "",
) -> Strain | None:
    """Parse one product page; return ``None`` when it is not a strain page.

    A page is rejected when it has no ``<h1>`` or no ``<dl>`` spec pairs
    (non-strain catalog products). Partial strain documents are kept:
    missing tasting notes/specs/availability are simply omitted.
    """
    parser = _ProductParser()
    parser.feed(html)
    parser.close()

    name = _squash(parser.h1)
    if not name or not parser.pairs:
        return None

    specs = dict({_spec_key(label): value for label, value in parser.pairs})
    if "TEMPERATURE RANGE" in specs:
        specs["TEMPERATURE RANGE"] = _normalize_temp(specs["TEMPERATURE RANGE"])

    strain_type = _squash(specs.get("STRAIN TYPE", ""))
    tasting_notes = _squash(specs.get("TASTING NOTES", ""))
    availability = _squash(specs.get("AVAILABILITY", ""))
    description = _truncate(_squash(parser.description), PROFILE_MAX_CHARS)
    part = _squash(part) or parser.breadcrumb_sku

    return Strain(
        name=name,
        part=part,
        strain_type=strain_type,
        tasting_notes=tasting_notes,
        specs=specs,
        availability=availability,
        description=description,
        source_url=source_url,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_strain(strain: Strain) -> str:
    """Render one strain as a compact, one-chunk markdown document.

    Order is retrieval-driven (same reasoning as ``wyeast_collector``):
    name, lab, product, specifications must land inside the embedding
    model's ~1000-char window; the narrative blurb follows.
    """
    title = f"# {strain.part} {strain.name}" if strain.part else f"# {strain.name}"
    lines = [f"{title} (Omega Yeast)", ""]

    if _is_bacteria(strain):
        type_label = "Ingredient (Bacteria culture)"
    else:
        type_label = "Ingredient (Yeast)"
    meta = [f"**Type:** {type_label}", "**Lab:** Omega Yeast"]
    if strain.part:
        meta.append(f"**Product:** {strain.part}")
    if strain.availability:
        meta.append(f"**Availability:** {strain.availability}")
    if strain.source_url:
        meta.append(f"**Source:** {strain.source_url}")
    lines += meta + [""]

    spec_lines = []
    for key in SPEC_ORDER:
        value = _squash(strain.specs.get(key, ""))
        if value:
            spec_lines.append(f"- {SPEC_LABELS[key]}: {value}")
    for key, raw_value in strain.specs.items():
        if key in SPEC_LABELS or key in META_LABELS:
            continue
        value = _squash(raw_value)
        if value:
            spec_lines.append(f"- {_spec_label(key)}: {value}")
    if spec_lines:
        lines += ["## Key Specs", *spec_lines, ""]

    header = "\n".join(lines)
    budget = MAX_DOC_CHARS - len(header) - len("\n## Profile\n") - 1
    budget = max(300, min(PROFILE_MAX_CHARS, budget))
    profile = _truncate(strain.description, budget)
    if profile:
        lines += ["## Profile", profile, ""]

    # no trailing whitespace anywhere; exactly one final newline
    text = "\n".join(line.rstrip() for line in "\n".join(lines).splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"


def strain_filename(strain: Strain) -> str:
    """``yeast-<part>-<name>.md`` (``yeast-oyl-009-west-coast-ii.md``).

    The ``yeast-`` prefix is the ingest contract: ``infer_type()`` classifies
    by filename prefix. Accents and (R)/(TM) marks are ASCII-folded, and the
    part number is removed from the name to avoid duplication.
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
    return "yeast-strain.md"


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
class OmegaCollector(DataCollector):
    id = "omega"
    description = (
        "Omega Yeast strain pages "
        "(attenuation, flocculation, temperature, tasting notes, profile)"
    )

    def collect(self, dest: Path) -> list[Path]:
        # Fetch + parse everything BEFORE clearing dest: a failed run must
        # never wipe previously collected strain files.
        products = self._list_products()
        if not products:
            raise SourceUnavailableError(
                f"omega: no products found at {COLLECTION_URL} "
                "(site restructured or blocking automated access?)"
            )

        strains, failures = self._fetch_strains(products)
        if not strains:
            raise SourceUnavailableError(
                "omega: no strain pages parsed "
                f"({failures} page failures) - site restructured or blocking "
                "automated access"
            )
        failure_limit = max(3, len(products) // 10)  # 10% of URLs, floor 3
        if failures > failure_limit:
            raise SourceUnavailableError(
                f"omega: {failures}/{len(products)} product pages failed "
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

        skipped = len(products) - failures - len(strains)
        print(
            f"omega: {len(rendered)} strains, {skipped} skipped, "
            f"{failures} page failures"
        )
        return write_documents(rendered, dest)

    # -- fetching --------------------------------------------------------
    @staticmethod
    def _list_products() -> list[dict[str, str]]:
        """Walk the collection JSON until a page adds no new handles."""
        products: list[dict[str, str]] = []
        seen: set[str] = set()
        for page in range(1, MAX_LISTING_PAGES + 1):
            url = LISTING_URL.format(page=page)
            try:
                payload = fetch(url)
            except SourceUnavailableError as exc:
                if page == 1:
                    raise  # catalog itself unreachable -> SKIP
                print(f"omega: listing page {page} unavailable ({exc}); stopping")
                break
            new = [p for p in products_from_json(payload) if p["handle"] not in seen]
            if not new:
                break
            for product in new:
                seen.add(product["handle"])
                products.append(product)
                if len(products) >= MAX_PRODUCTS:
                    return products
        return products

    @staticmethod
    def _fetch_strains(products: list[dict[str, str]]) -> tuple[list[Strain], int]:
        """Fetch + parse every product page, tolerating individual failures."""
        strains: list[Strain] = []
        failures = 0
        for product in products:
            url = PRODUCT_URL.format(handle=product["handle"])
            try:
                html = fetch_text(url)
            except SourceUnavailableError as exc:
                print(f"omega: warning: {product['handle']} failed ({exc})")
                failures += 1
                continue
            strain = parse_product(html, part=product["sku"], source_url=url)
            if strain is not None:
                strains.append(strain)
        return strains, failures
