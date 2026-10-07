"""The SEO landing page → tool deep link.

/admin/stats measured page_view → tool_open at 12%: visitors arrived on a tool
page from search, clicked the CTA, and landed on a *category* grid where they
had to find the tool a second time. The CTA now carries `&op=<slug>`, which
static/script.js resolves to the specific action card.

The contract spans two files that no compiler checks against each other, so
these tests hold the seam: every page's CTA must name an op script.js knows,
and every op must name a card index.html actually has.
"""
import re
from pathlib import Path

import pytest

from scripts import seo_content

STATIC = Path(__file__).resolve().parent.parent / "static"
SCRIPT_JS = (STATIC / "script.js").read_text(encoding="utf-8")
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")

_OP_RE = re.compile(r"^    '([a-z0-9-]+)': \{ card: '([a-z0-9-]+)'(?:, mode: '([a-z0-9-]+)')?(?:, lang: '([a-z0-9-]+)')? \},$", re.M)
DEEP_LINK_OPS = {m.group(1): {"card": m.group(2), "mode": m.group(3), "lang": m.group(4)} for m in _OP_RE.finditer(SCRIPT_JS)}

ACTION_CARD_IDS = set(re.findall(r'class="action-card[^"]*" id="([a-z0-9-]+)"', INDEX_HTML))
ELEMENT_IDS = set(re.findall(r'id="([a-z0-9-]+)"', INDEX_HTML))


def test_deep_link_ops_were_parsed():
    """Guard the guards: if the DEEP_LINK_OPS format changes, these tests must
    fail loudly rather than silently asserting over an empty dict."""
    assert len(DEEP_LINK_OPS) >= 30
    assert ACTION_CARD_IDS, "no action cards found in index.html"


@pytest.mark.parametrize("slug", sorted(seo_content.TOOL_PAGES))
def test_cta_deep_links_to_the_specific_tool(slug):
    html = seo_content.render_tool_page(slug)
    cta = re.search(r'<a class="cta" href="([^"]+)"', html)
    assert cta, f"{slug} has no CTA link"
    href = cta.group(1)
    assert href == f"/?tool={seo_content.TOOL_PAGES[slug]['tool']}&amp;op={slug}", (
        f"{slug} CTA must deep-link to its own tool, got {href}"
    )


@pytest.mark.parametrize("slug", sorted(seo_content.TOOL_PAGES))
def test_every_tool_page_has_a_deep_link_target(slug):
    """A page whose op script.js doesn't know still opens the category, so this
    can't break the site — it just silently gives up the conversion win."""
    assert slug in DEEP_LINK_OPS, (
        f"{slug} has no DEEP_LINK_OPS entry in static/script.js — its CTA would "
        f"drop visitors on the category grid"
    )


@pytest.mark.parametrize("slug,op", sorted(DEEP_LINK_OPS.items()))
def test_deep_link_targets_exist_in_the_dom(slug, op):
    assert op["card"] in ACTION_CARD_IDS, f"{slug} → unknown action card {op['card']}"
    if op["mode"]:
        assert op["mode"] in ELEMENT_IDS, f"{slug} → unknown mode input {op['mode']}"


def test_no_stale_deep_link_entries():
    """An op with no page behind it is dead config that will drift."""
    assert set(DEEP_LINK_OPS) <= set(seo_content.TOOL_PAGES)


def test_cta_appears_before_the_ad_slot():
    """The CTA is the conversion action — it has to be above the fold, and the
    ad block is what would otherwise push it below on mobile."""
    html = seo_content.render_tool_page("pdf-to-word")
    assert html.index('class="cta"') < html.index(seo_content.ADS_SLOT)


def test_landing_pages_do_not_load_the_app_bundle():
    """These pages rank because they render fully without JS, so the SPA bundle
    must stay off them. static/seo-upload.js is deliberately not that: it is a
    few KB whose only job is to hand a chosen file to the app, and the page
    degrades to the plain deep link without it (see the noscript test below)."""
    html = seo_content.render_tool_page("pdf-to-word")
    assert "/static/script.js" not in html
    assert '"/script.js"' not in html


@pytest.mark.parametrize("slug", sorted(seo_content.TOOL_PAGES))
def test_landing_pages_still_work_without_javascript(slug):
    """The upload box needs JS to carry the file across the navigation, so
    every page must still offer the plain link when there is none. Ranking
    depends on these pages being complete without scripting."""
    html = seo_content.render_tool_page(slug)
    tool = seo_content.TOOL_PAGES[slug]["tool"]
    assert f'<a class="cta" href="/?tool={tool}&amp;op={slug}"' in html


@pytest.mark.parametrize(
    "slug", sorted(s for s in seo_content.TOOL_PAGES
                   if seo_content.TOOL_PAGES[s]["tool"] in seo_content._CATEGORY_ACCEPT)
)
def test_tool_pages_lead_with_an_upload_box(slug):
    """The landing page used to offer only a link to the app, so the first
    upload box a visitor saw was a full page load away — 89% of landing
    sessions never opened a tool at all. The page's primary action is now
    giving it a file."""
    html = seo_content.render_tool_page(slug)
    assert "data-ff-upload" in html, f"{slug} has no upload box"
    assert f'data-ff-target="/?tool={seo_content.TOOL_PAGES[slug]["tool"]}&amp;op={slug}"' in html
    # Above the ad block, which is what would otherwise push it below the fold.
    assert html.index("data-ff-upload") < html.index(seo_content.ADS_SLOT)


@pytest.mark.parametrize("slug", sorted(seo_content._MULTI_FILE_SLUGS))
def test_merge_pages_have_multi_file_upload_box(slug):
    """Merge tools support uploading and dropping multiple files directly on the landing page."""
    html = seo_content.render_tool_page(slug)
    assert "data-ff-upload" in html, f"{slug} must have an upload box"
    assert ' multiple>' in html, f"{slug} upload box must allow multiple files"
    assert 'class="upload-cta-input"' in html
    assert ' multiple hidden>' not in html, "the file picker must remain keyboard accessible"
    assert "or drop files here" in html, f"{slug} upload box must have multi-file hint text"


@pytest.mark.parametrize("slug", sorted(seo_content.TOOL_PAGES))
def test_upload_box_accepts_what_its_category_accepts(slug):
    """A picker offering the wrong types is a dead end the visitor can only
    discover by choosing a file and being rejected."""
    html = seo_content.render_tool_page(slug)
    if "data-ff-upload" not in html:
        return
    tool = seo_content.TOOL_PAGES[slug]["tool"]
    accept = seo_content._CATEGORY_ACCEPT[tool]
    assert f'accept="{accept}"' in html
    # Must match the app-side input the handed-off file will land in.
    input_id = {"pdf": "file-input", "image": "image-file-input",
                "excel": "excel-file-input", "ppt": "ppt-file-input",
                "word": "word-file-input"}[tool]
    assert f'<input type="file" id="{input_id}" accept="{accept}" hidden>' in INDEX_HTML


def test_handoff_categories_match_between_the_two_files():
    """seo-upload.js stashes the file and script.js claims it into a category's
    input. Nothing checks these two across files but this."""
    handoff_js = (STATIC / "seo-upload.js").read_text(encoding="utf-8")
    # Both halves must agree on the IndexedDB database, store and key.
    for token in ("ff_handoff", "files", "pending"):
        assert token in handoff_js, f"{token} missing from seo-upload.js"
        assert token in SCRIPT_JS, f"{token} missing from script.js"

    claimed = set(re.findall(r"^\s{4}(\w+): '([a-z-]+)',$", SCRIPT_JS, re.M))
    categories = {c for c, _ in claimed}
    for tool in seo_content._CATEGORY_ACCEPT:
        assert tool in categories, f"script.js cannot claim a handoff for {tool}"


def test_every_public_web_tool_has_a_landing_page():
    """A tool reachable from the app but absent from TOOL_PAGES is invisible to
    search and to sitemap.xml/llms.txt. Premium and workflow surfaces are exempt."""
    from scripts.tool_registry import TOOL_REGISTRY

    missing = [
        t["id"] for t in TOOL_REGISTRY
        if t["web_card"] and not t["premium"] and t["category"] != "workflow" and not t["seo_slugs"]
    ]
    assert not missing, f"tools without an SEO landing page: {missing}"
    for t in TOOL_REGISTRY:
        for slug in t["seo_slugs"]:
            assert slug in seo_content.TOOL_PAGES, (t["id"], slug)
