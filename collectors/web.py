"""Polite HTTP helpers for web collectors.

Uses the pinned ``requests`` package (already a dependency via app.py) and
the stdlib HTML parser — no new dependencies. Every request:

- sends a descriptive user agent,
- is throttled (min delay between requests),
- times out and retries with backoff,
- raises :class:`SourceUnavailableError` when the source is unreachable,
  so the runner can report SKIP instead of crashing the whole run.
"""
from __future__ import annotations

import time
from html.parser import HTMLParser

import requests

from .base import SourceUnavailableError

USER_AGENT = (
    "brew-assistant-collector/0.1 (local RAG assistant; "
    "github.com/MasDenBy/brew-assistant)"
)
MIN_DELAY_SECONDS = 0.5   # politeness: min gap between requests
REQUEST_TIMEOUT = 20      # seconds
RETRIES = 3
_BACKOFF_SECONDS = (1.0, 3.0)  # pause before 2nd / 3rd attempt

_last_request_at = 0.0


def _throttle() -> None:
    global _last_request_at
    wait = MIN_DELAY_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def fetch(url: str, *, timeout: int = REQUEST_TIMEOUT) -> bytes:
    """GET ``url`` → bytes. Retries 429/5xx/network errors with backoff;
    other 4xx responses fail immediately (page moved → SKIP)."""
    last_error: Exception | None = None
    for attempt in range(RETRIES):
        if attempt:
            time.sleep(_BACKOFF_SECONDS[attempt - 1])
        _throttle()
        try:
            resp = requests.get(
                url, timeout=timeout, headers={"User-Agent": USER_AGENT}
            )
        except requests.RequestException as exc:
            last_error = exc
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = requests.HTTPError(
                f"HTTP {resp.status_code} for {url}", response=resp
            )
            continue
        if not resp.ok:
            raise SourceUnavailableError(f"{url} returned HTTP {resp.status_code}")
        return resp.content
    raise SourceUnavailableError(
        f"could not fetch {url} after {RETRIES} attempts: {last_error}"
    )


def fetch_text(url: str, *, encoding: str = "utf-8", timeout: int = REQUEST_TIMEOUT) -> str:
    """GET ``url`` → decoded text (best effort on encoding)."""
    return fetch(url, timeout=timeout).decode(encoding, errors="replace")


# ---------------------------------------------------------------------------
# HTML → markdown-ish text (good enough for reference pages; no raw HTML out)
# ---------------------------------------------------------------------------

class _Markdownifier(HTMLParser):
    _SKIP = {"script", "style", "head", "noscript", "svg", "template"}
    _BLOCKS = {
        "p", "div", "section", "article", "table", "blockquote",
        "header", "footer", "main", "dl", "dt", "dd",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._href: str | None = None
        self._link_text: list[str] = []

    def _emit(self, text: str) -> None:
        self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._emit("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self._emit("\n- ")
        elif tag == "br":
            self._emit("\n")
        elif tag in ("td", "th"):
            self._emit("| ")
        elif tag == "a" and not self._href:
            href = dict(attrs).get("href")
            if href:
                self._href = href
                self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") or tag in self._BLOCKS:
            self._emit("\n\n")
        elif tag in ("td", "th"):
            self._emit(" ")
        elif tag == "a" and self._href is not None:
            text = "".join(self._link_text).strip()
            self._emit(f"[{text}]({self._href})" if text else "")
            self._href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._href is not None:
            self._link_text.append(data)
        else:
            self._emit(data)


def html_to_markdown(html: str) -> str:
    """Convert HTML to readable markdown-ish text (headings, lists, links,
    pipe-ish tables); collapses whitespace and trims blank lines."""
    import re

    parser = _Markdownifier()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)   # collapse runs of spaces
    text = re.sub(r" *\n *", "\n", text)        # trim line edges
    text = re.sub(r"\n{3,}", "\n\n", text)      # one blank line max
    return text.strip()
