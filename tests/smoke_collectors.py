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

        # --- bjcp rendering: synthetic style dict, no network ---
        import json as _json

        from collectors.bjcp_collector import (
            parse_styles,
            render_style,
            style_filename,
        )

        style = {
            "style_id": "21A",
            "name": "American IPA",
            "category": "IPA",
            "style_guide": "BJCP2021",
            "tags": "standard-strength, pale-color, top-fermented, north-america",
            "original_gravity": {
                "minimum": {"unit": "sg", "value": 1.056},
                "maximum": {"unit": "sg", "value": 1.07},
            },
            "international_bitterness_units": {
                "minimum": {"unit": "IBUs", "value": 40},
                "maximum": {"unit": "IBUs", "value": 70},
            },
            "alcohol_by_volume": {
                "minimum": {"unit": "%", "value": 5.5},
                "maximum": {"unit": "%", "value": 7.5},
            },
            "color": {
                "minimum": {"unit": "SRM", "value": 6},
                "maximum": {"unit": "SRM", "value": 14},
            },
            "overall_impression": "A decidedly hoppy and bitter, moderately strong, pale American ale.",
            "aroma": "Citrusy and floral hop aroma.",
            "appearance": "Golden to amber with a persistent head.",
            "flavor": "Moderate malt backbone with firm hop bitterness.",
            "mouthfeel": "Medium body, medium carbonation.",
            "style_comparison": "Less malty than a Red Ale.",
            "history": "Developed in the United States in the 1990s.",
            "ingredients": "Pale malt with substantial hop additions.",
            "notes": "Recipes often use a dry yeast.",
            "examples": "Bell's Two-Hearted Ale",
        }
        md = render_style(style)
        assert md.startswith("# 21A American IPA (BJCP style)"), md[:80]
        assert "Category: IPA (BJCP2021)" in md
        # Type derived from the fermentation tag; full tags line kept
        assert "Type: ale (top-fermented)" in md, md
        assert (
            "Tags: standard-strength, pale-color, top-fermented, north-america" in md
        ), md
        # all stats on ONE dense line (keeps them in the embedding window)
        stats_line = next(line for line in md.splitlines() if line.startswith("OG:"))
        for frag in ("ABV: 5.5% - 7.5%", "IBU: 40 - 70", "Color: 6 - 14 SRM"):
            assert frag in stats_line, f"{frag!r} missing from {stats_line!r}"
        assert stats_line.count(" | ") == 3, stats_line
        assert "## Overall impression\nA decidedly hoppy" in md
        # requested narrative sections, in reading order
        for heading, snippet in (
            ("## Aroma", "Citrusy and floral"),
            ("## Appearance", "Golden to amber"),
            ("## Flavor", "Moderate malt backbone"),
            ("## Mouthfeel", "Medium body"),
            ("## Characteristic ingredients", "Pale malt"),
            ("## Comparison with other styles", "Less malty than a Red Ale"),
            ("## Origin and history", "Developed in the United States"),
            ("## Notes", "Recipes often use a dry yeast."),
            ("## Examples", "Bell's Two-Hearted Ale"),
        ):
            assert f"{heading}\n{snippet}" in md, f"missing {heading}"
        order = [
            "## Vital statistics",
            "## Characteristic ingredients",
            "## Overall impression",
            "## Aroma",
            "## Appearance",
            "## Flavor",
            "## Mouthfeel",
            "## Comparison with other styles",
            "## Origin and history",
            "## Notes",
            "## Examples",
        ]
        positions = [md.index(h) for h in order]
        assert positions == sorted(positions), f"section order broken: {positions}"
        assert style_filename(style) == "21a-american-ipa.md"

        # Type falls back to the raw type field when no fermentation tag
        no_ferm = {
            "style_id": "1A",
            "name": "American Light Lager",
            "type": "beer",
            "tags": "session-strength, pale-color",
        }
        assert "Type: beer" in render_style(no_ferm)

        # mead/cider style without OG/IBU stats: renders, no vital section
        bare = {
            "style_id": "28A",
            "name": "Dry Mead",
            "category": "Traditional Mead",
            "overall_impression": "A mead fermented to dryness.",
        }
        md2 = render_style(bare)
        assert "# 28A Dry Mead (BJCP style)" in md2
        assert "## Vital statistics" not in md2, md2
        assert "Type:" not in md2, md2
        assert "## Overall impression" in md2
        assert style_filename(bare) == "28a-dry-mead.md"

        # vital_statistics text instead of numeric stats (17 mead/cider styles)
        vs_entry = {
            "style_id": "28B",
            "name": "Brett Beer",
            "category": "Experimental Beer",
            "vital_statistics": "Variable by base style.",
        }
        md_vs = render_style(vs_entry)
        assert "## Vital statistics\nVariable by base style." in md_vs, md_vs

        # translated (Spanish) entry: localized field aliases recover sections
        es = {
            "style_id": "X2",
            "name": "IPA Argenta",
            "category": "Argentine Styles",
            "style_guide": "BJCP2021",
            # this entry stores SG x1000 upstream (1055 == OG 1.055)
            "original_gravity": {
                "minimum": {"unit": "sg", "value": 1055},
                "maximum": {"unit": "sg", "value": 1065},
            },
            "impresion_general": "Una IPA argentina.",
            "aspecto": "Color ambar.",
            "sabor": "Sabores citricos y malteados.",
            "sensacion_en_boca": "Cuerpo medio.",
            "historia": "Creada en Argentina.",
            "ingredientes": "Malta pale y lupa.",
            "ejemplos_comerciales": "Patagonia IPA",
        }
        md3 = render_style(es)
        assert "OG: 1.055 - 1.065" in md3, f"gravity not repaired: {md3}"
        for heading, snippet in (
            ("## Overall impression", "Una IPA argentina."),
            ("## Appearance", "Color ambar."),
            ("## Flavor", "Sabores citricos"),
            ("## Mouthfeel", "Cuerpo medio."),
            ("## Characteristic ingredients", "Malta pale"),
            ("## Origin and history", "Creada en Argentina."),
            ("## Examples", "Patagonia IPA"),
        ):
            assert f"{heading}\n{snippet}" in md3, f"alias missed {heading}"

        # payload validation: good structure parses, garbage fails soft
        good = _json.dumps(
            {"beerjson": {"version": 1.0, "styles": [style, bare]}}
        ).encode("utf-8")
        assert len(parse_styles(good)) == 2
        try:
            parse_styles(b"<html>not json</html>")
        except SourceUnavailableError:
            pass
        else:
            raise AssertionError("bad payload must raise SourceUnavailableError")

        print("PASS: registry, runner statuses (OK/SKIP/FAIL/MISSING),")
        print("      namespace rule, HTML -> markdown conversion,")
        print("      diydog zip extraction (idempotent, xml only),")
        print("      bjcp style rendering (sections, type/tags, aliases, payload)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
