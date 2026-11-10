"""Hop substitution charts (4 sources) -> one markdown document per hop.

Sources: four public hop substitution reference tables, merged into a single
name-normalized document per hop variety:

* ``https://www.geterbrewed.com/hops/hop-substitution-list/`` -- 216-row
  finder table (Hop / Alpha Acid / Type / Substitutes).
* ``https://help.grainfather.com/hc/en-us/articles/360012729398``
  -- 5-column Zendesk table (Hop / Aroma-Flavour / Alpha / Type / Substitutes).
* ``https://help.mangrovejacks.com/hc/en-us/articles/360005455558`` -- same
  table content as Grainfather (parsed independently so identical rows
  dedupe through the normal merge path instead of being assumed equal).
* ``https://homebrewersassociation.org/how-to-brew/hop-substitutions/`` --
  five same-header tables of ``Hop Variety / Possible Hop Substitution``
  rows; only ~110 varieties but not a subset (e.g. ``Citra -> Galaxy``
  appears only here) and the only source carrying origin country tags.

Merging is union semantics: substitutes are pooled across sources, nothing
is derived, and each hop document cites the tables it was agreed by. The
AHA origin codes (``(GBR)``, ``(DEU)``, ...) are descriptive country labels
(its legend says so) and fold into the Origin line; the charts' own
suffixed rows (``Northern Brewer, USA``) stay distinct varieties.

Rows that name hop *products*, not varieties, are excluded everywhere --
their references in other rows' substitute lists included: ``..., CRVO``,
``CT90``, ``Hop Extract``, ``Hop Hash`` (high-alpha extracts from
processors; not substitution targets -- excluded by collector decision).

Known upstream typos ("Amarill", "Heull Melon", "Syrian Goldings", ...) are
repaired by an explicit alias map; anything unrecognized is kept verbatim
and printed for review -- a wrong-name fix never fails a run, an
unrecognized substitute never silently disappears.

License note: chart data is (c) the publishers above. Pages are fetched
once per run for personal, local RAG use; rendered files are written to
``collect_dest`` (outside git) and are never committed or redistributed.

Output contract (collectors/base.py): ``dest/ingredients/hop-<slug>.md``
(ingest.infer_type() classifies by the ``hop-`` filename prefix).
"""
from __future__ import annotations

import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .base import DataCollector, SourceUnavailableError, register
from .web import fetch

# ----------------------------------------------------------------- sources

S_GETERBREWED = "Geterbrewed hop substitution list"
S_GRAINFATHER = "Grainfather Hop Substitution Chart"
S_MANGROVE = "Mangrove Jack's Hop Substitution Chart"
S_AHA = "American Homebrewers Association hop substitutions"

SOURCES = (
    (S_GETERBREWED, "https://www.geterbrewed.com/hops/hop-substitution-list/"),
    (
        S_GRAINFATHER,
        "https://help.grainfather.com/hc/en-us/articles/"
        "360012729398-Hop-Substitution-Chart",
    ),
    (
        S_MANGROVE,
        "https://help.mangrovejacks.com/hc/en-us/articles/"
        "360005455558-Hop-Substitution-Chart",
    ),
    (S_AHA, "https://homebrewersassociation.org/how-to-brew/hop-substitutions/"),
)
_SOURCE_ORDER = {label: index for index, (label, _) in enumerate(SOURCES)}

# Sanity floors: real counts are 216 / ~215 / ~215 / ~110 rows and ~200
# merged varieties. Floors sit well below reality so small upstream edits
# do not break runs, but a restructured page (different chart, most rows
# gone) fails soft instead of indexing a fragment of the reference data.
MIN_CHART_ROWS = 170    # per 5-column chart (Grainfather / Mangrove Jack's)
MIN_FINDER_ROWS = 180   # geterbrewed finder table
MIN_AHA_ROWS = 80       # AHA pipe tables across all 5 tables (real: 88 rows)
MIN_MERGED = 180        # merged unique varieties before rendering

# ------------------------------------------------------------- rendering

MAX_DOC_CHARS = 1900    # ingest CHUNK_SIZE is 2000
MAX_SUBSTITUTES = 12    # longest real list is 8; headroom for union growth
MAX_AROMA = 10          # longest real list is 9

# AHA origin codes -> display names (the codes AHA documents in its legend)
COUNTRY_LABELS = {
    "AUS": "Australia",
    "CZE": "Czech Republic",
    "DEU": "Germany",
    "FRA": "France",
    "GBR": "Great Britain",
    "NZL": "New Zealand",
    "POL": "Poland",
    "SVN": "Slovenia",
    "USA": "United States",
}

# alias map over name *keys* (lowercase alphanumerics, punctuation dropped)
ALIASES = {
    # Chart-internal suffix spellings -- the charts define the equivalence:
    # "Fuggle" is the UK row, "Fuggle, USA" the US row; refs spell "(US)/(UK)".
    "fuggleuk": "fuggle",
    "fuggleus": "fuggleusa",
    "fuggles": "fuggle",
    "perleusa": "perleus",          # "Perle, USA" (geter) vs "Perle, US" (charts)
    "tettnangusa": "tettnangus",    # "Tettnang, USA" (geter) vs "Tettnang US"
    "tettnanger": "tettnang",       # German Tettnanger is the Tettnang row
    "tettnangergr": "tettnang",
    "tettnangerger": "tettnang",
    "tettnager": "tettnang",
    "tettnag": "tettnang",
    "tettnangerus": "tettnangus",
    "tettnangerusa": "tettnangus",
    "northernbrewerus": "northernbrewerusa",
    "northernbrewergr": "northernbrewerger",
    "northernbrewergerman": "northernbrewerger",
    "hallertaugr": "hallertau",
    "hallertauger": "hallertau",
    "magnum": "hallertaumagnum",    # "Magnum" is Hallertau Magnum's short name
    "magnumgr": "hallertaumagnum",
    "magnumus": "hallertaumagnum",
    "taurus": "hallertautaurus",
    "tradition": "hallertautradition",
    "hallertaumittelfruh": "hallertauermittelfruh",
    "hallertauermagnum": "hallertaumagnum",
    "hallertauertaurus": "hallertautaurus",
    "hallertauertradition": "hallertautradition",
    "select": "spalterselect",      # AHA "Select (DEU)" is the Select variety
    "strisselpaltfra": "strisselspalt",
    "strisselspalter": "strisselspalt",
    "falconersflight7cs": "falconersflight7csblend",
    "goldinguk": "goldingsuk",
    "goldingusa": "goldingsusa",
    "goldingamerican": "goldingsusa",
    "bobek": "styrianbobek",
    "celeia": "styrianceleia",
    "lublin": "lublinlubelski",
    "lubelski": "lublinlubelski",
    "lubin": "lublinlubelski",
    # refs spelled without or with slightly different separators
    "saazczech": "saaz",
    "cascadeus": "cascade",
    "cascadeusa": "cascade",
    "columbustomahawkctc": "columbus",
    "ekuanot": "ekuanotequinox",
    "eukanot": "ekuanotequinox",
    "ekuanotformerlyequinox": "ekuanotequinox",
    # upstream typos for which the correct spelling exists as its own row
    "amarill": "amarillo",
    "heullmelon": "huellmelon",
    "huelmelon": "huellmelon",
    "huelmellon": "huellmelon",
    "williamette": "willamette",
    "strisselpalt": "strisselspalt",
    "hersbruker": "hersbrucker",
    "hersbrukergbr": "hersbruckergbr",
    "hersbrukerdeu": "hersbruckerdeu",
    "syriangoldings": "styriangolding",
    "styriangoldings": "styriangolding",
    "syrianaurora": "styrianaurora",
    "brambling": "bramlingcross",
    "bramblingcross": "bramlingcross",
    "nuget": "nugget",
    "pilgram": "pilgrim",
    "southernpromis": "southernpromise",
    "millenium": "millennium",
    "mounthood": "mthood",
}

# Explicit display headings for families where voting/length disagree with
# the correct modern spelling (e.g. "Eukanot(R)" is shorter than "Ekuanot
# (Equinox)" but that is the chart-variant name, not the display we want).
CANONICAL_DISPLAYS = {
    "ekuanotequinox": "Ekuanot (Equinox)",
}

# rows whose name marks them as products, not varieties (excluded by decision)
_PRODUCT_MARKER_RE = re.compile(r"hop\s+extract|hop\s+hash|crvo|ct90", re.IGNORECASE)


@dataclass
class HopRecord:
    """One raw row from one source chart, before name normalization."""

    name: str = ""
    aroma: list[str] = field(default_factory=list)
    alpha: str = ""
    purpose: str = ""
    substitutes: list[str] = field(default_factory=list)
    origin: str = ""          # AHA country display label, may be ""
    source: str = ""          # SOURCE_* label of the row's chart


@dataclass
class HopEntry:
    """One merged hop variety, ready for rendering (one doc per chunk)."""

    name: str = ""
    aroma: list[str] = field(default_factory=list)
    alpha: str = ""
    purpose: str = ""
    origin: str = ""
    substitutes: list[str] = field(default_factory=list)
    sub_keys: list[str] = field(default_factory=list)  # identity keys, parallel to substitutes
    sources: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers (same conventions as the other collectors)
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def _squash(text: str) -> str:
    """Collapse all whitespace (incl. ``&nbsp;``) into single spaces."""
    return _WS_RE.sub(" ", text.replace("\xa0", " ")).strip()


def _name_key(text: str) -> str:
    """Identity key for a hop spelling: lowercase ASCII, punctuation-free.

    Umlauts fold to plain letters (``Mittelfrüh`` -> ``mittelfruh``) and all
    punctuation drops (``Brewer's Gold, UK`` == ``Brewers gold UK``), so
    upstream spelling variance collapses before alias resolution.
    """
    text = text.replace("®", "").replace("™", "")  # NFKD would expand (R)/(TM)
    folded = unicodedata.normalize("NFKD", text)
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def _alias_key(text: str) -> str | None:
    """Canonical key for a name, or ``None`` for product rows (excluded)."""
    key = _name_key(text)
    if not key or _PRODUCT_MARKER_RE.search(text):
        return None
    return ALIASES.get(key, key)


def _alpha_clean(raw: str) -> str:
    """Tidy one published alpha string without interpreting it.

    ``13%-16.2%`` stays as published; stray dots directly before ``%`` go
    (``5.%-6.5%`` -> ``5%-6.5%``, ``15.5.%`` -> ``15.5%``) and doubled
    ``%%`` collapse to ``%``.
    """
    text = _squash(raw).replace("%%", "%")
    return re.sub(r"\.+(?=%)", "", text) or _squash(raw)


def _is_product(text: str) -> bool:
    """True for extract/hash/CRVO/CT90 rows and substitute references."""
    return bool(_PRODUCT_MARKER_RE.search(text))


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


def _capped(items: list[str], limit: int) -> str:
    """Comma-separated list capped at ``limit`` items, with ``, …`` when cut."""
    text = ", ".join(items[:limit])
    if len(items) > limit:
        text += ", …"
    return text


def _slug(text: str) -> str:
    """ASCII filename slug: fold accents, lowercase, '-' runs."""
    text = text.replace("®", "").replace("™", "")
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


def _finish(lines: list[str]) -> str:
    """Output hygiene: no trailing whitespace, max one blank line, one newline."""
    text = "\n".join(line.rstrip() for line in "\n".join(lines).splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"


# ---------------------------------------------------------------------------
# HTML tables -> rows of cells of text lines (stdlib HTMLParser)
# ---------------------------------------------------------------------------

class _TableCellParser(HTMLParser):
    """Scoped table walker: outermost tables -> rows -> cells -> text lines.

    Each cell is a list of text lines: ``<li>``, ``<p>``, ``<br>`` and
    headings start new lines; everything else within a cell accumulates into
    one line, so Geterbrewed's ``·``-chained substitute spans stay a single
    splittable line and AHA's ``|``-separated row text stays one line.
    Scripts, styles, SVGs and templates never contribute; only outermost
    table rows are collected (nested tables ignored).
    """

    _SKIP_TAGS = {"script", "style", "svg", "noscript", "template", "head"}
    _LINE_BREAKS = {"li", "p", "br", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # tables -> rows -> cells -> lines
        self.tables: list[list[list[list[str]]]] = []
        # parallel structure: tables -> rows -> cells -> had <strong>/<b>
        self.bolds: list[list[list[bool]]] = []
        self._skip_depth = 0
        self._table_depth = 0
        self._row: list[list[str]] | None = None
        self._cell: list[str] | None = None
        self._buf: list[str] = []
        self._bolds: list[bool] | None = None
        self._cell_bold = False

    def _flush_line(self) -> None:
        """Emit accumulated pieces as one (possibly empty) cell line."""
        line = _squash("".join(self._buf))
        self._buf = []
        if self._cell is not None:
            self._cell.append(line)

    # -- HTMLParser hooks ------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self.tables.append([])
                self.bolds.append([])
                self._row = None
                self._cell = None
            return
        if not self._table_depth:
            return
        if tag == "tr":
            if self._table_depth == 1 and self._row is None:
                self._row = []
                self._bolds = []
        elif tag in ("td", "th"):
            if self._row is not None and self._cell is None:
                self._cell = []
                self._cell_bold = False
                self._row.append(self._cell)
                self._bolds.append(False)
        elif tag in ("strong", "b"):
            self._cell_bold = True
            if self._bolds and self._cell is not None and self._bolds:
                self._bolds[-1] = True
        elif tag in self._LINE_BREAKS:
            self._flush_line()

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in self._LINE_BREAKS:
            self._flush_line()
        elif tag in ("td", "th"):
            self._flush_line()
            self._cell = None
        elif tag == "tr":
            self._flush_line()
            if self._row is not None and self.tables:
                self.tables[-1].append(self._row)
                self.bolds[-1].append(self._bolds)
            self._row = None
            self._bolds = None
        elif tag == "table":
            self._flush_line()
            if self._table_depth:
                self._table_depth -= 1
            if self._table_depth == 0:
                self._row = None
                self._cell = None
                self._bolds = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._table_depth or self._cell is None:
            return
        self._buf.append(data)


def parse_tables(html: str) -> list[list[list[list[str]]]]:
    """Every outermost HTML table as rows of cells of text lines."""
    parser = _TableCellParser()
    parser.feed(html)
    parser.close()
    return parser.tables


def parse_tables_bolds(html: str) -> tuple[list, list]:
    """Same as :func:`parse_tables`, plus the parallel ``<strong>`` flags."""
    parser = _TableCellParser()
    parser.feed(html)
    parser.close()
    return parser.tables, parser.bolds


# ---------------------------------------------------------------------------
# Source parsers: HTML -> list[HopRecord] (pure, offline, fail-soft)
# ---------------------------------------------------------------------------

_HEADER_AROMA = "aroma/flavour"
_HEADER_ALPHA = "alpha"
_HEADER_ALPHA_ACID = "alpha acid"
_HEADER_TYPE = "type (bittering/aroma)"
_HEADER_TYPE_BARE = "type"
_HEADER_SUBS = "substitutes"
_HEADER_VARIETY = "hop variety"
_HEADER_AHA_SUBS = "possible hop substitution"


def _squash_cell(lines: list[str]) -> str:
    """One cell's lines joined into a single squashed text."""
    return _squash(" ".join(lines))


def _header_map(row: list[list[str]]) -> dict[str, int]:
    """Column index per squashed header text of a table's first row."""
    mapping: dict[str, int] = {}
    for index, cell in enumerate(row):
        heading = _squash_cell(cell).casefold().rstrip(":")
        if heading and heading not in mapping:
            mapping[heading] = index
    return mapping


def _purpose_clean(raw: str) -> str:
    """Canonical purpose label; unknown values pass through squashed."""
    folded = _squash(raw).casefold()
    return {
        "bittering": "Bittering",
        "aroma": "Aroma",
        "dual purpose": "Dual Purpose",
    }.get(folded, _squash(raw))


def _aha_name_origin(raw: str) -> tuple[str, str]:
    """``Admiral (GBR)`` -> ``("Admiral", "Great Britain")``.

    AHA's codes are descriptive origin labels (documented in the article's
    legend); unknown uppercase triples stay part of the name.
    """
    text = _squash(raw)
    match = re.search(r"\(([A-Z]{3})\)\s*$", text)
    if not match:
        return text, ""
    return text[: match.start()].strip(), COUNTRY_LABELS.get(match.group(1), "")


def _aha_ref(raw: str) -> str:
    """AHA substitute text with the origin-code label removed.

    AHA writes substitute names exactly like row names (``Northdown (GBR)``);
    the code is a country label, not part of the variety identity.
    """
    return _aha_name_origin(raw)[0]


def _chart_table(header: dict[str, int], *, want_aroma: bool) -> bool:
    """Does this header map belong to the chart schema being parsed?"""
    if "hop" not in header or _HEADER_SUBS not in header:
        return False
    has_aroma = _HEADER_AROMA in header
    return has_aroma == want_aroma


def parse_chart(html: str, source: str) -> list[HopRecord]:
    """5-column chart parser (Grainfather / Mangrove Jack's).

    Accepts tables whose header maps Hop / Aroma-Flavour / Alpha /
    Type (Bittering:Aroma) / Substitutes. Substitutes arrive as one cell
    line per item (each an ``<li>``). Product rows are dropped later by
    :func:`merge_records` -- the parser keeps raw rows for reporting.
    """
    records: list[HopRecord] = []
    for table in parse_tables(html):
        if not table:
            continue
        header = _header_map(table[0])
        if not _chart_table(header, want_aroma=True):
            continue
        name_idx = header["hop"]
        aroma_idx = header[_HEADER_AROMA]
        subs_idx = header[_HEADER_SUBS]
        alpha_idx = header.get(_HEADER_ALPHA, header.get(_HEADER_ALPHA_ACID, -1))
        type_idx = header.get(_HEADER_TYPE, header.get(_HEADER_TYPE_BARE, -1))
        last_idx = max(name_idx, aroma_idx, subs_idx, alpha_idx, type_idx)
        for row in table[1:]:
            if len(row) <= last_idx:
                continue
            name = _squash(" ".join(row[name_idx]))
            if not name:
                continue

            def cell(index: int) -> str:
                return _squash(" ".join(row[index])) if index >= 0 else ""

            records.append(
                HopRecord(
                    name=name,
                    aroma=[line for line in row[aroma_idx] if line],
                    alpha=_alpha_clean(cell(alpha_idx)),
                    purpose=_purpose_clean(cell(type_idx)),
                    substitutes=[line for line in row[subs_idx] if line],
                    source=source,
                )
            )
    return records


def parse_finder(html: str, source: str) -> list[HopRecord]:
    """4-column finder parser (Geterbrewed): Hop / Alpha Acid / Type / Subs.

    The substitutes cell is one line of ``·``-chained spans, split on ``·``.
    The finder publishes no aroma column.
    """
    records: list[HopRecord] = []
    for table in parse_tables(html):
        if not table:
            continue
        header = _header_map(table[0])
        if not _chart_table(header, want_aroma=False) or _HEADER_ALPHA_ACID not in header:
            continue
        name_idx = header["hop"]
        subs_idx = header[_HEADER_SUBS]
        alpha_idx = header[_HEADER_ALPHA_ACID]
        type_idx = header.get(_HEADER_TYPE_BARE, -1)
        last_idx = max(name_idx, subs_idx, alpha_idx, type_idx)
        for row in table[1:]:
            if len(row) <= last_idx:
                continue
            name = _squash(" ".join(row[name_idx]))
            if not name:
                continue

            def cell(index: int) -> str:
                return _squash(" ".join(row[index])) if index >= 0 else ""

            subs = [
                _squash(piece)
                for line in row[subs_idx]
                for piece in line.split("·")
                if _squash(piece)
            ]
            records.append(
                HopRecord(
                    name=name,
                    alpha=_alpha_clean(cell(alpha_idx)),
                    purpose=_purpose_clean(cell(type_idx)),
                    substitutes=subs,
                    source=source,
                )
            )
    return records


def parse_aha(html: str, source: str) -> list[HopRecord]:
    """AHA parser: tables headed "Hop Variety / Possible Hop Substitution".

    The page splits the variety list across five same-schema tables
    (screenshots and call-outs sit between them). Only the first one keeps
    its header row; the rest start directly with data, so tables without the
    header are parsed with the documented layout: column 0 = variety, column
    1 = ``|``-separated substitutes. Rows carry no aroma/alpha/purpose --
    only substitutes and origin codes.
    """
    records: list[HopRecord] = []
    tables, bolds = parse_tables_bolds(html)
    for table, table_bolds in zip(tables, bolds):
        if not table:
            continue
        header = _header_map(table[0])
        if _HEADER_VARIETY in header and _HEADER_AHA_SUBS in header:
            name_idx = header[_HEADER_VARIETY]
            subs_idx = header[_HEADER_AHA_SUBS]
            data_rows = list(zip(table[1:], table_bolds[1:]))
        else:
            name_idx, subs_idx = 0, 1
            # Headerless continuation tables: AHA bolds every variety name,
            # so bold-less rows (page chrome, stray tables) are skipped.
            data_rows = list(zip(table, table_bolds))
        for row, row_bolds in data_rows:
            if len(row) <= max(name_idx, subs_idx):
                continue
            if not (row_bolds and row_bolds[name_idx]):
                # AHA bolds every variety name, including in the headerless
                # continuation tables; bold-less rows are page chrome.
                continue
            raw_name = _squash(" ".join(row[name_idx]))
            if not raw_name:
                continue
            name, origin = _aha_name_origin(raw_name)
            subs = [
                ref
                for line in row[subs_idx]
                for piece in line.split("|")
                for ref in [_aha_ref(piece)]
                if ref
            ]
            records.append(
                HopRecord(
                    name=name,
                    substitutes=subs,
                    origin=origin,
                    source=source,
                )
            )
    return records


# ---------------------------------------------------------------------------
# Merge: union by canonical identity key
# ---------------------------------------------------------------------------

def _merge_display(candidates: list[tuple[str, set]]) -> str:
    """Cleanest display spelling for one merged variety.

    Votes by source count (a spelling seen in 3 charts beats an AHA-only
    label) -- except when the shortest spelling is a word-boundary prefix
    of every other spelling (``Cascade`` is the common name of the family
    spelled ``Cascade, USA`` in the charts). Remaining ties: shortest, then
    first-seen order for full determinism.
    """
    names = [c[0] for c in candidates]

    def is_boundary_prefix(short: str, long: str) -> bool:
        """``Cascade`` vs ``Cascade, USA`` -- prefix then ',', '.', '-', ' '."""
        if not long.startswith(short) or len(short) == len(long):
            return False
        return long[len(short)] in ",. -"

    shortest = min(names, key=len)
    if all(is_boundary_prefix(shortest, name) for name in names if name != shortest):
        return shortest

    best = candidates[0]
    for candidate in candidates[1:]:
        if len(candidate[1]) > len(best[1]):
            best = candidate
            continue
        if len(candidate[1]) < len(best[1]):
            continue
        if len(candidate[0]) < len(best[0]):
            best = candidate
    return best[0]


def merge_records(records: list[HopRecord]) -> tuple[list[HopEntry], list[str]]:
    """Union raw records onto merged, name-normalized entries.

    Returns ``(entries, unresolved_substitute_names)``. Entries sort
    alphabetically by canonical name for deterministic output. Product rows
    drop everywhere (references included); self-references resolve out;
    substitute spellings that match no row are kept verbatim and reported
    (fixing one later is an ALIASES entry, never a silent data loss).
    """
    # 1st pass: identity registry -- alias-resolved key -> display vote counts
    key_displays: dict[str, dict[str, tuple[str, set[str]]]] = {}
    for record in records:
        if _is_product(record.name):
            continue
        key = _alias_key(record.name)
        if not key:
            continue
        # trademark marks never belong in a display heading
        display = _squash(record.name.replace("®", "").replace("™", ""))
        votes = key_displays.setdefault(key, {})
        existing = votes.get(display.casefold())
        if existing:
            existing[1].add(record.source)
        else:
            votes[display.casefold()] = (display, {record.source})

    def display_for(key: str) -> str:
        override = CANONICAL_DISPLAYS.get(key)
        if override:
            return override
        return _merge_display(list(key_displays[key].values()))
    display_by_key = {k: display_for(k) for k in key_displays}

    # 2nd pass: union facts per key, in record order (deterministic)
    entries: dict[str, HopEntry] = {}
    unresolved: set[str] = set()
    for record in records:
        key = _alias_key(record.name)
        if not key:
            continue
        entry = entries.setdefault(
            key, HopEntry(name=display_by_key.get(key, _squash(record.name)))
        )
        if record.source not in entry.sources:
            entry.sources.append(record.source)
        if not entry.alpha and record.alpha:
            entry.alpha = record.alpha
        if not entry.purpose and record.purpose:
            entry.purpose = record.purpose
        if not entry.origin and record.origin:
            entry.origin = record.origin
        if record.aroma:
            entry.aroma = _dedupe(
                entry.aroma + [_squash(item) for item in record.aroma if item]
            )
        if record.substitutes:
            seen = set(entry.sub_keys)
            substitutes = list(entry.substitutes)
            sub_keys = list(entry.sub_keys)
            for raw in record.substitutes:
                sub = _squash(raw)
                if not sub or _is_product(sub):
                    continue
                sub_key = _alias_key(sub)
                if sub_key == key:
                    continue
                resolved = display_by_key.get(sub_key)
                if resolved is None:
                    resolved = sub
                    if sub.casefold() not in {r.casefold() for r in substitutes}:
                        unresolved.add(sub)
                if sub_key in seen:
                    continue
                seen.add(sub_key)
                sub_keys.append(sub_key)
                substitutes.append(resolved)
            entry.substitutes = substitutes
            entry.sub_keys = sub_keys

    merged = [
        entries[key]
        for key in sorted(entries, key=lambda k: entries[k].name.casefold())
    ]
    for entry in merged:
        entry.sources.sort(key=lambda label: _SOURCE_ORDER.get(label, 0))
    return merged, sorted(unresolved)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _title(name: str) -> str:
    """``# {name} Hops`` -- no "Hops Hops" when the name already says hops."""
    suffix = "" if name.casefold().rstrip().endswith(("hops", "hop")) else " Hops"
    return f"# {name}{suffix}"


def render_hop(entry: HopEntry) -> str:
    """Render one merged hop as a compact, one-chunk markdown document.

    Order is retrieval-driven: name, purpose, alpha and origin land inside
    the embedding window; the substitute list follows; source citations end
    the document.
    """
    lines = [_title(entry.name), ""]
    meta = [
        "**Type:** Ingredient (Hop)",
        "**Source:** aggregated hop substitution reference tables",
    ]
    if entry.purpose:
        meta.append(f"**Purpose:** {entry.purpose}")
    if entry.alpha:
        meta.append(f"**Alpha acid:** {entry.alpha}")
    if entry.origin:
        meta.append(f"**Origin:** {entry.origin}")
    lines += meta + [""]

    if entry.aroma:
        lines += ["## Aroma", _capped(entry.aroma, MAX_AROMA)]
    if entry.substitutes:
        lines += ["## Substitute hops", _capped(entry.substitutes, MAX_SUBSTITUTES)]
    if entry.sources:
        lines += ["## Sources"]
        lines += [
            f"- {label} — {url}"
            for label, url in SOURCES
            if label in entry.sources
        ]

    text = _finish(lines)
    if len(text) > MAX_DOC_CHARS:
        # Unreachable for real data (lists are short and capped); safety net
        # trims beyond the one-chunk budget rather than emit an oversized doc.
        text = _truncate(text, MAX_DOC_CHARS - 1) + "\n"
    return text


def hop_filename(entry: HopEntry) -> str:
    """``hop-<slug>.md`` -- the ``hop-`` prefix is the ingest contract."""
    slug = _slug(entry.name)
    return f"hop-{slug}.md" if slug else "hop-variety.md"


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

def _parsed_rows(pool: dict[str, str]) -> dict[str, list[HopRecord]]:
    """Parse all four sources; return per-source row lists."""
    return {
        "finder": parse_finder(pool[S_GETERBREWED], S_GETERBREWED),
        "grainfather": parse_chart(pool[S_GRAINFATHER], S_GRAINFATHER),
        "mangrove": parse_chart(pool[S_MANGROVE], S_MANGROVE),
        "aha": parse_aha(pool[S_AHA], S_AHA),
    }


def _row_floors(rows: dict[str, list[HopRecord]]) -> None:
    """Fail soft when any source returns implausibly few rows."""
    limits = (
        ("finder", len(rows["finder"]), MIN_FINDER_ROWS, "Geterbrewed"),
        ("grainfather", len(rows["grainfather"]), MIN_CHART_ROWS, "Grainfather"),
        ("mangrove", len(rows["mangrove"]), MIN_CHART_ROWS, "Mangrove Jack's"),
        ("aha", len(rows["aha"]), MIN_AHA_ROWS, "AHA"),
    )
    for key, actual, minimum, label in limits:
        if actual < minimum:
            raise SourceUnavailableError(
                f"hop_substitutions: only {actual} rows parsed from {label} "
                f"(expected >= {minimum}) - source restructured or blocking "
                "automated access?"
            )


@register
class HopSubstitutionsCollector(DataCollector):
    id = "hop_substitutions"
    description = (
        "hop substitution reference tables from 4 charts (geterbrewed, "
        "grainfather, mangrove jack's, homebrewers association): union of "
        "substitutes + alpha/purpose/aroma/provenance per hop"
    )

    def collect(self, dest: Path) -> list[Path]:
        # Fetch + parse + merge + render everything BEFORE clearing dest:
        # a failed run must never wipe previously collected files.
        pool = {
            label: fetch(url).decode("utf-8", errors="replace")
            for label, url in SOURCES
        }
        rows = _parsed_rows(pool)
        _row_floors(rows)

        # Record order is also the display/alpha/origin preference order:
        # Geterbrewed spells the cleanest, then the charts, then AHA.
        records = [
            *rows["finder"],
            *rows["grainfather"],
            *rows["mangrove"],
            *rows["aha"],
        ]
        merged, unresolved = merge_records(records)
        if len(merged) < MIN_MERGED:
            raise SourceUnavailableError(
                f"hop_substitutions: only {len(merged)} merged varieties "
                f"(expected >= {MIN_MERGED}) - tables restructured?"
            )

        product_rows = sum(1 for record in records if _is_product(record.name))
        print(
            f"hop_substitutions: {len(merged)} hops "
            f"(finder {len(rows['finder'])}, grainfather {len(rows['grainfather'])}, "
            f"mangrove {len(rows['mangrove'])}, aha {len(rows['aha'])}); "
            f"{product_rows} product rows excluded"
        )
        if unresolved:
            preview = ", ".join(unresolved[:10])
            more = "" if len(unresolved) <= 10 else f", … (+{len(unresolved) - 10})"
            print(
                f"hop_substitutions: warning: {len(unresolved)} substitute "
                f"name(s) unknown to the alias map, kept verbatim: "
                f"{preview}{more}"
            )

        rendered: dict[str, str] = {}
        for entry in merged:
            filename = _unique_filename(hop_filename(entry), rendered)
            rendered[filename] = render_hop(entry)
        return write_documents(rendered, dest)
