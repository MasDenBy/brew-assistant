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
import collectors.whitelabs_collector as whitelabs_collector  # noqa: E402
from collectors.whitelabs_collector import (  # noqa: E402
    Strain,
    WhitelabsCollector,
    parse_strain,
    render_strain,
    strain_filename,
    strain_links,
    write_documents,
)


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

        # --- whitelabs: synthetic listing + detail page, no network ---
        listing_html = """
        <nav><a href="/yeast-bank?page=2">next page</a>
             <a href="/yeast-compare">compare</a></nav>
        <ul class="cards">
          <li><a href="/yeast-single?id=101&amp;type=YEAST">WLP001</a></li>
          <li><a href="/yeast-single?id=102&amp;type=YEAST">WLP002</a></li>
          <li><a href="/yeast-single?id=101&amp;type=YEAST">WLP001 again</a></li>
          <li><a href="/yeast-single?id=900&amp;type=BACTERIA">bacteria</a></li>
        </ul>
        """
        assert strain_links(listing_html) == ["101", "102"], strain_links(listing_html)

        detail_html = """
        <html><head>
          <title>WLP001 California Ale Yeast | White Labs</title>
          <style>.leak { color: red; }</style>
          <script>var secret = 1;</script>
        </head><body>
          <h1>WLP001 California Ale Yeast®</h1>
          <h2 class="table-title w-h2">ATTENUATION :</h2><p>73% - 85%</p>
          <h2 class="table-title w-h2">FLOCCULATION :</h2><p>Medium</p>
          <h2 class="table-title w-h2">ALCOHOL TOLERANCE :</h2><p>Medium (5-10%)</p>
          <h2 class="table-title w-h2">FERMENTATION TEMPERATURE :</h2>
            <p>64° - 73° F<br>18° - 23° C</p>
          <h2 class="table-title w-h2">STA1 :</h2><p>Negative</p>
          <div class="dynamic-content"><ul><li>Suggested tab, not the profile</li></ul></div>
          <ul class="details">
            <li><strong class="w-h3">Part No: </strong>WLP001</li>
            <li><strong class="w-h3">Part Name: </strong>WLP001 California Ale Yeast®</li>
            <li><strong class="w-h3">Also Available in Organic: </strong>Yes</li>
            <li><strong class="w-h3">STA1 QC Result: </strong>Negative</li>
            <li><strong class="w-h3">Type: </strong>Core</li>
          </ul>
          <h2 class="w-h2" style="font-weight:700">Description</h2>
          <div class="dynamic-content">
            <p>A clean, <strong>crisp</strong> fermenting yeast.</p>
            <script>var leaked = 2;</script>
            <style>.leaked { color: blue; }</style>
            <img src="leak.jpg" alt="alt text must not leak">
            <div><p>Great for American ales.</p></div>
            <p>&nbsp;&nbsp;&nbsp;</p>
            <p>Another paragraph.</p>
          </div>
          <ul class="beer_style">
            <li> American IPA </li>
            <li> American Wheat Beer </li>
            <li> Blonde Ale </li>
            <li> American IPA </li>
          </ul>
          <script>var trailing = 3;</script>
        </body></html>
        """
        strain = parse_strain(
            detail_html, source_url="https://www.whitelabs.com/yeast-single?id=101&type=YEAST"
        )
        assert strain is not None
        assert strain.name == "WLP001 California Ale Yeast®", strain.name
        assert strain.part == "WLP001", strain.part
        assert strain.availability == "Core", strain.availability
        assert strain.specs["ATTENUATION"] == "73% - 85%", strain.specs
        assert strain.specs["FLOCCULATION"] == "Medium", strain.specs
        assert strain.specs["ALCOHOL TOLERANCE"] == "Medium (5-10%)", strain.specs
        assert strain.specs["STA1"] == "Negative", strain.specs
        assert (
            strain.specs["FERMENTATION TEMPERATURE"] == "18–23 °C (64–73 °F)"
        ), strain.specs["FERMENTATION TEMPERATURE"]
        assert strain.styles == [
            "American IPA",
            "American Wheat Beer",
            "Blonde Ale",
        ], strain.styles
        # only the div after the Description heading is the profile
        assert "Suggested tab" not in strain.description, strain.description
        assert "A clean, crisp fermenting yeast." in strain.description, strain.description
        assert "Great for American ales." in strain.description, strain.description
        assert "leaked" not in strain.description, strain.description
        assert "alt text" not in strain.description, strain.description
        assert "<" not in strain.description, strain.description
        assert strain.source_url.endswith("id=101&type=YEAST")

        # live pages nest ul.details inside the description container; the
        # details must still parse and must not leak into the profile text
        nested = (
            "<h1>WLP002 English Ale Yeast</h1>"
            "<h2 class='table-title w-h2'>ATTENUATION :</h2><p>63% - 70%</p>"
            "<h2>Description</h2>"
            "<div class='dynamic-content'><p>Profile text.</p>"
            "<ul class='details'>"
            "<li><strong class='w-h3'>Part No: </strong>WLP002</li>"
            "<li><strong class='w-h3'>Type: </strong>Core</li>"
            "</ul></div>"
        )
        nested_strain = parse_strain(nested)
        assert nested_strain is not None
        assert nested_strain.part == "WLP002", nested_strain.part
        assert nested_strain.availability == "Core", nested_strain.availability
        assert "Profile text." in nested_strain.description, nested_strain.description
        assert "Part No" not in nested_strain.description, nested_strain.description

        # pages that are not renderable strains
        assert parse_strain("<html><body><h1>404</h1></body></html>") is None
        assert parse_strain("<html><body><p>nothing here</p></body></html>") is None
        product = (
            "<h1>WLN1000 Yeast Nutrient</h1>"
            "<div class='dynamic-content'><p>A nutrient, not a strain.</p></div>"
        )
        assert parse_strain(product) is None, "non-yeast product must be skipped"

        doc = render_strain(strain)
        assert doc.startswith("# WLP001 California Ale Yeast® (White Labs)"), doc[:60]
        assert "**Type:** Ingredient (Yeast)" in doc
        assert "**Lab:** White Labs" in doc
        assert "**Product:** WLP001" in doc
        assert "**Availability:** Core" in doc
        for bullet in (
            "- Attenuation: 73% - 85%",
            "- Flocculation: Medium",
            "- Alcohol tolerance: Medium (5-10%)",
            "- Recommended temperature: 18–23 °C (64–73 °F)",
            "- STA1: Negative",
        ):
            assert bullet in doc, f"missing bullet {bullet!r}"
        assert (
            "## Suggested Styles\nAmerican IPA, American Wheat Beer, Blonde Ale" in doc
        ), doc
        assert "## Profile" in doc and "A clean, crisp" in doc
        assert "<" not in doc, f"raw HTML leaked: {doc!r}"
        assert doc.endswith("\n") and not doc.endswith("\n\n")
        assert all(line == line.rstrip() for line in doc.splitlines()), "trailing whitespace"
        assert len(doc) <= 1900, f"doc exceeds the one-chunk budget: {len(doc)}"

        # filenames: yeast- prefix is the ingest contract; ASCII slugs
        assert (
            strain_filename(strain) == "yeast-wlp001-california-ale-yeast.md"
        ), strain_filename(strain)
        voss = Strain(name="LalBrew Voss™", part="WLDVOSS")
        assert strain_filename(voss) == "yeast-wldvoss-lalbrew-voss.md", strain_filename(voss)
        melange = Strain(name="WLP585 Mélange", part="WLP585")
        assert strain_filename(melange) == "yeast-wlp585-melange.md", strain_filename(melange)

        # write_documents: clears its own subtree, writes only ingredients/
        wl_dest = tmp / "whitelabs_write"
        (wl_dest / "ingredients").mkdir(parents=True, exist_ok=True)
        stale = wl_dest / "ingredients" / "yeast-stale.md"
        stale.write_text("old", encoding="utf-8")
        written = write_documents({"yeast-x.md": "# X\n"}, wl_dest)
        assert len(written) == 1 and written[0].is_file()
        assert not stale.exists(), "stale file survived re-run"
        assert written[0].parent == wl_dest / "ingredients", written[0]
        assert [p.name for p in wl_dest.iterdir()] == ["ingredients"]

        # fail soft: an unreachable source raises and preserves old output
        soft_dest = tmp / "whitelabs_soft"
        kept = soft_dest / "ingredients" / "yeast-keep.md"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text("# keep\n", encoding="utf-8")
        original_fetch = whitelabs_collector.fetch_text

        def _offline(url, **kwargs):
            raise SourceUnavailableError(f"offline: {url}")

        whitelabs_collector.fetch_text = _offline
        try:
            try:
                WhitelabsCollector().collect(soft_dest)
            except SourceUnavailableError:
                pass
            else:
                raise AssertionError("unreachable source must raise SourceUnavailableError")
        finally:
            whitelabs_collector.fetch_text = original_fetch
        assert kept.is_file(), "fail-soft run wiped previously collected files"

        print("PASS: registry, runner statuses (OK/SKIP/FAIL/MISSING),")
        print("      namespace rule, HTML -> markdown conversion,")
        print("      diydog zip extraction (idempotent, xml only),")
        print("      bjcp style rendering (sections, type/tags, aliases, payload),")
        print("      whitelabs strain parsing/rendering (specs, styles, fail-soft)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
