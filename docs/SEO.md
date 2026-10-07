# SEO architecture

How Forge Files gets found — by Google **and** by JS-less AI crawlers (GPTBot,
ClaudeBot, PerplexityBot, Google-Extended). Everything here is implemented in the
app itself; no external service or build step is required.

## The core idea

Every tool has its own **server-rendered** landing page at a clean URL
(`/merge-pdf`, `/pdf-to-word`, …). "Server-rendered" is the important part: the
title, meta description, `<h1>`, body copy and FAQ are all in the **raw HTML**, so
crawlers that don't run JavaScript (most AI crawlers) read the full page. The
interactive tool itself still lives on the homepage SPA; the landing page links
into it via `/?tool=<category>`.

## Where things live

| Concern | File |
|---|---|
| Tool-page content + HTML/JSON-LD generator | [`scripts/seo_content.py`](scripts/seo_content.py) |
| Routing, sitemap, robots, 404, AdSense substitution | [`main.py`](main.py) (`serve_seo_page`, `sitemap_xml`, `robots_txt`, `http_exception_handler`, `_substitute`) |
| Homepage `<head>`, schema, footer links, ad slot | [`static/index.html`](static/index.html) |
| Content pages (about/faq/contact/privacy/terms) | `static/pages/*.html` |

## Add a new tool landing page (one entry)

1. Add an entry to `TOOL_PAGES` in `scripts/seo_content.py`:

   ```python
   "rotate-pdf": {
       "title": "Rotate PDF — … | Forge Files",   # keep ≤ 60 chars
       "meta":  "…",                              # keep ≤ 160 chars, action verbs + privacy hook
       "h1": "…", "lede": "…",
       "tool": "pdf",            # deep-link category: pdf|image|excel|ppt|word
       "app": "Rotate PDF",      # SoftwareApplication name + breadcrumb label
       "cta": "Rotate a PDF now — free",
       "how": "How to rotate a PDF",
       "steps": ["…", "…", "…"],            # also become HowTo schema
       "benefits": ["<strong>…</strong> …"],
       "faqs": [("Question?", "Answer (may contain <a> links).")],
       "related": ["organize-pdf", "merge-pdf"],   # must be existing slugs
   },
   ```

2. Add a `HELP` entry for the slug in `scripts/tool_extra.py`, a `seo_slugs` entry for the tool in
   `scripts/tool_registry.json`, and the slug to `DEEP_LINK_OPS` in `static/script.js` (tests enforce all three).
3. That's it. The page is automatically served at `/rotate-pdf`, added to
   `sitemap.xml`, and gets `FAQPage` + `SoftwareApplication` + `HowTo` +
   `BreadcrumbList` JSON-LD. The footer and `ItemList` JSON-LD in `static/index.html` are hand-maintained —
   add the link there too if you want it sitewide.

**Rules:** titles ≤ 60 chars, metas ≤ 160 chars (enforced by
`tests/test_auth.py` indirectly; check with the snippet in "Verify" below). Keep
copy **honest** — files are processed **server-side** and deleted after download;
do not claim client-side / "files never leave your browser".

## AI crawlers & 404s

- **`robots.txt`** explicitly names AI retrieval bots (`AI_CRAWLERS` in `main.py`)
  with `Allow: /` so Forge Files can be cited in AI answers, plus an absolute
  `Sitemap:` URL.
- **404s are hard** (HTTP 404) with a branded HTML body and links to popular
  tools — never a soft-404. API routes (`/api/*`) keep JSON errors.
- If you ever put **Cloudflare or any WAF** in front of the app (the `forgefiles.org`
  setup proxies Cloudflare to the origin), allow those same bot user-agents at the
  edge — otherwise they get a `403` before reaching us and the robots.txt allow-list
  is moot. The exact Cloudflare WAF custom-rule expression, the "keep 404s hard at
  the edge" rules, and the verification `curl` checks live in
  [`docs/cloudflare-edge.md`](cloudflare-edge.md).

## AdSense (optional, env-gated)

Set `ADSENSE_CLIENT` (and optionally `ADSENSE_SLOT`). When unset, **no ad markup
is emitted at all**. When set, the loader is async, ad units sit in fixed
reserved-height `.ad-slot` containers (CLS ≈ 0), and are filled lazily via an
`IntersectionObserver` only as they near the viewport. `ads.txt` is served from
`ADSENSE_ADS_TXT`.

## Search Console & Bing verification (optional, env-gated)

Set `GOOGLE_SITE_VERIFICATION` and/or `BING_SITE_VERIFICATION` to the token (just the
token, not the whole `<meta>` tag) from Google Search Console / Bing Webmaster Tools.
When set, the matching verification meta tag is injected into the `<head>` of the
homepage **and** every server-rendered tool page via the `{{SITE_VERIFICATION}}`
token (substituted in `main.py:_substitute`). When unset, no markup is emitted. This
lets you verify domain ownership without a code change, then submit
`https://<your-domain>/sitemap.xml` in each console.

## Verify

```bash
# all pages render, JSON-LD parses, titles ≤60 / metas ≤160
python -c "import re,json; from scripts import seo_content as sc; \
[ (json.loads(m) for m in re.findall(r'ld\\+json\">(.*?)</script>', sc.render_tool_page(s), re.S)) for s in sc.TOOL_PAGES ]; \
print('over-limit:', [(s, len(p['title']), len(p['meta'])) for s,p in sc.TOOL_PAGES.items() if len(p['title'])>60 or len(p['meta'])>160])"

python -m pytest tests/test_auth.py -q
```

## Out of scope (manual)

Domain purchase, the AdSense account + `ads.txt` line, GitHub repo topics, and
off-page work (Reddit/HN launch, backlinks) are not code and live outside this repo.
