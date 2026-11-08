"""White Labs yeast strain pages -> one markdown document per strain.

Source: the public White Labs yeast bank,
https://www.whitelabs.com/yeast-bank (12 listing pages, ~222 catalog entries).
Each entry links to a detail page
``https://www.whitelabs.com/yeast-single?id=<id>&type=YEAST`` with the strain's
fermentation specs, suggested beer styles and a narrative description.

Structure (verified): the detail page carries

- ``<h1>`` -- the product name (part number optional, e.g. dry Lallemand
  strains have none);
- ``<h2 class="table-title ...">LABEL :</h2><p>value</p>`` pairs for
  ATTENUATION, FLOCCULATION, ALCOHOL TOLERANCE, FERMENTATION TEMPERATURE and
  STA1;
- ``<ul class="details">`` with ``<li><strong>Part No: </strong>...</li>``
  pairs (part number, availability ``Type``, organic flag, STA1 QC);
- ``<h2>Description</h2>`` followed by a ``<div class="dynamic-content">``
  with the marketing/narrative rich HTML; on the live page that div also
  wraps ``ul.details``, and a second ``dynamic-content`` div holds the
  "Suggested Beverages & Styles" tab later on the page;
- ``<ul class="beer_style">`` with one suggested style per ``<li>``.

Data quirks handled:
- the catalog mixes real yeasts with enzymes (``WLE...``), nutrients
  (``WLN...``), sourdough (``WLF...``) and wine/mead/cider/distilling
  strains -- every yeast is kept, only non-yeast products are skipped;
- the live page nests ``ul.details`` *inside* the ``Description``
  ``div.dynamic-content`` (older snapshots put it before), so the parser
  keeps structural parsing active while capturing the description and
  excludes the details/styles lists from the profile text;
- some catalog entries intermittently 404 -- each page failure is tolerated
  and counted, the run only fails soft when the failure rate says the site
  was restructured or is blocking us;
- part numbers never come from the H1 (dry products omit them there) but from
  ``ul.details`` -> ``Part No``;
- fermentation temperature is published as ``64° - 73° F`` / ``18° - 23° C``
  on two ``<br>``-separated lines and is normalized to
  ``18–23 °C (64–73 °F)``.

License note: product data is © White Labs. Pages are fetched once for
personal, local RAG use; rendered files are written to ``collect_dest``
(outside git) and are never committed or redistributed from this repository.

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

from .base import DataCollector, SourceUnavailableError, register
from .web import fetch_text, html_to_markdown

LISTING_URL = "https://www.whitelabs.com/yeast-bank"
STRAIN_URL = "https://www.whitelabs.com/yeast-single?id={strain_id}&type=YEAST"
MAX_LISTING_PAGES = 30          # safety cap; real catalog is 12 pages
PROFILE_MAX_CHARS = 800         # keeps one strain in one ingest chunk
MAX_STYLES = 25
MAX_DOC_CHARS = 1900            # ingest CHUNK_SIZE is 2000

# spec key (upper-case, from the table-title <h2>) -> bullet label
SPEC_LABELS = {
    "ATTENUATION": "Attenuation",
    "FLOCCULATION": "Flocculation",
    "ALCOHOL TOLERANCE": "Alcohol tolerance",
    "FERMENTATION TEMPERATURE": "Recommended temperature",
    "STA1": "STA1",
}
SPEC_ORDER = tuple(SPEC_LABELS)

_WS_RE = re.compile(r"\s+")
_STRAIN_LINK_RE = re.compile(
    r"yeast-single\?id=(\d+)(?:&amp;|&)type=YEAST\b", re.IGNORECASE
)
_TEMP_FC_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*°?\s*-\s*(\d+(?:\.\d+)?)\s*°?\s*F\s+"
    r"(\d+(?:\.\d+)?)\s*°?\s*-\s*(\d+(?:\.\d+)?)\s*°?\s*C",
    re.IGNORECASE,
)
_TEMP_CF_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*°?\s*-\s*(\d+(?:\.\d+)?)\s*°?\s*C\s+"
    r"(\d+(?:\.\d+)?)\s*°?\s*-\s*(\d+(?:\.\d+)?)\s*°?\s*F",
    re.IGNORECASE,
)


@dataclass
class Strain:
    """One White Labs strain page, normalized for rendering."""

    name: str                    # H1 text, trimmed
    part: str = ""               # "WLP001" etc. (may be "")
    specs: dict[str, str] = field(default_factory=dict)  # upper-case label -> value
    availability: str = ""       # details "Type" (Core/Vault/Seasonal), may be ""
    styles: list[str] = field(default_factory=list)  # suggested styles, ordered
    description: str = ""        # markdown, already trimmed/truncated
    source_url: str = ""         # strain page URL, for the citation line


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
    """``64° - 73° F 18° - 23° C`` -> ``18–23 °C (64–73 °F)``.

    Falls back to the whitespace-squashed raw text when the pattern does not
    match, so unusual labels survive unchanged.
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


def strain_links(html: str) -> list[str]:
    """Unique strain ids from a listing page, in document order.

    Matches only ``yeast-single?id=<digits>&(amp;)type=YEAST``; navigation,
    compare links and other product types are ignored.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for strain_id in _STRAIN_LINK_RE.findall(html):
        if strain_id not in seen:
            seen.add(strain_id)
            ids.append(strain_id)
    return ids


class _StrainParser(HTMLParser):
    """Stateful parser for one White Labs strain detail page.

    Captures the H1, table-title spec pairs, ``ul.details`` fields,
    ``ul.beer_style`` entries and the inner HTML of the ``dynamic-content``
    div that follows the ``Description`` heading. Scripts, styles, SVGs and
    images never contribute text.
    """

    _SKIP_TAGS = {"script", "style", "svg", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h1_parts: list[str] = []
        self.specs: dict[str, str] = {}
        self.details: dict[str, str] = {}
        self.styles: list[str] = []
        self.description_html: str = ""

        self._skip_depth = 0
        self._in_h1 = False

        # <h2 class="table-title">LABEL</h2><p>value</p>
        self._in_h2 = False
        self._h2_classes: set[str] = set()
        self._h2_parts: list[str] = []
        self._pending_spec: str | None = None
        self._in_p = False
        self._p_parts: list[str] = []

        # <ul> context stack (class sets) for details / beer_style lists
        self._ul_stack: list[set[str]] = []

        # <ul class="details"> -> <li><strong>label</strong>value</li>
        self._in_detail_li = False
        self._li_label_parts: list[str] = []
        self._li_value_parts: list[str] = []
        self._strong_depth = 0

        # <ul class="beer_style"> -> <li>style</li>
        self._in_style_li = False
        self._style_parts: list[str] = []

        # description: inner HTML of div.dynamic-content after the h2
        self._await_description = False
        self._capturing_desc = False
        self._desc_depth = 0
        self._desc_exclude_depth = 0  # nested ul.details / ul.beer_style
        self._desc_parts: list[str] = []

    # -- helpers ---------------------------------------------------------
    def _in_list(self, class_name: str) -> bool:
        return any(class_name in classes for classes in self._ul_stack)

    @staticmethod
    def _classes(attrs: list) -> set[str]:
        return set((dict(attrs).get("class") or "").split())

    def _raw_start_tag(self, tag: str) -> str:
        return self.get_starttag_text() or f"<{tag}>"

    # -- HTMLParser hooks ------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        classes = self._classes(attrs)

        # a table-title heading with no <p> must not capture a later <p>
        if self._pending_spec is not None and not self._in_p and tag not in ("p", "br"):
            self._pending_spec = None

        # Append raw description HTML, but never the details/styles lists:
        # on the live site div.dynamic-content wraps ul.details too.
        if self._capturing_desc:
            if self._desc_exclude_depth:
                if tag == "ul":
                    self._desc_exclude_depth += 1
            elif tag == "ul" and classes & {"details", "beer_style"}:
                self._desc_exclude_depth = 1
            else:
                self._desc_parts.append(self._raw_start_tag(tag))
                if tag == "div":
                    self._desc_depth += 1

        # structural bookkeeping continues even inside the description
        if tag == "h1":
            self._in_h1 = True
            self.h1_parts = []
        elif tag == "h2":
            self._in_h2 = True
            self._h2_classes = classes
            self._h2_parts = []
        elif tag == "p":
            if self._pending_spec is not None and not self._in_p:
                self._in_p = True
                self._p_parts = []
        elif tag == "br":
            if self._in_p:
                self._p_parts.append(" ")
            if self._in_detail_li:
                self._li_value_parts.append(" ")
            if self._in_style_li:
                self._style_parts.append(" ")
        elif tag == "ul":
            self._ul_stack.append(classes)
        elif tag == "li":
            if self._in_list("details"):
                self._in_detail_li = True
                self._li_label_parts = []
                self._li_value_parts = []
                self._strong_depth = 0
            elif self._in_list("beer_style"):
                self._in_style_li = True
                self._style_parts = []
        elif tag == "strong":
            if self._in_detail_li:
                self._strong_depth += 1
        elif tag == "div":
            if self._await_description and "dynamic-content" in classes:
                self._capturing_desc = True
                self._desc_depth = 1
                self._desc_parts = [self._raw_start_tag(tag)]
                self._await_description = False

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if self._capturing_desc:
            if self._desc_exclude_depth:
                if tag == "ul":
                    self._desc_exclude_depth -= 1
            elif tag == "div":
                self._desc_depth -= 1
                if self._desc_depth <= 0:
                    self._capturing_desc = False
                    self.description_html = "".join(self._desc_parts).strip()
                else:
                    self._desc_parts.append("</div>")
            else:
                self._desc_parts.append(f"</{tag}>")

        # structural bookkeeping continues even inside the description
        if tag == "h1":
            self._in_h1 = False
        elif tag == "h2":
            self._in_h2 = False
            label = _squash("".join(self._h2_parts)).rstrip(":").strip().upper()
            if "table-title" in self._h2_classes and label and not self._capturing_desc:
                self._pending_spec = label
            elif label == "DESCRIPTION":
                self._await_description = True
        elif tag == "p":
            if self._in_p:
                self._in_p = False
                if self._pending_spec is not None:
                    value = _squash("".join(self._p_parts))
                    if value:
                        self.specs[self._pending_spec] = value
                    self._pending_spec = None
                self._p_parts = []
        elif tag == "ul":
            if self._ul_stack:
                self._ul_stack.pop()
        elif tag == "li":
            if self._in_detail_li:
                label = _squash("".join(self._li_label_parts)).rstrip(":").strip()
                value = _squash("".join(self._li_value_parts))
                if label:
                    self.details[label] = value
                self._in_detail_li = False
                self._strong_depth = 0
            elif self._in_style_li:
                style = _squash("".join(self._style_parts))
                if style:
                    self.styles.append(style)
                self._in_style_li = False
        elif tag == "strong":
            if self._in_detail_li and self._strong_depth:
                self._strong_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._capturing_desc and not self._desc_exclude_depth:
            self._desc_parts.append(data)
        elif self._in_h1:
            self.h1_parts.append(data)
        elif self._in_h2:
            self._h2_parts.append(data)
        elif self._in_p:
            self._p_parts.append(data)
        elif self._in_detail_li:
            if self._strong_depth:
                self._li_label_parts.append(data)
            else:
                self._li_value_parts.append(data)
        elif self._in_style_li:
            self._style_parts.append(data)


def parse_strain(html: str, *, source_url: str = "") -> Strain | None:
    """Parse one detail page; return ``None`` when it is not a strain page.

    A page is rejected when it has no ``<h1>`` (or the H1 is ``404``) or when
    it has neither fermentation specs nor suggested styles -- enzymes,
    nutrients, sourdough starters and other supplies are catalog products,
    not yeast strains. Partial strain documents are kept.
    """
    parser = _StrainParser()
    parser.feed(html)
    parser.close()

    name = _squash("".join(parser.h1_parts))
    if not name or name.upper().startswith("404"):
        return None

    specs = dict(parser.specs)
    if "FERMENTATION TEMPERATURE" in specs:
        specs["FERMENTATION TEMPERATURE"] = _normalize_temp(
            specs["FERMENTATION TEMPERATURE"]
        )

    styles = _dedupe(
        [style for style in (_squash(s) for s in parser.styles) if style]
    )
    if not (specs.get("ATTENUATION") or specs.get("FERMENTATION TEMPERATURE") or styles):
        return None

    description = ""
    if parser.description_html:
        description = _truncate(
            html_to_markdown(parser.description_html).replace("\xa0", " "),
            PROFILE_MAX_CHARS,
        )

    return Strain(
        name=name,
        part=_squash(parser.details.get("Part No", "")),
        specs=specs,
        availability=_squash(parser.details.get("Type", "")),
        styles=styles,
        description=description,
        source_url=source_url,
    )


def render_strain(strain: Strain) -> str:
    """Render one strain as a compact, one-chunk markdown document.

    Order is retrieval-driven (same reasoning as ``bjcp_collector``): name,
    lab, product, specs and suggested style names must land inside the
    embedding model's ~1000-char window; the narrative profile follows.
    """
    lines = [f"# {strain.name} (White Labs)", ""]

    meta = ["**Type:** Ingredient (Yeast)", "**Lab:** White Labs"]
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
    if spec_lines:
        lines += ["## Key Specs", *spec_lines, ""]

    styles = strain.styles[:MAX_STYLES]
    if styles:
        lines += ["## Suggested Styles", ", ".join(styles), ""]

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
    """``yeast-<part>-<name>.md`` (``yeast-[wlp001]-california-ale-yeast.md``).

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
    return f"yeast-{name_slug or 'strain'}.md"


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
class WhitelabsCollector(DataCollector):
    id = "whitelabs"
    description = "White Labs yeast strain pages (attenuation, flocculation, styles, profile)"

    def collect(self, dest: Path) -> list[Path]:
        # Fetch + parse everything BEFORE clearing dest: a failed run must
        # never wipe previously collected strain files.
        strain_ids = self._list_strain_ids()
        if not strain_ids:
            raise SourceUnavailableError(
                f"whitelabs: no strain links found on {LISTING_URL} "
                "(site restructured?)"
            )

        strains, failures = self._fetch_strains(strain_ids)
        if not strains:
            raise SourceUnavailableError(
                "whitelabs: no strain pages parsed "
                f"({failures} page failures) - site restructured or blocking "
                "automated access"
            )
        failure_limit = max(3, len(strain_ids) // 10)  # 10% of links, floor 3
        if failures > failure_limit:
            raise SourceUnavailableError(
                f"whitelabs: {failures}/{len(strain_ids)} strain pages failed "
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

        skipped = len(strain_ids) - failures - len(strains)
        print(
            f"whitelabs: {len(rendered)} strains, {skipped} skipped, "
            f"{failures} page failures"
        )
        return write_documents(rendered, dest)

    # -- fetching --------------------------------------------------------
    @staticmethod
    def _list_strain_ids() -> list[str]:
        """Walk the yeast-bank listing until a page adds no new strain ids."""
        ids: list[str] = []
        seen: set[str] = set()
        for page in range(1, MAX_LISTING_PAGES + 1):
            url = LISTING_URL if page == 1 else f"{LISTING_URL}?page={page}"
            try:
                html = fetch_text(url)
            except SourceUnavailableError as exc:
                if page == 1:
                    raise  # catalog itself unreachable -> SKIP
                print(f"whitelabs: listing page {page} unavailable ({exc}); stopping")
                break
            new_ids = [sid for sid in strain_links(html) if sid not in seen]
            if not new_ids:
                break
            for strain_id in new_ids:
                seen.add(strain_id)
                ids.append(strain_id)
        return ids

    @staticmethod
    def _fetch_strains(strain_ids: list[str]) -> tuple[list[Strain], int]:
        """Fetch + parse every detail page, tolerating individual failures."""
        strains: list[Strain] = []
        failures = 0
        for strain_id in strain_ids:
            url = STRAIN_URL.format(strain_id=strain_id)
            try:
                html = fetch_text(url)
            except SourceUnavailableError as exc:
                print(f"whitelabs: warning: strain {strain_id} failed ({exc})")
                failures += 1
                continue
            strain = parse_strain(html, source_url=url)
            if strain is not None:
                strains.append(strain)
        return strains, failures
