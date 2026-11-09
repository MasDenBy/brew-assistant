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
    all_registered,
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
import collectors.wyeast_collector as wyeast_collector  # noqa: E402
from collectors.wyeast_collector import (  # noqa: E402
    Strain as WyeastStrain,
    WyeastCollector,
    parse_strain as parse_wyeast_strain,
    product_urls,
    render_strain as render_wyeast_strain,
    strain_filename as wyeast_filename,
    write_documents as write_wyeast_documents,
)
import collectors.brewingtools_collector as brewingtools_collector  # noqa: E402
from collectors.brewingtools_collector import (  # noqa: E402
    BrewingToolsCollector,
    FERMENTABLES_URL,
    MIN_MALT_RECORDS,
    SOURCE_URL,
    Hop,
    Malt,
    hop_filename,
    malt_filename,
    parse_dataset,
    parse_malts,
    render_hop,
    render_malt,
    write_documents as write_brewingtools_documents,
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

        # HTML → markdown helper (used by whitelabs/wyeast collectors)
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

        # --- wyeast: synthetic sitemap + product page, no network ---
        sitemap_xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<url><loc>https://wyeastlab.com/our-products/</loc></url>"
            b"<url><loc>https://wyeastlab.com/product/alpha/</loc></url>"
            b"<url><loc>https://wyeastlab.com/product/beta/</loc></url>"
            b"<url><loc>https://wyeastlab.com/product/alpha/</loc></url>"
            b"<url><loc>https://wyeastlab.com/style/weissbier/</loc></url>"
            b"<url><loc>https://wyeastlab.com/product/gamma/</loc></url>"
            b"</urlset>"
        )
        urls = product_urls(sitemap_xml)
        assert urls == [
            "https://wyeastlab.com/product/alpha/",
            "https://wyeastlab.com/product/beta/",
            "https://wyeastlab.com/product/gamma/",
        ], urls
        # garbage does not crash the helper; the collector turns it into SKIP
        assert product_urls(b"<html>not xml</html>") == []

        beer_html = """
        <html><head>
          <title>Strain: 3068 | Wyeast</title>
          <style>.leak { color: red; }</style>
          <script>var secret = 1;</script>
        </head><body>
          <h2 class="headline">Strain: 3068</h2>
          <h3 class="subheadline">Weihenstephan Weizen</h3>
          <div class="description">
            <p><strong>Species:</strong>
               <i>Saccharomyces cerevisiae</i></p>
            <p><strong>Profile: </strong> The classic and most popular German
               wheat beer strain used worldwide.
               <img src="leak.jpg" alt="alt text must not leak">
               Over pitching can result in a loss of banana character.</p>
          </div>
          <div class="tabs js-product-tabs">
            <h3 class="tabs-title">Used in:</h3>
            <div class="tabs-nav">
              <div class="tab-nav-item accent-color-orange-dark is-active">
                <div class="icon"><img src="icon-beer.svg" alt="Beer"></div>
                <span>Beer</span>
              </div>
            </div>
            <div class="tabs-content">
              <div class="tab-content is-active">
                <ul class="features">
                  <li><h6>Flocculation</h6><span>Low</span></li>
                  <li><h6>Apparent Attenuation</h6><span>73 - 77%</span></li>
                  <li><h6>Apparent ABV Tolerance</h6><span>10%</span></li>
                  <li><h6>Temperature Range</h6><span>64-75°F (18-24°C)</span></li>
                </ul>
              </div>
            </div>
          </div>
          <section class="content-block template-related-styles">
            <header class="template-header">
              <p class="preheadline">Recommended for Styles</p>
            </header>
            <div class="grid-x block-grid">
              <div class="cell"><div class="inner"><div class="content">
                <h3 class="title">Weissbier</h3>
                <h4 class="subtitle">Wheat</h4>
              </div></div></div>
              <div class="cell"><div class="inner"><div class="content">
                <h3 class="title">Dunkles Weissbier</h3>
              </div></div></div>
              <div class="cell"><div class="inner"><div class="content">
                <h3 class="title">Weizenbock</h3>
              </div></div></div>
            </div>
          </section>
          <header class="module"><h2 class="headline">Let’s get brewing.</h2></header>
          <script>var trailing = 3;</script>
        </body></html>
        """
        beer = parse_wyeast_strain(
            beer_html, source_url="https://wyeastlab.com/product/weihenstephan-weizen/"
        )
        assert beer is not None
        assert beer.part == "3068", beer.part
        assert beer.name == "Weihenstephan Weizen", beer.name
        assert beer.species == "Saccharomyces cerevisiae", beer.species
        assert "classic and most popular" in beer.profile, beer.profile
        assert "alt text" not in beer.profile, beer.profile
        assert "secret" not in beer.profile, beer.profile
        assert "<" not in beer.profile, beer.profile
        assert beer.used_in == ["Beer"], beer.used_in
        assert beer.specs["FLOCCULATION"] == "Low", beer.specs
        assert beer.specs["APPARENT ATTENUATION"] == "73 - 77%", beer.specs
        assert beer.specs["APPARENT ABV TOLERANCE"] == "10%", beer.specs
        assert (
            beer.specs["TEMPERATURE RANGE"] == "18–24 °C (64–75 °F)"
        ), beer.specs["TEMPERATURE RANGE"]
        assert beer.styles == [
            "Weissbier",
            "Dunkles Weissbier",
            "Weizenbock",
        ], beer.styles

        # multi-beverage page: repeated feature lists dedupe, labels accumulate
        multi_html = """
        <h2 class="headline">Strain: 4766</h2>
        <h3 class="subheadline">Cider</h3>
        <div class="description">
          <p><strong>Species:</strong> <i>Saccharomyces cerevisiae</i></p>
          <p><strong>Profile: </strong> Crisp and dry fermenting yeast.</p>
        </div>
        <div class="tabs js-product-tabs">
          <h3 class="tabs-title">Used in:</h3>
          <div class="tabs-nav">
            <div class="tab-nav-item"><div class="icon"><img alt="Cider"></div>
              <span>Cider</span></div>
            <div class="tab-nav-item"><div class="icon"><img alt="Mead"></div>
              <span>Mead</span></div>
          </div>
          <div class="tabs-content">
            <div class="tab-content is-active"><ul class="features">
              <li><h6>Flocculation</h6><span>Low</span></li>
              <li><h6>Apparent ABV Tolerance</h6><span>12%</span></li>
              <li><h6>Temperature Range</h6><span>60-75°F (16-24°C)</span></li>
            </ul></div>
            <div class="tab-content"><ul class="features">
              <li><h6>Flocculation</h6><span>Low</span></li>
              <li><h6>Apparent ABV Tolerance</h6><span>12%</span></li>
              <li><h6>Temperature Range</h6><span>60-75°F (16-24°C)</span></li>
            </ul></div>
          </div>
        </div>
        """
        multi = parse_wyeast_strain(multi_html)
        assert multi is not None
        assert multi.used_in == ["Cider", "Mead"], multi.used_in
        assert len(multi.specs) == 3, multi.specs
        multi_doc = render_wyeast_strain(multi)
        assert multi_doc.count("- Flocculation:") == 1, multi_doc
        assert multi_doc.count("- Recommended temperature:") == 1, multi_doc
        assert "**Used in:** Cider, Mead" in multi_doc, multi_doc

        # non-culture product: no "Strain:" headline -> skipped
        brewtan_html = """
        <h2 class="headline">Wyeast Brewtan B</h2>
        <div class="description"><p>A tannic acid product, not a culture.</p></div>
        """
        assert parse_wyeast_strain(brewtan_html) is None

        # bacteria culture: kept, species-aware Type line, yeast- bucket filename
        bacteria_html = """
        <h2 class="headline">Strain: 5335</h2>
        <h3 class="subheadline">Lactobacillus buchneri</h3>
        <div class="description">
          <p><strong>Species:</strong> <i>Lactobacillus buchneri</i></p>
          <p><strong>Profile: </strong> Produces moderate levels of acidity.</p>
        </div>
        <div class="tabs js-product-tabs">
          <div class="tabs-nav">
            <div class="tab-nav-item"><span>Beer</span></div>
          </div>
          <div class="tabs-content"><div class="tab-content"><ul class="features">
            <li><h6>Flocculation</h6><span>Low - High</span></li>
            <li><h6>Temperature Range</h6><span>60-95°F (16-35°C)</span></li>
          </ul></div></div>
        </div>
        """
        bacteria = parse_wyeast_strain(bacteria_html)
        assert bacteria is not None
        assert bacteria.species == "Lactobacillus buchneri", bacteria.species
        bacteria_doc = render_wyeast_strain(bacteria)
        assert "**Type:** Ingredient (Bacteria culture)" in bacteria_doc, bacteria_doc
        assert "**Species:** Lactobacillus buchneri" in bacteria_doc, bacteria_doc
        assert (
            wyeast_filename(bacteria) == "yeast-5335-lactobacillus-buchneri.md"
        ), wyeast_filename(bacteria)

        # rendering: retrieval-first layout, one chunk, no raw HTML
        doc = render_wyeast_strain(beer)
        assert doc.startswith("# 3068 Weihenstephan Weizen (Wyeast)"), doc[:60]
        assert "**Type:** Ingredient (Yeast)" in doc
        assert "**Lab:** Wyeast Laboratories" in doc
        assert "**Product:** 3068" in doc
        assert "**Species:** Saccharomyces cerevisiae" in doc
        assert "**Used in:** Beer" in doc
        assert "**Availability:**" not in doc, doc
        for bullet in (
            "- Flocculation: Low",
            "- Apparent attenuation: 73 - 77%",
            "- Apparent ABV tolerance: 10%",
            "- Recommended temperature: 18–24 °C (64–75 °F)",
        ):
            assert bullet in doc, f"missing bullet {bullet!r}"
        assert (
            "## Suggested Styles\nWeissbier, Dunkles Weissbier, Weizenbock" in doc
        ), doc
        assert "## Profile" in doc and "classic and most popular" in doc
        assert "<" not in doc, f"raw HTML leaked: {doc!r}"
        assert doc.endswith("\n") and not doc.endswith("\n\n")
        assert all(line == line.rstrip() for line in doc.splitlines()), "trailing whitespace"
        assert len(doc) <= 1900, f"doc exceeds the one-chunk budget: {len(doc)}"

        # private collection: -PC suffix adds an availability line
        pc = WyeastStrain(part="2035-PC", name="American Lager")
        pc_doc = render_wyeast_strain(pc)
        assert "# 2035-PC American Lager (Wyeast)" in pc_doc, pc_doc
        assert "**Availability:** Private Collection (seasonal release)" in pc_doc, pc_doc
        assert "**Product:** 2035-PC" in pc_doc, pc_doc

        # long profiles are truncated to stay inside one ingest chunk
        long_doc = render_wyeast_strain(
            WyeastStrain(
                part="9999",
                name="Long Profile",
                species="Saccharomyces cerevisiae",
                profile="A long profile sentence. " * 300,
            )
        )
        assert len(long_doc) <= 1900, len(long_doc)
        assert long_doc.endswith(" …\n"), long_doc[-40:]

        # filenames: yeast- prefix is the ingest contract; ASCII slugs
        assert (
            wyeast_filename(WyeastStrain(part="3068", name="Weihenstephan Weizen"))
            == "yeast-3068-weihenstephan-weizen.md"
        )
        assert (
            wyeast_filename(WyeastStrain(part="2035-PC", name="American Lager"))
            == "yeast-2035-pc-american-lager.md"
        )
        assert (
            wyeast_filename(WyeastStrain(part="2575-PC", name="Kölsch II"))
            == "yeast-2575-pc-kolsch-ii.md"
        )
        assert (
            wyeast_filename(WyeastStrain(part="5335", name="Lactobacillus buchneri"))
            == "yeast-5335-lactobacillus-buchneri.md"
        )

        # write_documents: clears its own subtree, writes only ingredients/
        wy_dest = tmp / "wyeast_write"
        (wy_dest / "ingredients").mkdir(parents=True, exist_ok=True)
        wy_stale = wy_dest / "ingredients" / "yeast-stale.md"
        wy_stale.write_text("old", encoding="utf-8")
        wy_written = write_wyeast_documents({"yeast-x.md": "# X\n"}, wy_dest)
        assert len(wy_written) == 1 and wy_written[0].is_file()
        assert not wy_stale.exists(), "stale file survived re-run"
        assert wy_written[0].parent == wy_dest / "ingredients", wy_written[0]
        assert [p.name for p in wy_dest.iterdir()] == ["ingredients"]

        # collector success path: sitemap -> pages, non-culture product skipped
        live_dest = tmp / "wyeast_live"
        pages = {
            "https://wyeastlab.com/product/alpha/": beer_html,
            "https://wyeastlab.com/product/beta/": brewtan_html,
            "https://wyeastlab.com/product/gamma/": bacteria_html,
        }
        original_wy_fetch = wyeast_collector.fetch
        original_wy_fetch_text = wyeast_collector.fetch_text
        wyeast_collector.fetch = lambda url, **kwargs: sitemap_xml
        wyeast_collector.fetch_text = lambda url, **kwargs: pages[url]
        try:
            live_written = WyeastCollector().collect(live_dest)
        finally:
            wyeast_collector.fetch = original_wy_fetch
            wyeast_collector.fetch_text = original_wy_fetch_text
        assert len(live_written) == 2, live_written
        assert (
            live_dest / "ingredients" / "yeast-3068-weihenstephan-weizen.md"
        ).is_file()
        assert (
            live_dest / "ingredients" / "yeast-5335-lactobacillus-buchneri.md"
        ).is_file()

        # fail soft: sitemap down / garbage / every page failing; old output kept
        wy_soft_dest = tmp / "wyeast_soft"
        wy_kept = wy_soft_dest / "ingredients" / "yeast-keep.md"
        wy_kept.parent.mkdir(parents=True, exist_ok=True)
        wy_kept.write_text("# keep\n", encoding="utf-8")

        def _offline_fetch(url, **kwargs):
            raise SourceUnavailableError(f"offline: {url}")

        def _garbage_fetch(url, **kwargs):
            return b"<html>not xml</html>"

        for label, sitemap_fetch, page_fetch in (
            ("sitemap down", _offline_fetch, original_wy_fetch_text),
            ("garbage sitemap", _garbage_fetch, original_wy_fetch_text),
            ("all pages failing", lambda url, **kw: sitemap_xml, _offline_fetch),
        ):
            wyeast_collector.fetch = sitemap_fetch
            wyeast_collector.fetch_text = page_fetch
            try:
                try:
                    WyeastCollector().collect(wy_soft_dest)
                except SourceUnavailableError:
                    pass
                else:
                    raise AssertionError(
                        f"{label}: unreachable source must raise SourceUnavailableError"
                    )
            finally:
                wyeast_collector.fetch = original_wy_fetch
                wyeast_collector.fetch_text = original_wy_fetch_text
            assert wy_kept.is_file(), f"{label}: fail-soft run wiped prior output"

        # --- brewingtools: synthetic hops.json payload, no network ---
        full_record = {
            "id": "citra-hbc-394",
            "name": "Citra HBC 394",
            "category": "hop",
            "origin": "USA",
            "pedigree": "Bred by the Hop Breeding Company and released in 2007.",
            "aromaDescriptors": ["citrus", "tropical"],
            # dataset ids: resolve to names; the self-reference is dropped
            "substitutes": ["mckenzie-c-148", "triskel", "citra-hbc-394"],
            "pairings": ["triskel"],
            "alphaAcidPercent": {"min": 11, "max": 14.3},
            "betaAcidPercent": {"min": 2.7, "max": 4.5},
            "cohumulonePercentOfAlpha": {"min": 20, "max": 35},
            "totalOilMlPer100g": {"max": 2.8},
            "oilComposition": {
                "myrcenePercent": {"min": 29, "max": 66},
                "humulenePercent": {"min": 7.9, "max": 13.2},
                "geraniolPercent": {"min": 0, "max": 0},   # "not published"
            },
            "provenance": {
                "primary": {
                    "publisher": "Crosby Hops",
                    "document": "Hop Catalog - Citra HBC 394",
                    "url": "https://www.crosbyhops.com/shop-hops/hop-catalog/citra",
                    "retrieved": "2026-08-10",
                }
            },
        }
        typical_record = {
            "id": "mckenzie-c-148",
            "name": "McKenzie C-148",
            "origin": "USA",
            "alphaAcidPercent": {"typical": 10.3},
            "betaAcidPercent": {"typical": 8.7},
            "totalOilMlPer100g": {"typical": 2.3},
            "provenance": {
                "primary": {
                    "publisher": "John I. Haas",
                    "document": "Hop Spec Sheet",
                    "url": "https://hops.com.au/downloads/data-sheet/america/McKenzie-Data-Sheet.pdf",
                    "retrieved": "2026-08-10",
                }
            },
        }
        minimal_record = {
            "id": "triskel",
            "name": "Triskel",
            "origin": "",
            "alphaAcidPercent": {"min": 1.5, "max": 5},
            "totalOilMlPer100g": {"min": 1.5},
            "provenance": {
                "primary": {
                    "publisher": "Hopsteiner",
                    "document": "Hop Variety Data Sheet - Triskel",
                    "url": "https://hopsteiner.us/variety-data-sheets/triskel/",
                    "retrieved": "2026-08-10",
                }
            },
        }
        hops_payload = _json.dumps(
            {
                "name": "brewing.tools open ingredient database - hop varieties",
                "version": "0.7.0",
                "generated": "2026-09-28",
                "recordCount": 3,
                "hops": [full_record, typical_record, minimal_record],
            }
        ).encode("utf-8")

        # enough hop records to clear MIN_RECORDS for the success path
        bulk_hops = [full_record, typical_record, minimal_record] + [
            {
                "id": f"variety-{i}",
                "name": f"Variety {i}",
                "alphaAcidPercent": {"min": 5, "max": 6},
            }
            for i in range(50)
        ]
        bulk_hops_payload = _json.dumps(
            {"version": "0.7.0", "hops": bulk_hops}
        ).encode("utf-8")

        # --- brewingtools malts: synthetic fermentables.json payload ---
        malt_full_record = {
            "id": "weyermann-abbey-malt",
            "name": "Abbey Malt®",
            "type": "grain",
            "grainGroup": "specialty",
            "maltster": "Weyermann Specialty Malts",
            "origin": "Germany, EU",
            "aliases": ["Abbey Malt", "Weyermann Abbey Malt"],
            "colourLovibond": {"min": 15.5, "max": 19.3},
            "colourEbc": {"min": 40, "max": 50},
            "extractFineGrindDryPercent": {"min": 75},
            "moisturePercent": {"max": 4.5},
            "maxInBatchPercent": {"max": 50},
            "proteinPercent": {"min": 8.1, "max": 9.7},
            "description": (
                "Made from the finest malting quality barley. The special "
                "manufacturing process creates an extra crunchy malt with a "
                "complex aroma profile."
            ),
            "notes": (
                "Weyermann sensory description: complex malt aroma with intense "
                "notes of biscuit and honey, nuts and chocolate."
            ),
            "provenance": {
                "primary": {
                    "publisher": "Weyermann Specialty Malts",
                    "document": "Product Specification — Weyermann® Abbey Malt®",
                    "url": "https://www.weyermann.de/abbey-malt-specification.pdf",
                    "retrieved": "2026-08-10",
                }
            },
        }
        malt_typical_record = {
            "id": "dingemans-amber-aromatic-md",
            "name": "Dingemans Amber Aromatic MD",
            "type": "grain",
            "grainGroup": "specialty",
            "maltster": "Dingemans",
            "colourLovibond": {"typical": 20},
            "extractFineGrindDryPercent": {"typical": 79},
            "moisturePercent": {"typical": 4.5},
            "diastaticPowerLintner": {"typical": 43},
        }
        malt_minimal_record = {
            "id": "mystery-malt",
            "name": "",
            "colourLovibond": {"typical": 2},
            "provenance": {
                "primary": {
                    "publisher": "Some Maltster",
                    "document": "Spec Sheet",
                    "url": "https://example.com/spec.pdf",
                    "retrieved": "2026-08-10",
                }
            },
        }
        malt_adjunct_record = {
            "id": "simpsons-flaked-oats",
            "name": "Simpsons Flaked Oats",
            "type": "adjunct",
            "grainGroup": "flaked",
            "colourLovibond": {"typical": 1.8},
            "moisturePercent": {"typical": 8},
        }
        malt_extract_record = {
            "id": "briess-lme-pilsen",
            "name": "Briess Pilsen Light LME",
            "type": "extract",
            "colourLovibond": {"typical": 1.9},
        }
        malt_broken_record = {"name": "No id"}

        malts_payload = _json.dumps(
            {
                "name": "brewing.tools open ingredient database - fermentables",
                "version": "0.7.0",
                "recordCount": 6,
                "fermentables": [
                    malt_full_record,
                    malt_typical_record,
                    malt_minimal_record,
                    malt_adjunct_record,
                    malt_extract_record,
                    malt_broken_record,
                ],
            }
        ).encode("utf-8")

        # enough malt records to clear MIN_MALT_RECORDS for the success path
        bulk_malts = [
            malt_full_record,
            malt_typical_record,
            malt_minimal_record,
            malt_adjunct_record,
            malt_extract_record,
            malt_broken_record,
        ] + [
            {
                "id": f"bulk-malt-{i}",
                "name": f"Bulk Malt {i}",
                "type": "grain",
                "colourLovibond": {"typical": 2},
            }
            for i in range(MIN_MALT_RECORDS)
        ]
        bulk_malts_payload = _json.dumps(
            {"version": "0.7.0", "fermentables": bulk_malts}
        ).encode("utf-8")

        hops = parse_dataset(hops_payload)
        assert len(hops) == 3, hops
        hops_by_id = {hop.id: hop for hop in hops}
        citra_hop = hops_by_id["citra-hbc-394"]
        # substitutes/pairings are dataset IDs -> display names; self-ref dropped
        assert citra_hop.substitutes == ["McKenzie C-148", "Triskel"], citra_hop.substitutes
        assert citra_hop.pairings == ["Triskel"], citra_hop.pairings
        assert citra_hop.dataset_version == "0.7.0", citra_hop.dataset_version
        assert hops_by_id["triskel"].origin == "", hops_by_id["triskel"].origin

        citra_doc = render_hop(citra_hop)
        assert citra_doc.startswith("# Citra HBC 394 Hops"), citra_doc[:60]
        assert "**Type:** Ingredient (Hop)" in citra_doc
        assert "**Origin:** USA" in citra_doc
        for bullet in (
            "- Alpha acid: 11–14.3%",
            "- Beta acid: 2.7–4.5%",
            "- Cohumulone: 20–35% of alpha",
            "- Total oils: up to 2.8 mL/100g",
        ):
            assert bullet in citra_doc, f"missing bullet {bullet!r}"
        assert (
            "- Oil composition: myrcene 29–66%, humulene 7.9–13.2%" in citra_doc
        ), citra_doc
        assert "geraniol" not in citra_doc, "{min:0,max:0} must be omitted"
        assert "## Substitutions\nMcKenzie C-148, Triskel" in citra_doc, citra_doc
        assert "## Aroma\ncitrus, tropical" in citra_doc, citra_doc
        assert "## Pairs well with\nTriskel" in citra_doc, citra_doc
        assert "## Profile\nBred by the Hop Breeding Company" in citra_doc, citra_doc
        assert "## Provenance\nCrosby Hops" in citra_doc, citra_doc
        assert "https://www.crosbyhops.com/shop-hops/hop-catalog/citra" in citra_doc
        assert len(citra_doc) <= 1900, len(citra_doc)

        typical_doc = render_hop(hops_by_id["mckenzie-c-148"])
        assert typical_doc.startswith("# McKenzie C-148 Hops"), typical_doc[:60]
        assert "- Alpha acid: 10.3%" in typical_doc, typical_doc
        assert "- Total oils: 2.3 mL/100g" in typical_doc, typical_doc
        assert "## Substitutions" not in typical_doc, typical_doc
        assert "## Aroma" not in typical_doc, typical_doc
        assert len(typical_doc) <= 1900, len(typical_doc)

        minimal_doc = render_hop(hops_by_id["triskel"])
        assert "**Origin:**" not in minimal_doc, minimal_doc
        assert "- Beta acid:" not in minimal_doc, minimal_doc
        assert "- Total oils: from 1.5 mL/100g" in minimal_doc, minimal_doc
        assert len(minimal_doc) <= 1900, len(minimal_doc)

        # a 5000-char pedigree is truncated inside the one-chunk budget
        long_hop = Hop(
            id="long-profile",
            name="Long Profile",
            pedigree="A long pedigree sentence. " * 200,
        )
        long_doc = render_hop(long_hop)
        assert len(long_doc) <= 1900, len(long_doc)
        assert long_doc.endswith(" …\n"), long_doc[-40:]

        # filenames: hop- prefix is the ingest contract; dataset id verbatim
        assert hop_filename(citra_hop) == "hop-citra-hbc-394.md", hop_filename(citra_hop)
        assert hop_filename(Hop()) == "hop-variety.md", hop_filename(Hop())

        # --- brewingtools malts ---
        malts = parse_malts(malts_payload)
        assert len(malts) == 4, [m.id for m in malts]
        malts_by_id = {malt.id: malt for malt in malts}
        assert "briess-lme-pilsen" not in malts_by_id, "extract record must be filtered"
        assert set(malts_by_id) == {
            "weyermann-abbey-malt",
            "dingemans-amber-aromatic-md",
            "mystery-malt",
            "simpsons-flaked-oats",
        }, set(malts_by_id)
        abbey_malt = malts_by_id["weyermann-abbey-malt"]
        assert abbey_malt.dataset_version == "0.7.0", abbey_malt.dataset_version
        assert abbey_malt.maltster == "Weyermann Specialty Malts", abbey_malt.maltster
        assert abbey_malt.group == "specialty", abbey_malt.group
        assert abbey_malt.aliases == ["Abbey Malt", "Weyermann Abbey Malt"], abbey_malt.aliases
        assert malts_by_id["simpsons-flaked-oats"].group == "flaked", (
            malts_by_id["simpsons-flaked-oats"].group
        )
        assert malts_by_id["mystery-malt"].group == "", malts_by_id["mystery-malt"].group
        assert malts_by_id["mystery-malt"].name == "Mystery Malt", (
            malts_by_id["mystery-malt"].name
        )

        abbey_doc = render_malt(abbey_malt)
        assert abbey_doc.startswith("# Abbey Malt®"), abbey_doc[:60]
        for meta_line in (
            "**Type:** Ingredient (Malt)",
            "**Maltster:** Weyermann Specialty Malts",
            "**Origin:** Germany, EU",
            "**Group:** specialty",
            "**Also known as:** Abbey Malt, Weyermann Abbey Malt",
            "**Source:** brewing.tools open ingredient database v0.7.0 (ODbL 1.0)",
        ):
            assert meta_line in abbey_doc, f"missing meta {meta_line!r}"
        for bullet in (
            "- Colour: 15.5–19.3 °L (40–50 EBC)",
            "- Extract (fine grind, dry basis): from 75%",
            "- Moisture: up to 4.5%",
            "- Max in batch: up to 50%",
            "- Protein: 8.1–9.7%",
        ):
            assert bullet in abbey_doc, f"missing bullet {bullet!r}"
        assert "## Profile\nMade from the finest malting quality barley" in abbey_doc, abbey_doc
        assert "Weyermann sensory description" in abbey_doc, abbey_doc
        assert "## Provenance\nWeyermann Specialty Malts" in abbey_doc, abbey_doc
        assert "https://www.weyermann.de/abbey-malt-specification.pdf" in abbey_doc
        for absent in ("## Substitutions", "## Pairs well with", "## Aroma"):
            assert absent not in abbey_doc, f"{absent!r} must not be rendered for malts"
        assert len(abbey_doc) <= 1900, len(abbey_doc)
        assert abbey_doc.endswith("\n") and not abbey_doc.endswith("\n\n"), abbey_doc[-40:]
        assert all(
            line == line.rstrip() for line in abbey_doc.splitlines()
        ), "trailing whitespace in malt doc"

        typical_malt_doc = render_malt(malts_by_id["dingemans-amber-aromatic-md"])
        assert typical_malt_doc.startswith("# Dingemans Amber Aromatic MD"), (
            typical_malt_doc[:60]
        )
        assert "- Colour: 20 °L" in typical_malt_doc, typical_malt_doc
        assert "EBC" not in typical_malt_doc, typical_malt_doc
        assert "- Extract (fine grind, dry basis): 79%" in typical_malt_doc, typical_malt_doc
        assert "- Diastatic power: 43 °Lintner" in typical_malt_doc, typical_malt_doc
        assert "°WK" not in typical_malt_doc, typical_malt_doc
        assert "## Profile" not in typical_malt_doc, typical_malt_doc
        assert len(typical_malt_doc) <= 1900, len(typical_malt_doc)

        minimal_malt_doc = render_malt(malts_by_id["mystery-malt"])
        for absent in ("**Maltster:**", "**Origin:**", "**Group:**"):
            assert absent not in minimal_malt_doc, minimal_malt_doc
        assert "- Colour: 2 °L" in minimal_malt_doc, minimal_malt_doc
        for absent_bullet in (
            "- Extract (fine grind, dry basis):",
            "- Moisture:",
            "- Diastatic power:",
        ):
            assert absent_bullet not in minimal_malt_doc, minimal_malt_doc
        assert "## Provenance" in minimal_malt_doc, minimal_malt_doc

        adjunct_malt_doc = render_malt(malts_by_id["simpsons-flaked-oats"])
        assert "**Group:** flaked" in adjunct_malt_doc, adjunct_malt_doc
        assert "**Type:** Ingredient (Malt)" in adjunct_malt_doc, adjunct_malt_doc

        # filenames: malt- prefix is the ingest contract
        assert malt_filename(abbey_malt) == "malt-weyermann-abbey-malt.md", malt_filename(
            abbey_malt
        )
        assert malt_filename(Malt()) == "malt-grain.md", malt_filename(Malt())

        # a 6000-char profile is truncated inside the one-chunk budget
        long_malt = Malt(
            id="long-malt",
            name="Long Malt",
            description="A long malt description. " * 200,
            notes="More long malt notes. " * 100,
        )
        long_malt_doc = render_malt(long_malt)
        assert len(long_malt_doc) <= 1900, len(long_malt_doc)
        assert long_malt_doc.endswith(" …\n"), long_malt_doc[-40:]

        # write_documents: clears its own subtree, writes only ingredients/
        bt_dest = tmp / "brewingtools_write"
        (bt_dest / "ingredients").mkdir(parents=True, exist_ok=True)
        bt_stale = bt_dest / "ingredients" / "hop-stale.md"
        bt_stale.write_text("old", encoding="utf-8")
        bt_written = write_brewingtools_documents({"hop-x.md": "# X\n"}, bt_dest)
        assert len(bt_written) == 1 and bt_written[0].is_file()
        assert not bt_stale.exists(), "stale file survived re-run"
        assert bt_written[0].parent == bt_dest / "ingredients", bt_written[0]
        assert [p.name for p in bt_dest.iterdir()] == ["ingredients"]

        # fail soft matrix: network error, non-JSON, empty/too-small dataset,
        # and the malt endpoint failing while the hop endpoint is healthy
        bt_soft_dest = tmp / "brewingtools_soft"
        bt_kept = bt_soft_dest / "ingredients" / "hop-keep.md"
        bt_malt_kept = bt_soft_dest / "ingredients" / "malt-keep.md"
        bt_kept.parent.mkdir(parents=True, exist_ok=True)
        bt_kept.write_text("# keep\n", encoding="utf-8")
        bt_malt_kept.write_text("# keep\n", encoding="utf-8")

        def _bt_offline(url, **kwargs):
            raise SourceUnavailableError(f"offline: {url}")

        def _bt_garbage(url, **kwargs):
            return b"<html>not json</html>"

        def _bt_empty_hops(url, **kwargs):
            if url == SOURCE_URL:
                return _json.dumps({"version": "0.7.0", "hops": []}).encode("utf-8")
            return malts_payload

        def _bt_too_few_hops(url, **kwargs):
            if url == SOURCE_URL:
                small = [dict(typical_record, id=f"variety-{i}") for i in range(10)]
                return _json.dumps({"version": "0.7.0", "hops": small}).encode("utf-8")
            return malts_payload

        def _bt_malts_garbage(url, **kwargs):
            if url == FERMENTABLES_URL:
                return b"<html>not json</html>"
            return bulk_hops_payload

        def _bt_malts_empty(url, **kwargs):
            if url == FERMENTABLES_URL:
                return _json.dumps(
                    {"version": "0.7.0", "fermentables": []}
                ).encode("utf-8")
            return bulk_hops_payload

        def _bt_malts_too_few(url, **kwargs):
            if url == FERMENTABLES_URL:
                small = [dict(malt_minimal_record, id=f"malt-{i}") for i in range(10)]
                return _json.dumps(
                    {"version": "0.7.0", "fermentables": small}
                ).encode("utf-8")
            return bulk_hops_payload

        original_bt_fetch = brewingtools_collector.fetch
        for label, fake_fetch in (
            ("network error", _bt_offline),
            ("non-JSON payload", _bt_garbage),
            ("empty hops list", _bt_empty_hops),
            ("too few hop records", _bt_too_few_hops),
            ("non-JSON malt payload", _bt_malts_garbage),
            ("empty fermentables list", _bt_malts_empty),
            ("too few malt records", _bt_malts_too_few),
        ):
            brewingtools_collector.fetch = fake_fetch
            try:
                try:
                    BrewingToolsCollector().collect(bt_soft_dest)
                except SourceUnavailableError:
                    pass
                else:
                    raise AssertionError(
                        f"{label}: source problem must raise SourceUnavailableError"
                    )
            finally:
                brewingtools_collector.fetch = original_bt_fetch
            assert bt_kept.is_file(), f"{label}: fail-soft run wiped prior hop output"
            assert bt_malt_kept.is_file(), f"{label}: fail-soft run wiped prior malt output"

        # success path: enough records -> hops and malts written in one run
        bt_live_dest = tmp / "brewingtools_live"
        brewingtools_collector.fetch = lambda url, **kwargs: (
            bulk_hops_payload if url == SOURCE_URL else bulk_malts_payload
        )
        try:
            bt_live_written = BrewingToolsCollector().collect(bt_live_dest)
        finally:
            brewingtools_collector.fetch = original_bt_fetch
        expected_malts = MIN_MALT_RECORDS + 4  # 4 fixture malts; extract dropped
        assert len(bt_live_written) == len(bulk_hops) + expected_malts, (
            len(bt_live_written),
            len(bulk_hops),
            expected_malts,
        )
        assert (
            bt_live_dest / "ingredients" / "hop-citra-hbc-394.md"
        ).is_file()
        assert (
            bt_live_dest / "ingredients" / "hop-triskel.md"
        ).is_file()
        assert (
            bt_live_dest / "ingredients" / "malt-weyermann-abbey-malt.md"
        ).is_file()
        assert (
            bt_live_dest / "ingredients" / "malt-simpsons-flaked-oats.md"
        ).is_file()
        assert not (
            bt_live_dest / "ingredients" / "malt-briess-lme-pilsen.md"
        ).exists(), "extract must not be written"
        assert len(
            list((bt_live_dest / "ingredients").glob("malt-*.md"))
        ) == expected_malts

        # registry
        assert "wyeast" in all_registered(), all_registered().keys()
        assert "brewingtools" in all_registered(), all_registered().keys()
        assert "hopunion" not in all_registered(), all_registered().keys()

        # naming rule: <resource>_collector.py with id = "<resource>"
        for collector_id, collector_cls in all_registered().items():
            if collector_id.startswith("smoke_"):
                continue  # synthetic collectors defined in this test module
            module_name = collector_cls.__module__.rsplit(".", 1)[-1]
            assert module_name == f"{collector_id}_collector", (
                f"{collector_id} must live in {collector_id}_collector.py, "
                f"found {module_name}.py"
            )

        print("PASS: registry, runner statuses (OK/SKIP/FAIL/MISSING),")
        print("      namespace rule, HTML -> markdown conversion,")
        print("      diydog zip extraction (idempotent, xml only),")
        print("      bjcp style rendering (sections, type/tags, aliases, payload),")
        print("      whitelabs strain parsing/rendering (specs, styles, fail-soft),")
        print("      wyeast sitemap/culture parsing (multi-tab dedupe, bacteria,")
        print("      -PC availability, filename/one-chunk contract, fail-soft),")
        print("      brewingtools hop dataset (range quirks, id->name resolution,")
        print("      one-chunk rendering, hop- filenames, fail-soft),")
        print("      brewingtools malt dataset (colour L/EBC as published, extract/")
        print("      moisture/protein/DP bands, extracts excluded, group fallback,")
        print("      malt- filenames, one-chunk rendering, both-or-nothing fail-soft,")
        print("      naming rule)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
