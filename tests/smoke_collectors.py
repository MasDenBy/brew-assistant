"""Smoke test for the collector framework (offline — no real sources).

Covers: registry, namespace rule, runner statuses (OK/SKIP/FAIL/MISSING),
the HTML→markdown helper used by web collectors, and the diydog archive
extraction (synthetic zip, no network).

Run: .venv\\Scripts\\python.exe tests\\smoke_collectors.py
"""
import io
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collectors import discover  # noqa: E402
from collectors.base import (  # noqa: E402
    DataCollector,
    SourceUnavailableError,
    register,
)
from collectors.runner import FAIL, MISSING, OK, SKIP, run  # noqa: E402
from collectors.web import html_to_markdown  # noqa: E402


@register
class _DemoCollector(DataCollector):
    id = "smoke_demo"
    description = "writes one fixture file"

    def collect(self, dest: Path) -> list[Path]:
        dest.mkdir(parents=True, exist_ok=True)
        doc = dest / "styles" / "demo.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("# Demo\n\nSmoke fixture.\n", encoding="utf-8")
        return [doc]


@register
class _FlakyCollector(DataCollector):
    id = "smoke_unavailable"

    def collect(self, dest: Path) -> list[Path]:
        raise SourceUnavailableError("example.com unreachable (connection refused)")


@register
class _BrokenCollector(DataCollector):
    id = "smoke_broken"

    def collect(self, dest: Path) -> list[Path]:
        raise RuntimeError("boom")


def main() -> None:
    discover()  # no *_collector modules yet in Step 1 — must be a no-op

    tmp = Path(tempfile.mkdtemp(prefix="brew_collectors_"))
    try:
        ids = ["smoke_demo", "smoke_unavailable", "smoke_broken", "does_not_exist"]
        results = {r.collector_id: r for r in run(ids, tmp)}

        by_id = {i: results[i].status for i in ids}
        assert by_id == {
            "smoke_demo": OK,
            "smoke_unavailable": SKIP,
            "smoke_broken": FAIL,
            "does_not_exist": MISSING,
        }, f"unexpected statuses: {by_id}"

        assert results["smoke_demo"].files == 1
        assert "unreachable" in results["smoke_unavailable"].detail
        assert "boom" in results["smoke_broken"].detail

        # Namespace rule: file lives under dest/<id>/, nowhere else
        assert (tmp / "smoke_demo" / "styles" / "demo.md").is_file()
        assert not (tmp / "styles").exists(), "collector wrote outside its namespace"
        assert not (tmp / "smoke_unavailable").exists(), "SKIP should not create dirs"

        # HTML → markdown helper (used by whitelabs/wyeast/hopunion collectors)
        html = (
            "<html><head><script>var x=1;</script></head><body>"
            "<h2>Strauss</h2><p>Attenuation <b>74</b>-76%.</p>"
            "<ul><li><a href='/strains/wlp001'>WLP001</a></li></ul>"
            "<table><tr><td>Temp</td><td>18-22 C</td></tr></table>"
            "</body></html>"
        )
        md = html_to_markdown(html)
        assert "var x" not in md, "script content leaked"
        assert md.startswith("## Strauss"), f"heading not converted: {md!r}"
        assert "74-76%" in md, f"text not preserved: {md!r}"
        assert "[WLP001](/strains/wlp001)" in md, f"link not converted: {md!r}"
        assert "| Temp" in md, f"table cell not converted: {md!r}"
        assert "<" not in md, f"raw HTML leaked: {md!r}"

        # --- diydog extraction: synthetic zip, no network ---
        from collectors.diydog_collector import extract_beerxml

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "diydog-beerxml-master/ab01.xml",
                "<RECIPES><RECIPE><NAME>Ab1</NAME></RECIPE></RECIPES>",
            )
            zf.writestr(
                "diydog-beerxml-master/ab02.xml",
                "<RECIPES><RECIPE><NAME>Ab2</NAME></RECIPE></RECIPES>",
            )
            zf.writestr("diydog-beerxml-master/README.md", "# not xml")
        dog_dest = tmp / "diydog"
        files = extract_beerxml(buf.getvalue(), dog_dest)
        assert len(files) == 2, files
        assert (dog_dest / "recipes" / "ab01.xml").is_file()
        assert not (dog_dest / "recipes" / "README.md").exists(), "non-xml leaked"
        # idempotent: stale file from a previous run is cleared
        stale = dog_dest / "recipes" / "stale.xml"
        stale.write_text("old", encoding="utf-8")
        files = extract_beerxml(buf.getvalue(), dog_dest)
        assert len(files) == 2 and not stale.exists(), "stale file survived re-run"

        print("PASS: registry, runner statuses (OK/SKIP/FAIL/MISSING),")
        print("      namespace rule, HTML -> markdown conversion,")
        print("      diydog zip extraction (idempotent, xml only)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
