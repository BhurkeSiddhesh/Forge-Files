from typing import Any, Callable, List, Optional
from fastapi import FastAPI, BackgroundTasks
from fastapi import UploadFile, File, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
import asyncio
import os
import re
import secrets
import shutil
import uuid
import html
import hmac
import json
import logging
import mimetypes
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar, Token
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

# ES modules (vendored pdf.js is .mjs) are refused by browsers unless served as
# a JavaScript MIME type, and the platform mime tables don't reliably know .mjs
# (Windows and minimal Linux images answer text/plain). Register before the
# static mount below is created.
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")

# --- Logging Setup ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logger = logging.getLogger("file_forge")
logger.setLevel(LOG_LEVEL)

# Ensure logs are emitted if no parent handlers exist (e.g., when running directly)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)

from fastapi.concurrency import run_in_threadpool
from scripts.pdf_utils import (
    ServerDependencyError,
    remove_pdf_password,
    pdf_to_docx,
    pdf_to_word_ai,
    extract_pdf_pages,
    split_pdf_to_zip,
    compress_pdf,
    merge_pdfs,
    add_watermark,
    crop_pdf,
    pdf_to_images_zip,
    sign_pdf,
    rotate_pdf,
    protect_pdf,
    images_to_pdf,
    word_to_pdf,
    word_to_pptx,
    pdf_to_excel,
    pdf_to_pptx,
    pdf_to_epub,
    extract_text_from_pdf,
    ocr_pdf_to_searchable_pdf,
    organize_pdf,
    add_page_numbers,
    repair_pdf,
    create_pdf_from_text,
    create_blank_pdf,
    annotate_pdf,
    edit_pdf_metadata,
    get_pdf_metadata,
)
from scripts.image_utils import (
    heic_to_jpeg,
    rotate_image,
    compress_image,
    convert_image_format,
    watermark_image,
    MAX_RESIZE_DIMENSION,
)
from scripts.excel_utils import (
    excel_to_pdf,
    csv_to_xlsx,
    xlsx_to_csv,
    merge_excel_files,
)
from scripts.ppt_utils import (
    ppt_to_pdf,
    ppt_to_images_zip,
    merge_pptx,
)
from scripts import seo_content
from scripts import blog_content
from scripts import event_log
from scripts import ga4_mp

PROD = os.environ.get("ENV") == "production"


# --- Application lifespan ---
# Replaces the deprecated @app.on_event("startup") hooks. Defined here because
# FastAPI() takes the lifespan at construction time; the helpers it calls
# (`cleanup_stale_files_loop`, `_warmup_ai`) are defined further down and are
# resolved when the app boots, not when this function is defined.
#
# A deployment that mounts extra routers onto this app and needs its own startup
# work must *wrap* this context manager, not assign over `lifespan_context` —
# unlike the on_event hooks it replaces, a lifespan does not accumulate, and
# replacing it would silently drop the stale-file sweeper (a privacy guarantee).
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the stale-file sweeper (and optionally warm AI models), then stop it."""
    _assert_single_worker()
    _warn_if_edge_unauthenticated()
    # Hold a reference: a bare create_task() may be garbage-collected mid-flight.
    sweeper = asyncio.create_task(cleanup_stale_files_loop())
    await _warmup_ai()
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        # The event log holds one long-lived SQLite write connection.
        event_log.close_connections()


app = FastAPI(
    title="Forge Files API",
    docs_url=None if PROD else "/docs",
    redoc_url=None if PROD else "/redoc",
    openapi_url=None if PROD else "/openapi.json",
    lifespan=lifespan,
)

# --- CORS ---
# The web frontend is served same-origin (no CORS needed there), but the
# Capacitor mobile app loads its assets from capacitor://localhost (iOS) and
# https://localhost (Android) and calls this API cross-origin. Allow those
# origins plus any explicitly configured web origins (comma-separated in
# CORS_EXTRA_ORIGINS, e.g. the production site domain).
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

# The origins Capacitor actually loads from. "https" is Capacitor's default
# androidScheme (since Capacitor 4; this repo is on @capacitor/android ^8.5.0)
# and mobile/capacitor.config.ts sets no `server` block, so the shipped Android
# WebView origin is https://localhost — dropping it took the whole Android app
# offline. "http://localhost" is the Capacitor 3 default, kept for older
# installs. Both are port-less, so they match only :443/:80 and never served
# local development; the spoofing concern applies to them equally, which is why
# allow_credentials is off below rather than the origin being removed.
_CORS_ORIGINS = [
    "capacitor://localhost",
    "https://localhost",
    "http://localhost",
]
_CORS_ORIGINS += [
    o.strip()
    for o in os.environ.get("CORS_EXTRA_ORIGINS", "").split(",")
    if o.strip()
]

# The API is not cookie-authenticated: the mobile app sends a bearer token in
# the Authorization header, and ff_sid is an anonymous analytics id the server
# sets itself — nothing cross-origin reads it or depends on it being sent.
# Leaving allow_credentials on meant a page served from http://localhost could
# make credentialed calls whose responses it could actually read. Off by
# default; CORS_ALLOW_CREDENTIALS=1 restores the old behaviour if some caller
# turns out to need it.
_CORS_ALLOW_CREDENTIALS = os.environ.get("CORS_ALLOW_CREDENTIALS", "0") == "1"

# Baseline security headers on every response. Full script-src CSP is a
# follow-up: the tool pages, checkout, and AdSense bootstrap still use
# inline <script> / style. frame-ancestors + X-Frame-Options cover the
# clickjacking gap on /checkout today (#77).
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Cross-Origin-Opener-Policy": "same-origin",
}


@app.middleware("http")
async def canonical_host_middleware(request: Request, call_next):
    """Redirect the apex domain to the canonical www host.

    Cloudflare proxies both hostnames to the same origin, so this application
    layer fallback keeps direct-origin requests canonical as well. A permanent
    308 preserves the request method for any API clients that use the apex.
    """
    if request.url.hostname and request.url.hostname.lower() == "forgefiles.org":
        target = request.url.replace(scheme="https", hostname="www.forgefiles.org")
        return RedirectResponse(url=str(target), status_code=308)
    return await call_next(request)


# On-device Office engine (LibreOffice WASM) needs SharedArrayBuffer, so its page
# must be cross-origin isolated: COOP same-origin (already sent everywhere) plus
# COEP require-corp. Only this dedicated, ad-free route and the engine and viewer
# files it loads get COEP; the rest of the site is untouched because COEP can
# block third-party ads. Worker scripts must carry COEP themselves, which is why
# the vendor directories are listed here and not just the page.
_ISOLATED_PREFIXES = (
    "/on-device-office",
    "/static/on-device-office/",
    "/static/vendor/lo-wasm/",
    "/static/vendor/pdfjs/",
    "/static/vendor/jszip.min.js",
)
_ENGINE_PREFIX = "/static/vendor/lo-wasm/"


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    path = request.url.path
    if path.startswith(_ISOLATED_PREFIXES):
        response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if path.startswith(_ENGINE_PREFIX) and response.status_code == 200 and not path.endswith("MANIFEST.json"):
            # Version-pinned directory: safe to cache for a year.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
    if proto == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=_CORS_ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    # Content-Disposition is not a CORS-safelisted response header, so without
    # this the app cannot read the filename off a download it fetched itself.
    # /api/download addresses results by an opaque token on purpose, so the name
    # the user should actually see exists ONLY in this header — and the app has
    # to fetch and save the bytes by hand, because a WebView has no download
    # manager (see mobile/app-assets/src/native-download.js). Without it every
    # saved file would be called "forgefiles-download".
    expose_headers=["Content-Disposition"],
)

# --- Configuration ---
BASE_URL = os.environ.get("BASE_URL", "https://www.forgefiles.org").rstrip("/")
# Stable sitemap <lastmod>. Tool/content pages are static, so reporting today's
# date on every request is inaccurate and teaches crawlers to distrust the field
# (wasting crawl budget). Bump CONTENT_LAST_MODIFIED (or set the env var on a
# real content change) so the sitemap reflects the true last edit, not "now".
CONTENT_LAST_MODIFIED = os.environ.get("CONTENT_LAST_MODIFIED", "2026-09-26").strip()
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
# Multi-file endpoints (the merge tools, images->PDF) share one budget across the
# whole request: a per-file cap alone would let a single request write
# MAX_UPLOAD_MB x N. Defaults to MAX_UPLOAD_MB so there is one number to tune.
MAX_UPLOAD_TOTAL_MB_ENV = os.environ.get("MAX_UPLOAD_TOTAL_MB", "").strip()
DISABLE_AI = os.environ.get("DISABLE_AI", "0") == "1"
FILE_TTL_SECONDS = int(os.environ.get("FILE_TTL_SECONDS", "3600"))
# Bounds how many steps a single /api/workflow/execute request can chain, so a
# caller can't pair a huge step list with the heavy-tier rate limit to pin the
# server on one "request".
MAX_WORKFLOW_STEPS = int(os.environ.get("MAX_WORKFLOW_STEPS", "20"))

# --- Google AdSense (optional, fully env-gated) ---
# When ADSENSE_CLIENT is unset, every ad placeholder renders empty — zero markup,
# zero performance impact. Ads load asynchronously and lazily (IntersectionObserver),
# inside fixed reserved-height containers so they never cause layout shift (CLS).
ADSENSE_CLIENT = os.environ.get("ADSENSE_CLIENT", "").strip()  # e.g. ca-pub-1234567890123456
ADSENSE_SLOT = os.environ.get("ADSENSE_SLOT", "").strip()      # numeric data-ad-slot (optional)


def _build_adsense_head() -> str:
    if not ADSENSE_CLIENT:
        return ""
    # Consent-gated (Google Consent Mode v2): storage/personalization default to
    # 'denied' before the AdSense script runs, and the lazy ad-fill only happens
    # once the visitor accepts via the consent banner (or on a prior 'granted'
    # choice persisted in localStorage). The banner calls window.__ffConsentInit()
    # on accept to fill any ads already in view. Declining leaves ads unfilled.
    #
    # Ad-free gate (build-prompt task 4.5): before filling, we make a best-effort
    # call to the backend-controlled GET /api/me and skip ads entirely (hiding the
    # reserved .ad-slot boxes) when features.ad_free is true. The public app only
    # ever reads features.ad_free — never entitlement internals.
    #
    # The session token comes from window.__ffSession, which static/session.js
    # populates from the record the auth layer writes on sign-in. That file used
    # not to exist: the global was read here and set nowhere, so every "Ad-Free
    # Forever" purchase resolved to "show ads" forever. It is loaded here rather
    # than from the page templates because {{ADSENSE_HEAD}} is the one token every
    # page carries, and because it must run before this script's DOMContentLoaded
    # init() — a customer should never see an ad flash before the gate resolves.
    # It is also only needed where ads exist, which is exactly where this renders.
    #
    # With no session — the free-launch default, payments off — the gate resolves
    # to "show ads", so behaviour is identical to before.
    return (
        '<link rel="preconnect" href="https://pagead2.googlesyndication.com" crossorigin>\n'
        '    <script src="/static/session.js?v=20260802"></script>\n'
        '    <script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
        "gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',"
        "ad_personalization:'denied',analytics_storage:'denied',wait_for_update:500});</script>\n"
        '    <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client='
        + ADSENSE_CLIENT + '" crossorigin="anonymous"></script>\n'
        '    <script>\n'
        "    (function(){var K='ff_consent';function granted(){return localStorage.getItem(K)==='granted';}"
        'function fill(el){try{(adsbygoogle=window.adsbygoogle||[]).push({});'
        "el.setAttribute('data-lazy-filled','1');}catch(e){}}"
        "function hideSlots(){var s=document.querySelectorAll('.ad-slot');"
        'for(var i=0;i<s.length;i++){s[i].style.display=\'none\';}}'
        'function adFree(cb){'
        # A cached positive answers synchronously: an expired access token must
        # not put ads back in front of someone who bought a lifetime removal.
        'if(window.__ffAdFreeHint&&window.__ffAdFreeHint()===true){cb(true);return;}'
        'var t=(window.__ffSession&&window.__ffSession.access_token);'
        'if(!t){cb(false);return;}'
        "var u=(window.apiUrl?window.apiUrl('/api/me'):'/api/me');"
        "fetch(u,{headers:{Authorization:'Bearer '+t}})"
        '.then(function(r){return r.ok?r.json():null;})'
        '.then(function(d){var af=!!(d&&d.features&&d.features.ad_free);'
        'if(window.__ffCacheAdFree)window.__ffCacheAdFree(af);cb(af);})'
        '.catch(function(){cb(false);});}'
        "function runFill(){if(!granted())return;"
        "var ads=document.querySelectorAll('ins.adsbygoogle:not([data-lazy-filled])');"
        "if(!('IntersectionObserver' in window)){ads.forEach(fill);return;}"
        'var io=new IntersectionObserver(function(es){es.forEach(function(e){'
        "if(e.isIntersecting){io.unobserve(e.target);fill(e.target);}});},{rootMargin:'250px'});"
        'ads.forEach(function(a){io.observe(a);});}'
        'function init(){adFree(function(af){if(af){hideSlots();return;}runFill();});}'
        'window.__ffConsentInit=init;'
        "if(document.readyState!=='loading'){init();}else{document.addEventListener('DOMContentLoaded',init);}})();\n"
        '    </script>'
    )


def _build_consent_banner() -> str:
    """Cookie-consent banner shown until the visitor accepts/declines.

    Only emitted when ADSENSE_CLIENT is set — with no ads there are no ad cookies
    to consent to, so the banner (like the ad markup) is zero-markup when unset.
    This is the consent mechanism AdSense requires before serving personalized
    ads (build-prompt task 4.6). Accepting flips Consent Mode to 'granted' and
    triggers the lazy ad-fill; declining persists the choice and leaves ads off.
    """
    if not ADSENSE_CLIENT:
        return ""
    return (
        '<div id="ff-consent" role="dialog" aria-live="polite" aria-label="Cookie consent" hidden'
        ' style="position:fixed;left:0;right:0;bottom:0;z-index:9999;display:none;flex-wrap:wrap;'
        'gap:12px;align-items:center;justify-content:center;padding:14px 18px;'
        'background:#181b22;color:#e8eaed;border-top:1px solid #262b35;'
        'font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif">'
        '<span style="max-width:640px">We use cookies to serve ads that keep Forge Files free. '
        'See our <a href="/privacy" style="color:#4f8cff">Privacy Policy</a>.</span>'
        '<span style="display:flex;gap:8px">'
        '<button id="ff-consent-decline" type="button" style="cursor:pointer;border:1px solid #262b35;'
        'border-radius:8px;padding:8px 16px;background:transparent;color:#e8eaed;font:inherit">Decline</button>'
        '<button id="ff-consent-accept" type="button" style="cursor:pointer;border:0;'
        'border-radius:8px;padding:8px 16px;background:#4f8cff;color:#fff;font:inherit;font-weight:600">Accept</button>'
        '</span></div>\n'
        '<script>(function(){'
        "var K='ff_consent',b=document.getElementById('ff-consent');if(!b)return;"
        'var c=null;try{c=localStorage.getItem(K);}catch(e){}'
        'if(!c){b.hidden=false;b.style.display="flex";}'
        'function choose(v){try{localStorage.setItem(K,v);}catch(e){}b.hidden=true;b.style.display="none";'
        "if(v==='granted'){try{gtag('consent','update',{ad_storage:'granted',ad_user_data:'granted',"
        "ad_personalization:'granted',analytics_storage:'granted'});}catch(e){}"
        'if(window.__ffConsentInit)window.__ffConsentInit();}}'
        "var a=document.getElementById('ff-consent-accept');if(a)a.onclick=function(){choose('granted');};"
        "var d=document.getElementById('ff-consent-decline');if(d)d.onclick=function(){choose('denied');};"
        '})();</script>'
    )


def _build_adsense_slot() -> str:
    if not ADSENSE_CLIENT:
        return ""
    slot_attr = f' data-ad-slot="{ADSENSE_SLOT}"' if ADSENSE_SLOT else ""
    return (
        '<div class="ad-slot" role="complementary" aria-label="Advertisement">'
        '<span class="ad-label">Advertisement</span>'
        '<ins class="adsbygoogle" style="display:block"'
        f' data-ad-client="{ADSENSE_CLIENT}"{slot_attr}'
        ' data-ad-format="auto" data-full-width-responsive="true"></ins>'
        '</div>'
    )


ADSENSE_HEAD_HTML = _build_adsense_head()
ADSENSE_SLOT_HTML = _build_adsense_slot()
CONSENT_BANNER_HTML = _build_consent_banner()

# --- Search engine site verification (optional, fully env-gated) ---
# Paste the token (not the whole meta tag) from Google Search Console / Bing
# Webmaster Tools. When unset, no verification markup is emitted.
GOOGLE_SITE_VERIFICATION = os.environ.get("GOOGLE_SITE_VERIFICATION", "").strip()
BING_SITE_VERIFICATION = os.environ.get("BING_SITE_VERIFICATION", "").strip()


def _build_site_verification() -> str:
    tags = []
    if GOOGLE_SITE_VERIFICATION:
        tags.append(
            f'<meta name="google-site-verification" content="{html.escape(GOOGLE_SITE_VERIFICATION, quote=True)}">'
        )
    if BING_SITE_VERIFICATION:
        tags.append(
            f'<meta name="msvalidate.01" content="{html.escape(BING_SITE_VERIFICATION, quote=True)}">'
        )
    return "\n    ".join(tags)


SITE_VERIFICATION_HTML = _build_site_verification()

# --- Cloudflare Web Analytics (optional, fully env-gated) ---
# Paste the site token from the Cloudflare dashboard (Web Analytics → your site →
# "JS snippet" — it's the value of data-cf-beacon's "token"). When unset, no
# beacon is emitted, so there is zero third-party JS and zero performance cost.
#
# Cloudflare Web Analytics is cookieless and privacy-first: it records page views
# and Core Web Vitals only (no custom events). That already gives the page-to-page
# navigation funnel (home → tool page → …). The *"did they actually process a
# file"* funnel steps are emitted client-side via window.zaraz.track() in
# script.js — those show up in Cloudflare when Zaraz is enabled on the zone, and
# are silent no-ops otherwise, so nothing here depends on Zaraz being present.
CLOUDFLARE_ANALYTICS_TOKEN = os.environ.get("CLOUDFLARE_ANALYTICS_TOKEN", "").strip()


def _build_cf_analytics() -> str:
    if not CLOUDFLARE_ANALYTICS_TOKEN:
        return ""
    token = json.dumps(CLOUDFLARE_ANALYTICS_TOKEN)  # JSON-safe, quoted
    return (
        '<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
        f"data-cf-beacon='{{\"token\": {token}}}'></script>"
    )


CF_ANALYTICS_HTML = _build_cf_analytics()

# --- Google Analytics 4 (optional, fully env-gated) ---
# Set GA_MEASUREMENT_ID (or GOOGLE_ANALYTICS_ID) to a GA4/Google tag id such as
# G-XXXXXXXXXX. The open-source repo deliberately does not hardcode a property
# id: self-hosters should not leak traffic into the production Forge Files
# analytics account, and local/dev runs should stay tracker-free by default.
#
# Only anonymous interaction intent is sent by static/script.js: page path, tool
# category, selected operation, processing success and download click. Never send
# file names, document contents, emails, order ids, or user ids to GA.
GA_MEASUREMENT_ID = (
    os.environ.get("GA_MEASUREMENT_ID", "").strip()
    or os.environ.get("GOOGLE_ANALYTICS_ID", "").strip()
)
_GA_TAG_RE = re.compile(r"^(?:G|GT)-[A-Za-z0-9_-]{4,64}$")


def _build_ga_analytics() -> str:
    if not GA_MEASUREMENT_ID:
        return ""
    if not _GA_TAG_RE.fullmatch(GA_MEASUREMENT_ID):
        logger.warning("Ignoring invalid GA_MEASUREMENT_ID value")
        return ""
    tag_id_json = json.dumps(GA_MEASUREMENT_ID)
    tag_id_attr = html.escape(GA_MEASUREMENT_ID, quote=True)
    return (
        '<link rel="preconnect" href="https://www.googletagmanager.com" crossorigin>\n'
        f'    <script async src="https://www.googletagmanager.com/gtag/js?id={tag_id_attr}"></script>\n'
        '    <script>\n'
        '    (function(){\n'
        '      window.dataLayer=window.dataLayer||[];\n'
        '      window.gtag=window.gtag||function(){window.dataLayer.push(arguments);};\n'
        f'      var tagId={tag_id_json};\n'
        "      window.gtag('js',new Date());\n"
        "      window.gtag('config',tagId,{send_page_view:false});\n"
        '      function pageView(path,title,locationHref){try{window.gtag(\'event\',\'page_view\',{'
        "page_path:path||window.location.pathname||'/',"
        'page_title:title||document.title,'
        'page_location:locationHref||window.location.href,'
        "transport_type:'beacon'});}catch(e){}}\n"
        '      function event(name,label){try{'
        "if(name==='page_view'){"
        "var p=(label&&typeof label==='object')?(label.page_path||window.location.pathname||'/'):(label||window.location.pathname||'/');"
        "pageView(p);return;}"
        "var p=(label&&typeof label==='object')?Object.assign({},label):{event_category:'intent',event_label:label||'',tool_label:label||'',tool_name:label||''};"
        "if(!p.transport_type){p.transport_type='beacon';}"
        "window.gtag('event',name,p);"
        '}catch(e){}}\n'
        '      window.ffAnalytics={pageView:pageView,event:event};\n'
        '      if(!window.FF_GA_MANUAL_PAGEVIEW){pageView(window.location.pathname||\'/\');}\n'
        '    })();\n'
        '    </script>'
    )


GA_ANALYTICS_HTML = _build_ga_analytics()

AI_DISABLED_MESSAGE = (
    "AI Layout Recovery is disabled on this server (it needs more memory than the "
    "free hosting tier provides). Uncheck 'Use AI Layout Recovery' to use the standard converter."
)

# pdf_to_word_ai reports which conversion path it actually took via
# method_callback, so the response message reflects what happened instead of
# always claiming "AI Layout Recovery" even when OCR never ran (or ran
# without real layout support - see scripts/pdf_utils.py:pdf_to_word_ai).
_AI_METHOD_MESSAGES = {
    "text_layer": "Converted to Word (used the PDF's existing text layer — AI OCR wasn't needed)",
    "paddle_layout": "Converted to Word with AI Layout Recovery",
    "ocr_hybrid": "Converted to Word with AI Layout Recovery (OCR applied only to scanned pages)",
    "ocr_fallback": "Converted to Word with AI OCR (layout recovery isn't available on this server)",
}


def _ai_conversion_message(method: Optional[str]) -> str:
    return _AI_METHOD_MESSAGES.get(method, "Converted to Word with AI Layout Recovery")

async def _warmup_ai():
    """Load the OCR backend at boot (opt-in) so the first request isn't the one that pays for it."""
    if os.environ.get("WARMUP_AI") != "1" or DISABLE_AI:
        return
    logger.info("Initializing AI Models... This may take a while on first run.")
    try:
        from scripts.ocr_engine import get_ocr_engine
        engine = await run_in_threadpool(get_ocr_engine)
        logger.info("OCR backend ready: %s", engine.name if engine else "none")
    except Exception as e:
        logger.warning("AI Model initialization failed: %s", e)


# Ensure directories exist
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Mount static files
class _StaticFilesWithModuleTypes(StaticFiles):
    """StaticFiles that always labels ES modules and wasm correctly.

    `mimetypes.add_type` above is not enough on its own: anything that later
    calls `mimetypes.init()` resets the table, and the response would revert to
    text/plain, which browsers refuse for module scripts. Pinning the header
    here removes that dependency.
    """

    _FIXED_TYPES = {
        ".mjs": "text/javascript; charset=utf-8",
        ".wasm": "application/wasm",
    }

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        fixed = self._FIXED_TYPES.get(os.path.splitext(path)[1].lower())
        if fixed and response.status_code == 200:
            response.headers["content-type"] = fixed
        return response


app.mount("/static", _StaticFilesWithModuleTypes(directory=str(BASE_DIR / "static")), name="static")

# --- Stale-file sweeper (privacy guarantee) ---
from scripts.security_utils import secure_filename

def _delete_stale_files(directory: Path, ttl: int) -> None:
    now = __import__("time").time()
    for f in directory.iterdir():
        try:
            if f.is_file():
                if (now - f.stat().st_mtime) > ttl:
                    try:
                        f.unlink(missing_ok=True)
                    except (OSError, FileNotFoundError):
                        pass
            elif f.is_dir():
                # A per-result output directory (its name is the download
                # token). Age it by its newest member so a multi-step workflow
                # writing into it can't have the directory swept mid-chain.
                mtimes = [c.stat().st_mtime for c in f.iterdir() if c.is_file()]
                if not mtimes:
                    if (now - f.stat().st_mtime) > ttl:
                        shutil.rmtree(f, ignore_errors=True)
                        app.state.downloads.discard(f.name)
                else:
                    newest = max(mtimes)
                    if (now - newest) > ttl:
                        shutil.rmtree(f, ignore_errors=True)
                        app.state.downloads.discard(f.name)
        except Exception:
            pass

async def cleanup_stale_files_loop():
    while True:
        for d in (UPLOAD_DIR, OUTPUT_DIR):
            await run_in_threadpool(_delete_stale_files, d, FILE_TTL_SECONDS)
        # Same cadence, same purpose: reclaim bookkeeping nobody can reach any
        # more. The limiter's map would otherwise grow one entry per distinct
        # client seen since boot.
        app.state.rate_limiter.prune()
        # Same reasoning applies to the SSE job registry (reconnect/recovery
        # for execute_workflow and api_convert_to_word_stream): a job whose
        # consumer never reconnects never calls jobs.get() for its own id, so
        # without this it grows one entry per job for the life of the process.
        app.state.jobs.prune()
        # Enforce the 90-day retention policy on funnel_events and operation_events.
        await run_in_threadpool(event_log.prune_expired_events)
        await asyncio.sleep(900)

# --- Upload intake ---
# Every endpoint that accepts an UploadFile goes through save_upload() or
# save_uploads(), so the size cap, the extension allowlist and the sandbox check
# live in exactly one place. Do not hand-roll a shutil.copyfileobj() into a
# handler: MAX_UPLOAD_MB is otherwise unenforced (nginx's client_max_body_size
# guards only the proxied deploy, not the Docker image or render.yaml), and the
# allowlist is what keeps arbitrary bytes out of the pikepdf / PyMuPDF /
# LibreOffice / Pillow parsers.
#
# Allowlists are per tool family rather than one global set: a PDF endpoint has
# no reason to accept a .pptx, and the narrower the set the smaller the parser
# surface each route exposes.
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif",
                    ".bmp", ".tif", ".tiff", ".gif"}
# .doc/.odt/.rtf reach LibreOffice via word_to_pdf/word_to_pptx (see Dockerfile).
WORD_EXTENSIONS = {".docx", ".doc", ".odt", ".rtf"}
EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".csv"}
PPT_EXTENSIONS = {".pptx", ".ppt"}

# The union: what a tool-agnostic endpoint (the workflow runner, whose step list
# decides the real type) accepts, and the default when a caller doesn't narrow it.
ALLOWED_EXTENSIONS = (
    PDF_EXTENSIONS | IMAGE_EXTENSIONS | WORD_EXTENSIONS
    | EXCEL_EXTENSIONS | PPT_EXTENSIONS
)


def _total_upload_budget_mb() -> int:
    """Whole-request byte budget for the multi-file endpoints, in MB."""
    # Resolved per call rather than at import so MAX_UPLOAD_MB stays the single
    # knob when MAX_UPLOAD_TOTAL_MB is unset.
    request_limit = current_upload_limit_mb()
    return int(MAX_UPLOAD_TOTAL_MB_ENV) if MAX_UPLOAD_TOTAL_MB_ENV else request_limit


_request_upload_limit_mb: ContextVar[Optional[int]] = ContextVar(
    "file_forge_upload_limit_mb", default=None
)


def current_upload_limit_mb() -> int:
    """Return this request's server-authorized cap, or the public default."""
    limit = _request_upload_limit_mb.get()
    return int(limit) if limit is not None else MAX_UPLOAD_MB


def set_request_upload_limit_mb(limit_mb: int) -> Token:
    """Set an authenticated deployment override without coupling public code to plans."""
    return _request_upload_limit_mb.set(max(MAX_UPLOAD_MB, int(limit_mb)))


def reset_request_upload_limit_mb(token: Token) -> None:
    """Reset a request override after the response has been produced."""
    _request_upload_limit_mb.reset(token)


def _upload_dest(file: UploadFile, allowed: set) -> Path:
    """Validate an upload's extension and return the temp path to write it to.

    The name keeps the "<uuid4>_<original name>" shape that
    scripts.utils.original_stem() knows how to strip, so the file the user
    downloads is still named after the file they uploaded.
    """
    safe_name = secure_filename(file.filename or "")
    ext = Path(safe_name).suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {ext or safe_name}",
        )
    dest = (UPLOAD_DIR / f"{uuid.uuid4()}_{safe_name}").resolve()
    # secure_filename() already strips path components; this is the belt to that
    # braces, and it matters more now that the name is attacker-influenced.
    try:
        dest.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    return dest


def _stream_to_disk(file: UploadFile, dest: Path, budget: int, limit_mb: int) -> int:
    """Copy an upload to `dest` in chunks, stopping if it exceeds `budget` bytes.

    Returns the bytes written. On overflow the partial file is removed and 413
    is raised — the body is never fully buffered in memory or fully written.
    """
    written = 0
    too_large = False
    first_chunk = True
    with dest.open("wb") as out:
        while chunk := file.file.read(1024 * 1024):
            if first_chunk:
                first_chunk = False
                import filetype
                kind = filetype.guess(chunk)
                if kind and kind.mime in ("application/x-executable", "application/x-msdownload", "application/x-sh"):
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=415, detail="Executable files are not allowed for security reasons.")
            written += len(chunk)
            if written > budget:
                too_large = True
                break
            out.write(chunk)
    if too_large:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413, detail=f"Upload exceeds the {limit_mb} MB limit."
        )
    return written


async def save_upload(file: UploadFile, allowed: Optional[set] = None) -> Path:
    """Save one upload under a unique name, size- and extension-checked.

    Call this *before* the handler's try/except: an HTTPException raised here is
    a 413/415 that must reach the client, and most handlers funnel every
    in-try exception into a 400.

    The chunked disk write runs in the threadpool (via run_in_threadpool) so a
    large upload doesn't block the event loop for every other in-flight request
    for the duration of the write.
    """
    dest = _upload_dest(file, ALLOWED_EXTENSIONS if allowed is None else allowed)
    limit_mb = current_upload_limit_mb()
    await run_in_threadpool(
        _stream_to_disk, file, dest, limit_mb * 1024 * 1024, limit_mb
    )
    return dest


async def save_uploads(files: List[UploadFile], allowed: Optional[set] = None) -> List[Path]:
    """Save a batch of uploads under a single shared size budget.

    Everything written so far is removed before the 413/415 propagates, so a
    rejected batch leaves nothing behind for the sweeper to find. Each file's
    disk write runs in the threadpool, same as save_upload().
    """
    allowed = ALLOWED_EXTENSIONS if allowed is None else allowed
    limit_mb = _total_upload_budget_mb()
    remaining = limit_mb * 1024 * 1024
    saved: List[Path] = []
    try:
        for f in files:
            dest = _upload_dest(f, allowed)
            remaining -= await run_in_threadpool(_stream_to_disk, f, dest, remaining, limit_mb)
            saved.append(dest)
    except Exception:
        for p in saved:
            p.unlink(missing_ok=True)
        raise
    return saved

# --- Rate Limiting (Issue #47) ---
import time
import threading
from collections import defaultdict, deque


class RedisSlidingWindowRateLimiter:
    """Sliding-window limiter whose counters live in Redis (#105, #125).

    Same ``check(key, limit) -> (allowed, retry_after)`` contract as the
    in-memory limiter, so the middleware does not care which backend is in
    use. Timestamps are wall-clock (not monotonic) because workers do not
    share a clock source other than Redis.
    """

    def __init__(self, client, window_seconds: float = 60.0, prefix: str = "ff:rl:"):
        self.client = client
        self.window = window_seconds
        self.prefix = prefix

    def check(self, key: str, limit: int):
        now = time.time()
        rkey = self.prefix + key
        member = f"{now:.6f}:{os.urandom(4).hex()}"
        cutoff = now - self.window
        pipe = self.client.pipeline()
        pipe.zremrangebyscore(rkey, "-inf", cutoff)
        pipe.zcard(rkey)
        count = int(pipe.execute()[1])
        if count >= limit:
            oldest = self.client.zrange(rkey, 0, 0, withscores=True)
            retry_after = int(self.window) + 1
            if oldest:
                retry_after = int(self.window - (now - float(oldest[0][1]))) + 1
            return False, max(1, retry_after)
        self.client.zadd(rkey, {member: now})
        self.client.expire(rkey, int(self.window) + 2)
        return True, 0

    def prune(self) -> int:
        return 0

    def reset(self):
        for key in self.client.scan_iter(match=self.prefix + "*"):
            self.client.delete(key)


def build_rate_limiter(window_seconds: float = 60.0):
    """Use Redis when REDIS_URL is set; otherwise the in-process limiter."""
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return SlidingWindowRateLimiter(window_seconds=window_seconds)
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError(
            "REDIS_URL is set but the redis package is not installed"
        ) from exc
    client = redis.Redis.from_url(url, decode_responses=True)
    client.ping()
    return RedisSlidingWindowRateLimiter(client, window_seconds=window_seconds)


class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding-window rate limiter keyed by client.

    State is per-process. With more than one worker the effective limit
    multiplies by the worker count, so this has to move to shared storage
    (Redis via REDIS_URL) before the app is run with `-w N`.
    assert_single_worker() below turns that from a silent 4x into a boot error.
    """

    def __init__(self, window_seconds: float = 60.0, max_keys: int = 20000):
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int):
        """Record a hit for `key`. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            if key not in self._hits and len(self._hits) >= self.max_keys:
                self._evict_locked()
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = int(self.window - (now - hits[0])) + 1
                return False, retry_after
            hits.append(now)
            return True, 0

    def _prune_locked(self, cutoff: float) -> int:
        stale = [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for k in stale:
            del self._hits[k]
        return len(stale)

    def _evict_locked(self) -> None:
        """Make room for a new key. Caller holds the lock.

        prune() only runs every 900s from the sweeper, so between sweeps a
        client rotating its identity — which is cheap to do whenever the
        trusted header isn't authenticated, see `client_identity` — grew this
        map without bound. Drain the expired keys first; if that isn't enough,
        drop the least-recently-seen keys until we're back under the cap.

        Eviction is deliberately not "refuse the request": refusing would let a
        flood of junk keys 429 everybody else, which is the outage the cap
        exists to prevent. An evicted attacker gets a fresh bucket, but that is
        what they already had by rotating the key; what actually bounds their
        damage is the identity-independent heavy-tier gate below.
        """
        self._prune_locked(time.monotonic() - self.window)
        if len(self._hits) < self.max_keys:
            return
        oldest = sorted(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0.0)
        for k in oldest[: max(1, len(self._hits) - self.max_keys + 1)]:
            del self._hits[k]

    def prune(self) -> int:
        """Drop keys whose window has fully drained. Returns the count removed.

        defaultdict(deque) creates an entry per distinct key and check() only
        pops expired *hits*, never the emptied deque — so _hits grew by one
        entry per client seen since boot, unbounded, and an attacker rotating
        spoofed addresses could drive that growth deliberately.
        """
        with self._lock:
            return self._prune_locked(time.monotonic() - self.window)

    def reset(self):
        with self._lock:
            self._hits.clear()


# --- Result delivery ---
# Output names are deterministic — branded_filename() turns "resume.pdf" into
# "resume_forgefiles.org.pdf" every time — so serving them out of one flat
# directory meant anyone who could guess a name could download a stranger's
# document, and two people converting "report.pdf" at once wrote to the same
# path. Each result now lands in its own directory named by an unguessable
# token; that token is the only download key, and the branded name survives
# purely as the Content-Disposition label the user sees when saving.
#
# 24 bytes -> 192 bits of entropy, well past guessing, and URL-safe so it needs
# no escaping in the path segment.
_DOWNLOAD_TOKEN_BYTES = 24
_DOWNLOAD_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# Defence in depth for a *leaked* link (referrer, shared history), not the
# primary control — the token is. Off by default because the Capacitor mobile
# app calls this API cross-origin, where the browser does not attach the ff_sid
# cookie, and a hard requirement would break downloads there.
DOWNLOAD_BIND_SESSION = os.environ.get("DOWNLOAD_BIND_SESSION", "0") == "1"


class DownloadRegistry:
    """Maps a download token to the finished result it stands for.

    In-process, deliberately: results live on this box's local disk, so a second
    worker could not serve another worker's files even with a shared map. Going
    multi-worker means shared *storage*, not just shared state — see the note on
    SlidingWindowRateLimiter, which has the same constraint.

    A restart of *this* worker is a different problem: the in-memory map is
    gone, but new_result_dir() names every result directory after its own
    token, so the file is still findable on disk under OUTPUT_DIR/<token>.
    resolve() falls back to that lookup on a cache miss instead of telling a
    user their just-finished conversion "no longer exists".
    """

    def __init__(self):
        self._entries = {}
        self._lock = threading.Lock()

    def add(self, path: Path, session_id) -> str:
        """Register a finished result and return its download token."""
        token = path.parent.name
        with self._lock:
            self._entries[token] = (path, session_id, time.monotonic())
        return token

    def _recover_from_disk(self, token: str):
        """Reconstruct an entry for a token that isn't in memory, by checking
        whether OUTPUT_DIR/<token> still holds the one file a completed
        result directory always ends up with.

        Session ownership can't be recovered this way, so a recovered entry
        has owner=None -- resolve() only enforces DOWNLOAD_BIND_SESSION when
        an owner is known, so this doesn't defeat that check, it just doesn't
        survive a restart (the token itself is still unguessable).
        """
        result_dir = OUTPUT_DIR / token
        try:
            result_dir.resolve().relative_to(OUTPUT_DIR.resolve())
        except (ValueError, OSError):
            return None
        if not result_dir.is_dir():
            return None
        files = [p for p in result_dir.iterdir() if p.is_file()]
        if len(files) != 1:
            return None
        path = files[0]
        age_seconds = max(0.0, time.time() - path.stat().st_mtime)
        created = time.monotonic() - age_seconds
        return (path, None, created)

    def resolve(self, token: str, session_id):
        """Return the registered path, or None if unknown/expired/not yours."""
        with self._lock:
            entry = self._entries.get(token)
        if entry is None:
            entry = self._recover_from_disk(token)
            if entry is None:
                return None
            with self._lock:
                entry = self._entries.setdefault(token, entry)
        path, owner, created = entry
        # Entries expire with the files themselves, so a token can never
        # outlive its result and point at a directory reused later.
        if time.monotonic() - created > FILE_TTL_SECONDS:
            with self._lock:
                self._entries.pop(token, None)
            return None
        if DOWNLOAD_BIND_SESSION and owner is not None and owner != session_id:
            return None
        return path

    def discard(self, token: str) -> None:
        with self._lock:
            self._entries.pop(token, None)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


app.state.downloads = DownloadRegistry()


class JobRegistry:
    """Tracks the terminal outcome of a background SSE job by a stable job_id,
    independent of whether the SSE connection that started it is still open.

    api_convert_to_word_stream's worker keeps running on its own thread even
    after the client's SSE connection drops (a network blip, a backgrounded
    tab) — it finishes the conversion and puts the 'complete' event on a queue
    nobody is reading anymore. A client that reconnects (or falls back to
    polling after a stream read fails) can recover the result here by job_id
    instead of losing a multi-minute AI conversion to a dropped connection.
    """

    def __init__(self):
        self._entries = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        job_id = secrets.token_urlsafe(_DOWNLOAD_TOKEN_BYTES)
        with self._lock:
            self._entries[job_id] = {"status": "pending", "created": time.monotonic()}
        return job_id

    def set_result(self, job_id: str, event: dict) -> None:
        """Record the job's terminal SSE event (a 'complete' or 'error' payload)."""
        with self._lock:
            previous = self._entries.get(job_id, {})
            created = previous.get("created", time.monotonic())
            self._entries[job_id] = {
                "status": "done",
                "event": event,
                "progress": previous.get("progress"),
                "created": created,
            }

    def set_progress(self, job_id: str, event: dict) -> None:
        """Retain the most recent non-terminal event for reconnecting clients."""
        with self._lock:
            previous = self._entries.get(job_id)
            if previous is None or previous.get("status") == "done":
                return
            previous["progress"] = event

    def get(self, job_id: str):
        with self._lock:
            entry = self._entries.get(job_id)
            if entry is None:
                return None
            if time.monotonic() - entry["created"] > FILE_TTL_SECONDS:
                del self._entries[job_id]
                return None
            return dict(entry)

    def prune(self) -> int:
        """Drop entries older than FILE_TTL_SECONDS. Returns the count removed.

        get() only expires an entry when something looks it up by its exact
        job_id — the reconnect/recovery path. A job whose SSE consumer never
        disconnects (the common case: the browser tab stayed open and read the
        'complete' event straight off the stream) never calls get() for its own
        job_id, so its entry sits in _entries forever. That is the same
        unbounded-growth shape SlidingWindowRateLimiter.prune() already exists
        to fix for the rate limiter's per-client map; JobRegistry had no
        equivalent, so cleanup_stale_files_loop's periodic sweep left this one
        process-local dict growing by one entry per workflow/stream job for the
        life of the process.
        """
        cutoff = time.monotonic() - FILE_TTL_SECONDS
        with self._lock:
            stale = [jid for jid, entry in self._entries.items() if entry["created"] <= cutoff]
            for jid in stale:
                del self._entries[jid]
            return len(stale)


app.state.jobs = JobRegistry()

# Strong references for work deliberately detached from an HTTP stream. Python
# keeps running tasks weakly; without this set, a dropped SSE client could make
# the only application-level reference to its still-useful workflow disappear.
_DETACHED_TASKS = set()


def _start_detached(coro):
    task = asyncio.create_task(coro)
    _DETACHED_TASKS.add(task)
    task.add_done_callback(_DETACHED_TASKS.discard)
    return task


@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str):
    """Poll the outcome of a background SSE job (issue #95).

    A client whose SSE stream dropped mid-conversion calls this after it
    reconnects, or after retries are exhausted, to find out whether the job
    actually finished while it was disconnected.
    """
    if not _DOWNLOAD_TOKEN_RE.match(job_id):
        raise HTTPException(status_code=404, detail="Unknown job")
    entry = app.state.jobs.get(job_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return entry


def new_result_dir() -> Path:
    """Create a fresh per-result output directory; its name is the token."""
    d = OUTPUT_DIR / secrets.token_urlsafe(_DOWNLOAD_TOKEN_BYTES)
    d.mkdir(parents=True, exist_ok=True)
    return d


_CTX_SESSION = object()  # sentinel: "take the session from the request context"


def download_fields(output_path, session_id=_CTX_SESSION) -> dict:
    """Register a finished result and describe it for the client.

    `filename` stays the branded display name every tool already returned — the
    clients show it as text — while `download_token` is what /api/download
    actually takes.

    Pass `session_id` explicitly from a worker thread: contextvars don't
    propagate there, so the request context would read back empty (the SSE
    endpoints already capture it up front for exactly this reason).
    """
    path = Path(output_path)
    if session_id is _CTX_SESSION:
        _, session_id = event_log.get_request_context()
    token = app.state.downloads.add(path, session_id)
    return {"filename": path.name, "download_token": token}


# Endpoints that are CPU/memory intensive get a stricter limit
RATE_LIMIT_HEAVY_PATHS = {
    "/api/pdf/convert-to-word",
    "/api/pdf/convert-to-word-stream",
    "/api/workflow/execute",
    "/api/pdf/to-excel",
    "/api/pdf/to-pptx",
    "/api/word/to-pptx",
    # Mounted by the private server.py, not by this app — but it is the most
    # expensive route in the deployment (a 200 MB multi-PDF OCR batch), so it
    # belongs in the strictest tier wherever it happens to be registered.
    "/premium/batch-ocr",
}

# Path prefixes the limiter applies to.
#
# This used to be a bare `startswith("/api/")`, which was right while the free
# app owned every route. The private server.py then mounted /premium, /whoami,
# /admin/stats and /checkout onto this same app, and each one silently landed
# outside the prefix — unthrottled, including the batch-OCR route above. Match
# on a tuple instead so mounting a new prefix is an explicit decision.
_DEFAULT_RATE_LIMIT_PREFIXES = ("/api/", "/premium/", "/admin/", "/checkout", "/whoami")
_RATE_LIMIT_PREFIXES_ENV = os.environ.get("RATE_LIMIT_PREFIXES")
RATE_LIMIT_PREFIXES = (
    tuple(p.strip() for p in _RATE_LIMIT_PREFIXES_ENV.split(",") if p.strip())
    if _RATE_LIMIT_PREFIXES_ENV
    else _DEFAULT_RATE_LIMIT_PREFIXES
)

def _assert_single_worker() -> None:
    """Refuse to boot multi-worker while per-process state is load-bearing.

    Both the rate limiter and the download registry keep their state in this
    process, so a second worker silently multiplies the rate limits and cannot
    resolve tokens minted by its sibling. gunicorn is already in
    requirements.txt, so `-w 4` is one flag away from being a quiet
    correctness bug with no error to notice. Fail loudly instead.

    Set ALLOW_MULTI_WORKER=1 once both have moved to shared storage, or set
    REDIS_URL so the limiter is shared (#105, #125). Download tokens already
    recover from disk, so a shared volume is enough for results.
    """
    if os.environ.get("ALLOW_MULTI_WORKER") == "1":
        return
    if os.environ.get("REDIS_URL", "").strip():
        return
    workers = os.environ.get("WEB_CONCURRENCY") or os.environ.get("GUNICORN_WORKERS")
    try:
        count = int(workers) if workers else 1
    except ValueError:
        return
    if count > 1:
        raise RuntimeError(
            f"This app keeps rate-limit and download-token state per process, so "
            f"{count} workers would multiply the rate limits by {count} and break "
            f"downloads served by another worker. Run a single worker, set "
            f"REDIS_URL for a shared limiter, or set ALLOW_MULTI_WORKER=1."
        )


app.state.rate_limiter = build_rate_limiter()
app.state.rate_limit_enabled = os.environ.get("RATE_LIMIT_ENABLED", "1").lower() not in ("0", "false", "no")
app.state.rate_limit_heavy = int(os.environ.get("RATE_LIMIT_HEAVY", "5"))    # req/min per IP
app.state.rate_limit_light = int(os.environ.get("RATE_LIMIT_LIGHT", "20"))   # req/min per IP
# Funnel beacons (/api/track) get their own generous bucket so page-view pings
# never eat into a visitor's file-operation budget (the "light" tier).
app.state.rate_limit_track = int(os.environ.get("RATE_LIMIT_TRACK", "120"))  # req/min per IP
# Concurrent heavy jobs allowed across ALL clients, whatever they claim to be.
# Set to 0 to disable the gate.
app.state.rate_limit_heavy_concurrency = int(os.environ.get("RATE_LIMIT_HEAVY_CONCURRENCY", "4"))


# Header carrying the true client address, most-trustworthy first.
#
# request.client.host is NOT usable behind this deployment's proxy chain:
# nginx sets X-Forwarded-For to $proxy_add_x_forwarded_for, which *appends* the
# peer to whatever the client sent, and uvicorn's ProxyHeadersMiddleware trusts
# localhost proxies by default — so a request arriving with a fabricated
# "X-Forwarded-For: 1.2.3.4" reaches the app as client.host == "1.2.3.4".
# Rotating that value walked straight past the 5/min limit on the endpoints
# that run OCR, pdf2docx and LibreOffice on one small VM.
#
# CF-Connecting-IP is first because Cloudflare sets it from the real connection
# and strips any client-supplied copy. That makes it unforgeable *for requests
# that actually traverse Cloudflare* — which is not the same as unforgeable.
# The origin answers on 443 from anywhere and its hostname is public (the
# Let's Encrypt cert is in the CT logs), so a request sent straight to the VM
# arrives with a fully attacker-controlled CF-Connecting-IP and nginx forwards
# it verbatim. Rotating it is then exactly the bypass that rotating
# X-Forwarded-For used to be.
#
# Set RATE_LIMIT_TRUSTED_HEADER to override for a different fronting proxy, or
# to "" to fall back to the socket peer (a direct, unproxied deploy).
_DEFAULT_CLIENT_IP_HEADERS = ("cf-connecting-ip", "x-real-ip")
_TRUSTED_IP_HEADER = os.environ.get("RATE_LIMIT_TRUSTED_HEADER")
CLIENT_IP_HEADERS = (
    tuple(h.strip().lower() for h in _TRUSTED_IP_HEADER.split(",") if h.strip())
    if _TRUSTED_IP_HEADER is not None
    else _DEFAULT_CLIENT_IP_HEADERS
)

# Proof that a request reached us through our own edge rather than direct to
# the origin. nginx injects this header, and only for peers in Cloudflare's
# published ranges (see docs/cloudflare-edge.md); a client that reaches the
# origin directly cannot produce it, so its client-IP headers are ignored and
# it is bucketed on the socket peer instead.
#
# Unset means "no proof available" and preserves the old, spoofable behaviour —
# the deploy is not broken by upgrading, but _warn_if_edge_unauthenticated()
# says so at boot. Note that the header is only ever compared, never logged.
EDGE_AUTH_HEADER = os.environ.get("RATE_LIMIT_EDGE_HEADER", "x-ff-edge-auth").strip().lower()
EDGE_AUTH_SECRET = os.environ.get("RATE_LIMIT_EDGE_SECRET", "").strip()


def _from_trusted_edge(request: Request) -> bool:
    """True if this request carries our edge's shared secret."""
    if not EDGE_AUTH_SECRET:
        return False
    presented = request.headers.get(EDGE_AUTH_HEADER) or ""
    # Compare as bytes: hmac.compare_digest raises TypeError on a str holding
    # non-ASCII, which a client controls and could otherwise turn into a 500.
    return hmac.compare_digest(
        presented.encode("utf-8", "replace"), EDGE_AUTH_SECRET.encode("utf-8", "replace")
    )


def _warn_if_edge_unauthenticated() -> None:
    if not EDGE_AUTH_SECRET and CLIENT_IP_HEADERS:
        logger.warning(
            "Rate limiting buckets on %s but RATE_LIMIT_EDGE_SECRET is unset, so a "
            "request sent straight to the origin can forge it and mint a fresh "
            "bucket per request. Restrict 443 to Cloudflare's ranges, or set "
            "RATE_LIMIT_EDGE_SECRET here and inject %s at the edge "
            "(see docs/cloudflare-edge.md).",
            CLIENT_IP_HEADERS[0],
            EDGE_AUTH_HEADER,
        )


def client_identity(request: Request) -> str:
    """The address to bucket this request under.

    Only reads the forwarded client-IP headers when the request demonstrably
    came through our edge; otherwise the socket peer is the best available
    identity, and unlike the headers the client cannot choose it.
    """
    if _from_trusted_edge(request):
        for header in CLIENT_IP_HEADERS:
            value = request.headers.get(header)
            if value:
                # X-Real-IP is a single address; be tolerant of a list anyway and
                # take the first entry, which is the one the trusted proxy wrote.
                return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


QUEUE_ESTIMATED_SECONDS_PER_JOB = int(os.environ.get("QUEUE_ESTIMATED_SECONDS_PER_JOB", "5"))
MAX_QUEUE_DEPTH = int(os.environ.get("MAX_QUEUE_DEPTH", "50"))
SSE_QUEUE_MANAGED_PATHS = {"/api/pdf/convert-to-word-stream"}


class _GatePermit:
    def __init__(self, gate: "_InFlightQueueGate", counted: bool = True):
        self._gate = gate
        self._counted = counted
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._counted:
            self._gate.release()


class _GateTicket:
    def __init__(
        self,
        gate: "_InFlightQueueGate",
        future: asyncio.Future,
        waiter: Optional[dict[str, Any]] = None,
    ):
        self._gate = gate
        self.future = future
        self._waiter = waiter

    async def wait(self) -> _GatePermit:
        return await self.future

    def cancel(self) -> None:
        self._gate.cancel(self)


class _InFlightQueueGate:
    """Counts active heavy requests and buffers excess work in FIFO order.

    The per-identity limits above are only as good as the identity. This gate
    is deliberately identity-free: however many buckets an attacker mints, the
    box still runs at most `limit` OCR / pdf2docx / LibreOffice jobs at once,
    which is what stops a spoofed flood from taking the single worker down.
    Excess requests wait in a bounded FIFO queue instead of being dropped until
    `max_queue_depth` is reached.
    """

    def __init__(self, limit: int, max_queue_depth: int = MAX_QUEUE_DEPTH):
        self.limit = limit
        self.max_queue_depth = max_queue_depth
        self._active = 0
        self._queue = deque()
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        """Immediate, non-queued acquire kept for legacy tests and probes."""
        if self.limit <= 0:
            return True
        with self._lock:
            if self._active >= self.limit:
                return False
            self._active += 1
            return True

    def reserve(
        self,
        on_update: Optional[Callable[[dict[str, int]], None]] = None,
    ) -> Optional[_GateTicket]:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        with self._lock:
            if self.limit <= 0:
                future.set_result(_GatePermit(self, counted=False))
                return _GateTicket(self, future)
            if self._active < self.limit:
                self._active += 1
                future.set_result(_GatePermit(self))
                return _GateTicket(self, future)
            if len(self._queue) >= self.max_queue_depth:
                return None

            waiter = {"future": future, "loop": loop, "on_update": on_update}
            self._queue.append(waiter)
            ticket = _GateTicket(self, future, waiter)
            self._broadcast_locked()
            return ticket

    async def wait(self) -> Optional[_GatePermit]:
        ticket = self.reserve()
        if ticket is None:
            return None
        try:
            return await ticket.wait()
        except asyncio.CancelledError:
            ticket.cancel()
            raise

    def cancel(self, ticket: _GateTicket) -> None:
        waiter = ticket._waiter
        if waiter is None:
            return
        with self._lock:
            try:
                self._queue.remove(waiter)
            except ValueError:
                return
            future = waiter["future"]
            if not future.done():
                waiter["loop"].call_soon_threadsafe(future.cancel)
            self._broadcast_locked()

    def release(self) -> None:
        to_wake = None
        with self._lock:
            self._active = max(0, self._active - 1)
            if self.limit > 0 and self._queue and self._active < self.limit:
                to_wake = self._queue.popleft()
                self._active += 1
            self._broadcast_locked()
        if to_wake:
            future = to_wake["future"]
            if not future.done():
                to_wake["loop"].call_soon_threadsafe(
                    future.set_result,
                    _GatePermit(self),
                )

    @property
    def queued_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def _broadcast_locked(self) -> None:
        total = len(self._queue)
        for index, waiter in enumerate(self._queue, start=1):
            callback = waiter.get("on_update")
            if not callback:
                continue
            payload = {
                "position": index,
                "total_queued": total,
                "estimated_wait_seconds": index * QUEUE_ESTIMATED_SECONDS_PER_JOB,
            }
            waiter["loop"].call_soon_threadsafe(callback, payload)


_InFlightGate = _InFlightQueueGate


app.state.heavy_gate = _InFlightQueueGate(app.state.rate_limit_heavy_concurrency)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    state = request.app.state
    path = request.url.path
    if not getattr(state, "rate_limit_enabled", False) or not path.startswith(RATE_LIMIT_PREFIXES):
        return await call_next(request)

    client_ip = client_identity(request)
    if path == "/api/track":
        tier, limit = "track", getattr(state, "rate_limit_track", 120)
    elif path in RATE_LIMIT_HEAVY_PATHS:
        tier, limit = "heavy", state.rate_limit_heavy
    else:
        tier, limit = "light", state.rate_limit_light

    allowed, retry_after = state.rate_limiter.check(f"{client_ip}:{tier}", limit)
    if not allowed:
        logger.warning("Rate limit exceeded for %s on %s", client_ip, path)
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please slow down."},
            headers={"Retry-After": str(retry_after)},
        )

    if tier != "heavy":
        return await call_next(request)

    gate = getattr(state, "heavy_gate", None)
    if gate is None:
        return await call_next(request)
    if path in SSE_QUEUE_MANAGED_PATHS:
        return await call_next(request)
    permit = await gate.wait()
    if permit is None:
        logger.warning("Heavy-tier capacity reached; refusing %s", path)
        return JSONResponse(
            status_code=503,
            content={"detail": "The server is busy processing other files. Please retry shortly."},
            headers={"Retry-After": "5"},
        )
    try:
        response = await call_next(request)
    except Exception:
        permit.release()
        raise

    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is None:
        permit.release()
        return response

    async def release_after_body():
        try:
            async for chunk in body_iterator:
                yield chunk
        finally:
            job_id = response.headers.get("X-FF-Job-ID")
            if not job_id:
                permit.release()
            else:
                # The client can disconnect while a recoverable SSE job keeps
                # running. Keep its capacity slot until the underlying job, not
                # merely the response body, reaches a terminal state (#171).
                async def release_after_job():
                    try:
                        deadline = time.monotonic() + FILE_TTL_SECONDS
                        while time.monotonic() < deadline:
                            entry = state.jobs.get(job_id)
                            if entry is None or entry.get("status") == "done":
                                break
                            await asyncio.sleep(0.1)
                    finally:
                        permit.release()

                _start_detached(release_after_job())

    response.body_iterator = release_after_body()
    return response


# --- Anonymous operation-event context (server-side analytics, no tracking script) ---
SESSION_COOKIE_NAME = "ff_sid"
SESSION_COOKIE_MAX_AGE = 90 * 24 * 3600

# Cloudflare sends an ISO-3166-1 alpha-2 code, plus "XX" (unknown) and "T1"
# (Tor). Anything else is a client writing whatever it likes into
# operation_events.country, which is what /admin/stats reports on.
_COUNTRY_CODE_RE = re.compile(r"^[A-Z0-9]{2}$")


def _client_country(request: Request) -> Optional[str]:
    """The visitor's country, or None when we have no trustworthy answer."""
    if not _from_trusted_edge(request):
        return None
    value = (request.headers.get("cf-ipcountry") or "").strip().upper()
    return value if _COUNTRY_CODE_RE.match(value) else None


def _content_length(request: Request) -> Optional[int]:
    """The request's Content-Length as a non-negative int, or None if absent
    or malformed (a chunked upload sends no Content-Length at all)."""
    raw = request.headers.get("content-length")
    if not raw:
        return None
    try:
        size = int(raw)
    except ValueError:
        return None
    return size if size >= 0 else None


@app.middleware("http")
async def event_context_middleware(request: Request, call_next):
    """Expose CF-IPCountry and an anonymous session id to scripts/event_log.

    Only /api/ requests get the context and cookie: every operation lives under
    /api/, and page views are deliberately not tracked. The session id is a
    random UUID in a first-party cookie tied to nothing — no PII, and IP
    addresses are never stored anywhere.
    """
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    country = _client_country(request)
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    is_new_session = session_id is None
    if is_new_session:
        session_id = str(uuid.uuid4())
    token = event_log.set_request_context(country, session_id)
    # How big the upload was. Taken here, once, rather than at each of the ~40
    # log_event() call sites: every operation runs inside a request, so the
    # middleware is the one place that sees the size for all of them. It is the
    # request body, not the file — close enough to answer "are the slow runs
    # just the big files?", which is the question p95 alone can't.
    bytes_token = event_log.set_request_bytes(_content_length(request))
    ga_cid = ga4_mp.extract_client_id(request)
    ga_token = ga4_mp.set_request_client_id(ga_cid)
    try:
        response = await call_next(request)
    finally:
        event_log.reset_request_context(token)
        event_log.reset_request_bytes(bytes_token)
        ga4_mp.reset_request_client_id(ga_token)
    if is_new_session:
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_id,
            max_age=SESSION_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=True,
        )
    return response

# --- Input Validation Helpers (Issue #45) ---

def validate_range(name: str, value, min_value=None, max_value=None):
    """Validate that a numeric parameter falls within an allowed range.

    None values are allowed (treated as "not provided"); callers enforce
    required parameters separately. Raises 422 on invalid input.
    """
    if value is None:
        return
    if min_value is not None and value < min_value:
        raise HTTPException(
            status_code=422,
            detail=f"{name} must be >= {min_value} (got {value})",
        )
    if max_value is not None and value > max_value:
        raise HTTPException(
            status_code=422,
            detail=f"{name} must be <= {max_value} (got {value})",
        )


def validate_quality(quality: int):
    """JPEG quality must be in [1, 95] (Pillow recommends <= 95)."""
    validate_range("quality", quality, 1, 95)


def _substitute(html_text: str) -> str:
    """Replace server-side template tokens shared by every page."""
    return (
        html_text
        .replace("{{BASE_URL}}", BASE_URL)
        .replace("{{ADSENSE_HEAD}}", ADSENSE_HEAD_HTML)
        .replace("{{ADSENSE_SLOT}}", ADSENSE_SLOT_HTML)
        .replace("{{CONSENT_BANNER}}", CONSENT_BANNER_HTML)
        .replace("{{SITE_VERIFICATION}}", SITE_VERIFICATION_HTML)
        .replace("{{CF_ANALYTICS}}", CF_ANALYTICS_HTML)
        .replace("{{GA_ANALYTICS}}", GA_ANALYTICS_HTML)
    )


@lru_cache(maxsize=64)
def _render_page(relative_path: str) -> str:
    raw = (BASE_DIR / "static" / relative_path).read_text(encoding="utf-8")
    return _substitute(raw)


@lru_cache(maxsize=64)
def _render_tool_page(slug: str) -> str:
    """Server-render a tool landing page (full HTML, no JS needed for crawlers)."""
    return _substitute(seo_content.render_tool_page(slug))

@app.api_route("/", methods=["GET", "HEAD"])
async def read_index():
    return HTMLResponse(_render_page("index.html"))

@app.post("/api/pdf/remove-password")
async def api_remove_password(
    file: UploadFile = File(...),
    password: str = Form(...)
):
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_unlock",
            run_in_threadpool(remove_pdf_password, str(temp_path), password, str(result_dir)),
        )
        return {"status": "success", "message": "Password removed", **download_fields(output_path)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass

@app.get("/api/ai-capabilities")
async def api_ai_capabilities():
    """Report what the configured AI/OCR backend can actually deliver, so the
    frontend's "Use AI Layout Recovery" checkbox can describe reality (e.g.
    on ARM deployments where RapidOCR has no table/column layout recovery)
    instead of a single hard-coded label."""
    if DISABLE_AI:
        return {"enabled": False, "supports_layout": None}
    from scripts.ocr_engine import get_ocr_engine
    engine = await run_in_threadpool(get_ocr_engine)
    if engine is None:
        return {"enabled": False, "supports_layout": None}
    return {"enabled": True, "supports_layout": engine.supports_layout}


@app.post("/api/pdf/convert-to-word")
async def api_convert_to_word(
    file: UploadFile = File(...),
    use_ai: bool = Form(False),
    password: str = Form(None)
):
    # Sanitize filename and add UUID prefix
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    logger.debug("Converting: %s, use_ai=%s, password=%s", safe_filename, use_ai, '***' if password else 'None')
    try:
        logger.debug("File saved to: %s", temp_path)
        
        if use_ai:
            # @jules: This can be very slow for large PDFs.
            # We should probably implement a progress bar or background task with polling.
            ai_method = {}
            output_path = await event_log.timed(
                "pdf_to_word_ai",
                run_in_threadpool(
                    pdf_to_word_ai, str(temp_path), str(result_dir), password,
                    method_callback=lambda m: ai_method.__setitem__("value", m),
                ),
                use_ai=True,
            )
            message = _ai_conversion_message(ai_method.get("value"))
        else:
            output_path = await event_log.timed(
                "pdf_to_word_standard",
                run_in_threadpool(pdf_to_docx, str(temp_path), str(result_dir), password),
            )
            message = "Converted to Word (Standard)"

        logger.info("Conversion successful: %s", output_path)
        return {"status": "success", "message": message, **download_fields(output_path)}
    except ServerDependencyError as e:
        # 503, not the blanket 400 below: the server is missing a component, so
        # calling it a bad request points the user at their own file and tells
        # uptime monitoring that everything is fine.
        logger.exception("Conversion unavailable for %s", safe_filename)
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("Conversion failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))

    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass  # Windows file locking - will be cleaned up later

@app.post("/api/pdf/convert-to-word-stream")
async def api_convert_to_word_stream(
    request: Request,
    file: UploadFile = File(...),
    use_ai: bool = Form(False),
    password: str = Form(None)
):
    """Convert PDF to Word with real-time SSE progress events (Issue #46).

    Streams events: {"event": "start"}, {"event": "progress", "page": n,
    "total": t}, then {"event": "complete", "filename": ...} or
    {"event": "error", "detail": ...}.
    """
    import json
    import queue as queue_mod
    from fastapi.responses import StreamingResponse

    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    # Captured here because the worker runs on a raw thread, where the
    # middleware's contextvars don't propagate. The upload size comes along for
    # the same reason — and it matters most here, since these are the slowest
    # operations on the site and "was it just a big file?" is the first
    # question their duration raises.
    ctx_country, ctx_session = event_log.get_request_context()
    ctx_bytes = event_log.get_request_bytes()
    op_name = "pdf_to_word_ai" if use_ai else "pdf_to_word_standard"
    # The worker thread below outlives a dropped SSE connection (issue #95):
    # a job_id lets a reconnecting/polling client recover the result via
    # GET /api/jobs/{job_id} even though the 'complete' event it's about to
    # put on `events` was never read by anyone.
    job_id = app.state.jobs.create()
    queue_updates: asyncio.Queue = asyncio.Queue()
    gate = getattr(app.state, "heavy_gate", None)

    def queue_update(payload: dict[str, int]) -> None:
        queue_updates.put_nowait({"event": "queued", **payload})

    ticket = gate.reserve(queue_update) if gate is not None else None
    if gate is not None and ticket is None:
        with suppress(OSError):
            if temp_path.exists():
                os.remove(temp_path)
        with suppress(OSError):
            result_dir.rmdir()
        raise HTTPException(
            status_code=503,
            detail="The server is busy processing other files. Please retry shortly.",
            headers={"Retry-After": "5"},
        )

    async def event_stream():
        events = queue_mod.Queue()
        permit = None
        worker_started = False

        def progress_cb(page_done, total_pages):
            progress_event = {"event": "progress", "page": page_done, "total": total_pages}
            app.state.jobs.set_progress(job_id, progress_event)
            events.put(progress_event)

        def worker():
            terminal_event = None
            try:
                if use_ai:
                    ai_method = {}
                    output_path = event_log.timed_call(
                        op_name, pdf_to_word_ai,
                        str(temp_path), str(result_dir), password, progress_callback=progress_cb,
                        method_callback=lambda m: ai_method.__setitem__("value", m),
                        use_ai=True, country=ctx_country, session_id=ctx_session,
                        request_bytes=ctx_bytes,
                    )
                    method = ai_method.get("value")
                    message = _ai_conversion_message(method)
                else:
                    output_path = event_log.timed_call(
                        op_name, pdf_to_docx, str(temp_path), str(result_dir), password,
                        progress_callback=progress_cb,
                        country=ctx_country, session_id=ctx_session,
                        request_bytes=ctx_bytes,
                    )
                    method = "standard"
                    message = "Converted to Word (Standard)"
                complete_event = {
                    "event": "complete",
                    "message": message,
                    "method": method,
                    **download_fields(output_path, ctx_session),
                }
                terminal_event = complete_event
                events.put(complete_event)
            except Exception as e:
                logger.exception("Streaming conversion failed for %s", safe_filename)
                error_event = {"event": "error", "detail": event_log.scrub_paths(str(e))}
                terminal_event = error_event
                events.put(error_event)
            finally:
                # Cleanup belongs to the worker, not the SSE consumer. A client
                # disconnect must not unlink a file the conversion still reads
                # or leave it behind until the periodic sweeper (#172).
                if temp_path.exists():
                    try:
                        os.remove(temp_path)
                    except (PermissionError, OSError):
                        pass
                app.state.jobs.set_result(job_id, terminal_event or {
                    "event": "error",
                    "detail": "Conversion ended without a result",
                })
                if permit is not None:
                    permit.release()
                events.put(None)  # sentinel: stream finished

        try:
            if ticket is not None:
                while not ticket.future.done():
                    if await request.is_disconnected():
                        ticket.cancel()
                        app.state.jobs.set_result(job_id, {
                            "event": "error",
                            "detail": "Conversion was cancelled before it started.",
                        })
                        return
                    try:
                        queued = await asyncio.wait_for(queue_updates.get(), timeout=0.25)
                    except asyncio.TimeoutError:
                        continue
                    yield f"data: {json.dumps(queued)}\n\n"
                permit = await ticket.wait()

            yield f"data: {json.dumps({'event': 'start', 'filename': safe_filename, 'job_id': job_id})}\n\n"
            worker_started = True
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            while True:
                item = await run_in_threadpool(events.get)
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            if not worker_started:
                if ticket is not None:
                    ticket.cancel()
                if permit is not None:
                    permit.release()
                if temp_path.exists():
                    with suppress(OSError):
                        os.remove(temp_path)
                with suppress(OSError):
                    result_dir.rmdir()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-FF-Job-ID": job_id,
        },
    )


@app.post("/api/pdf/extract-pages")
async def api_extract_pages(
    file: UploadFile = File(...),
    pages: str = Form(...),
    password: str = Form(None),
):
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    logger.debug("Extracting pages: %s, pages='%s', password=%s", safe_filename, pages, '***' if password else 'None')
    try:
        output_path = await event_log.timed(
            "page_extract",
            run_in_threadpool(extract_pdf_pages, str(temp_path), str(result_dir), pages, password),
        )
        return {"status": "success", "message": "Pages extracted", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Page extraction failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/pdf/split")
async def api_split_pdf(
    file: UploadFile = File(...),
    mode: str = Form("each"),
    ranges: str = Form(None),
    n: Optional[int] = Form(None),
    password: str = Form(None),
    max_mb: Optional[float] = Form(None),
):
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    logger.debug("Splitting PDF: %s, mode=%s, password=%s", safe_filename, mode, '***' if password else 'None')
    try:
        result = await event_log.timed(
            "pdf_split",
            run_in_threadpool(split_pdf_to_zip, str(temp_path), str(result_dir), mode, ranges, n, password, max_mb),
        )
        return {
            "status": "success",
            "message": f"PDF split into {result['file_count']} file(s)",
            **download_fields(result["output_path"]),
            "file_count": result["file_count"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("PDF split failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/pdf/compress")
async def api_compress_pdf(
    file: UploadFile = File(...),
    level: str = Form('medium'),
    password: str = Form(None)
):
    """Compress PDF by optimizing structure and resampling large images."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    logger.debug("Compressing: %s, level=%s, password=%s", safe_filename, level, '***' if password else 'None')
    try:
        result = await event_log.timed(
            "pdf_compress",
            run_in_threadpool(compress_pdf, str(temp_path), str(result_dir), level, password or None),
        )
        return {
            "status": "success",
            "message": "PDF compressed successfully",
            **download_fields(result['output_path']),
            "original_size": result['original_size'],
            "compressed_size": result['compressed_size'],
            "reduction_pct": result['reduction_pct'],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("PDF compression failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/pdf/merge")
async def api_merge_pdfs(
    files: List[UploadFile] = File(...),
    passwords: Optional[str] = Form(None),
):
    """Merge multiple PDFs into one. `passwords` is an optional comma-separated list aligned with files."""
    if not files or len(files) < 2:
        raise HTTPException(status_code=400, detail="Provide at least two PDF files to merge.")

    # Outside the try: a 413/415 from here must reach the client, and the
    # handler below funnels every in-try exception into a 400.
    temp_paths = await save_uploads(files, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        pwd_list = None
        if passwords:
            pwd_list = [p if p else None for p in passwords.split(",")]

        output_path = await event_log.timed(
            "pdf_merge",
            run_in_threadpool(merge_pdfs, [str(p) for p in temp_paths], str(result_dir), pwd_list),
        )
        return {"status": "success", "message": "PDFs merged", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("PDF merge failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        for p in temp_paths:
            if p.exists():
                try:
                    os.remove(p)
                except PermissionError:
                    pass


@app.post("/api/pdf/watermark")
async def api_add_watermark(
    file: UploadFile = File(...),
    text: str = Form(""),
    position: str = Form("diagonal"),
    opacity: float = Form(0.3),
    password: str = Form(None),
    color: str = Form("#808080"),
    font_size: int = Form(0),
    tile: bool = Form(False),
    layer: str = Form("over"),
    logo: UploadFile = File(None),
    logo_scale: float = Form(0.4),
):
    """Stamp a text or PNG/JPEG logo watermark on every page."""
    safe_filename = secure_filename(file.filename)
    logo_bytes = None
    if logo is not None and logo.filename:
        logo_bytes = await logo.read(5 * 1024 * 1024 + 1)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_watermark",
            run_in_threadpool(
                add_watermark, str(temp_path), str(result_dir), text, position, opacity, password or None,
                color, font_size, tile, layer, logo_bytes, logo_scale,
            ),
        )
        return {"status": "success", "message": "Watermark added", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Watermark failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/pdf/crop")
async def api_crop_pdf(
    file: UploadFile = File(...),
    top: float = Form(0),
    bottom: float = Form(0),
    left: float = Form(0),
    right: float = Form(0),
    pages: str = Form(None),
    password: str = Form(None),
):
    """Crop PDF pages by percentage margins (sets the CropBox)."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_crop",
            run_in_threadpool(
                crop_pdf, str(temp_path), str(result_dir), top, bottom, left, right,
                pages or None, password or None,
            ),
        )
        return {"status": "success", "message": "PDF cropped", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("PDF crop failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


@app.post("/api/pdf/rotate")
async def api_rotate_pdf(
    file: UploadFile = File(...),
    angle: int = Form(...),
    pages: str = Form(None),
    password: str = Form(None),
):
    """Rotate PDF pages by specified angle (90, 180, 270 degrees)."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_rotate",
            run_in_threadpool(
                rotate_pdf, str(temp_path), str(result_dir), angle, pages or None, password or None
            ),
        )
        return {"status": "success", "message": f"PDF rotated by {angle}°", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("PDF rotation failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/pdf/to-images")
async def api_pdf_to_images(
    file: UploadFile = File(...),
    dpi: int = Form(150),
    fmt: str = Form("jpg"),
    password: str = Form(None),
    mode: str = Form("pages"),
):
    """Render every page to an image, or extract embedded images, and return a zip."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        result = await event_log.timed(
            "pdf_to_images",
            run_in_threadpool(
                pdf_to_images_zip, str(temp_path), str(result_dir), dpi, fmt, password or None, mode
            ),
        )
        return {
            "status": "success",
            "message": (f"Extracted {result['page_count']} embedded image(s)" if mode == "embedded"
                        else f"Rendered {result['page_count']} page(s) to images"),
            **download_fields(result["output_path"]),
            "page_count": result["page_count"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("PDF to images failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/pdf/sign")
async def api_sign_pdf(
    file: UploadFile = File(...),
    signature: UploadFile = File(...),
    page: int = Form(1),
    x: float = Form(0.65),
    y: float = Form(0.85),
    width: float = Form(0.2),
    password: str = Form(None),
):
    """Stamp a signature image onto the chosen page."""
    result_dir = new_result_dir()
    sig_ct = (signature.content_type or "").lower()
    if sig_ct not in ("image/png", "image/jpeg", "image/jpg"):
        raise HTTPException(status_code=400, detail="Signature must be a PNG or JPEG image.")

    safe_pdf = secure_filename(file.filename)
    # Two files, two allowlists — and one shared budget, so a signature image
    # can't be used to smuggle a second MAX_UPLOAD_MB past the cap.
    pdf_path, sig_path = await save_uploads(
        [file, signature], PDF_EXTENSIONS | IMAGE_EXTENSIONS
    )
    try:
        output_path = await event_log.timed(
            "pdf_sign",
            run_in_threadpool(
                sign_pdf, str(pdf_path), str(sig_path), str(result_dir), page, x, y, width, password or None
            ),
        )
        return {"status": "success", "message": "Signature added", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Sign PDF failed for %s", safe_pdf)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        for p in (pdf_path, sig_path):
            if p.exists():
                try:
                    os.remove(p)
                except PermissionError:
                    pass


@app.post("/api/image/heic-to-jpeg")
async def api_heic_to_jpeg(
    file: UploadFile = File(...),
    quality: int = Form(95),
):
    """Convert HEIC/HEIF image to JPEG format."""
    validate_quality(quality)
    # Sanitize filename to prevent path traversal
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, IMAGE_EXTENSIONS)
    result_dir = new_result_dir()
    logger.debug("Converting HEIC: %s, quality=%d", safe_filename, quality)
    try:
        output_path = await event_log.timed(
            "heic_to_jpeg",
            run_in_threadpool(heic_to_jpeg, str(temp_path), str(result_dir), quality),
        )
        return {"status": "success", "message": "Converted to JPEG", **download_fields(output_path)}
    except Exception as e:
        logger.exception("HEIC conversion failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass  # Windows file locking - will be cleaned up later


@app.post("/api/image/resize")
async def api_resize_image(
    file: UploadFile = File(...),
    mode: str = Form(...),
    width: int = Form(None),
    height: int = Form(None),
    percentage: int = Form(None),
    target_size_kb: int = Form(None),
):
    """Resize image based on parameters."""
    if mode not in ("dimensions", "percentage", "target_size"):
        raise HTTPException(status_code=422, detail="mode must be one of: dimensions, percentage, target_size")
    validate_range("width", width, 1, MAX_RESIZE_DIMENSION)
    validate_range("height", height, 1, MAX_RESIZE_DIMENSION)
    validate_range("percentage", percentage, 1, 500)
    validate_range("target_size_kb", target_size_kb, 1)
    # Sanitize filename to prevent path traversal
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, IMAGE_EXTENSIONS)
    result_dir = new_result_dir()
    logger.debug("Resizing image: %s, mode=%s", safe_filename, mode)
    try:
        from scripts.image_utils import resize_image
        output_path = await event_log.timed(
            "resize",
            run_in_threadpool(
                resize_image,
                str(temp_path),
                str(result_dir),
                mode,
                width=width,
                height=height,
                percentage=percentage,
                target_size_kb=target_size_kb
            ),
        )
        return {"status": "success", "message": "Image Resized", **download_fields(output_path)}
    except Exception as e:
        logger.exception("Image resize failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/image/crop")
async def api_crop_image(
    file: UploadFile = File(...),
    x: int = Form(...),
    y: int = Form(...),
    width: int = Form(...),
    height: int = Form(...),
):
    """Crop image based on coordinates."""
    validate_range("x", x, 0)
    validate_range("y", y, 0)
    validate_range("width", width, 1)
    validate_range("height", height, 1)
    # Sanitize filename to prevent path traversal
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, IMAGE_EXTENSIONS)
    result_dir = new_result_dir()
    logger.debug("Cropping image: %s, x=%d, y=%d, w=%d, h=%d", safe_filename, x, y, width, height)
    try:
        from scripts.image_utils import crop_image
        output_path = await event_log.timed(
            "crop",
            run_in_threadpool(
                crop_image,
                str(temp_path),
                str(result_dir),
                x=x, y=y, width=width, height=height
            ),
        )
        return {"status": "success", "message": "Image Cropped", **download_fields(output_path)}
    except Exception as e:
        logger.exception("Image crop failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/image/rotate")
async def api_rotate_image(
    file: UploadFile = File(...),
    angle: float = Form(90),
    quality: int = Form(95),
):
    validate_quality(quality)
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, IMAGE_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "image_rotate",
            run_in_threadpool(rotate_image, str(temp_path), str(result_dir), angle, quality),
        )
        return {"status": "success", "message": f"Rotated by {angle}°", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try: os.remove(temp_path)
            except PermissionError: pass


@app.post("/api/image/compress")
async def api_compress_image(
    file: UploadFile = File(...),
    quality: int = Form(70),
):
    validate_quality(quality)
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, IMAGE_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        result = await event_log.timed(
            "image_compress",
            run_in_threadpool(compress_image, str(temp_path), str(result_dir), quality),
        )
        return {
            "status": "success",
            "message": "Image compressed" if result["compressed_size"] < result["original_size"] else "Already optimized; original kept",
            **download_fields(result["output_path"]),
            "original_size": result["original_size"],
            "compressed_size": result["compressed_size"],
            "reduction_pct": result["reduction_pct"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try: os.remove(temp_path)
            except PermissionError: pass


@app.post("/api/image/convert")
async def api_convert_image(
    file: UploadFile = File(...),
    target_format: str = Form(...),
    quality: int = Form(90),
):
    validate_quality(quality)
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, IMAGE_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "image_convert",
            run_in_threadpool(convert_image_format, str(temp_path), str(result_dir), target_format, quality),
        )
        return {"status": "success", "message": f"Converted to {target_format.upper()}", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try: os.remove(temp_path)
            except PermissionError: pass


@app.post("/api/image/watermark")
async def api_watermark_image(
    file: UploadFile = File(...),
    text: str = Form(...),
    position: str = Form("bottom-right"),
    opacity: float = Form(0.4),
    color: str = Form("white"),
):
    validate_range("opacity", opacity, 0.0, 1.0)
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, IMAGE_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "image_watermark",
            run_in_threadpool(watermark_image, str(temp_path), str(result_dir), text, position, opacity, color),
        )
        return {"status": "success", "message": "Watermark added", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try: os.remove(temp_path)
            except PermissionError: pass


# --- Excel Routes ---

@app.post("/api/excel/to-pdf")
async def api_excel_to_pdf(
    file: UploadFile = File(...),
):
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, EXCEL_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "excel_to_pdf", run_in_threadpool(excel_to_pdf, str(temp_path), str(result_dir))
        )
        return {"status": "success", "message": "Excel converted to PDF", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try: os.remove(temp_path)
            except PermissionError: pass


@app.post("/api/excel/csv-to-xlsx")
async def api_csv_to_xlsx(
    file: UploadFile = File(...),
    delimiter: str = Form(","),
):
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, EXCEL_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "csv_to_xlsx", run_in_threadpool(csv_to_xlsx, str(temp_path), str(result_dir), delimiter)
        )
        return {"status": "success", "message": "CSV converted to XLSX", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try: os.remove(temp_path)
            except PermissionError: pass


@app.post("/api/excel/xlsx-to-csv")
async def api_xlsx_to_csv(
    file: UploadFile = File(...),
    sheet: str = Form(None),
):
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, EXCEL_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "xlsx_to_csv", run_in_threadpool(xlsx_to_csv, str(temp_path), str(result_dir), sheet or None)
        )
        return {"status": "success", "message": "XLSX converted to CSV", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try: os.remove(temp_path)
            except PermissionError: pass


@app.post("/api/excel/merge")
async def api_merge_excel(
    files: List[UploadFile] = File(...),
):
    if not files or len(files) < 2:
        raise HTTPException(status_code=400, detail="Provide at least two Excel files to merge.")
    temp_paths = await save_uploads(files, EXCEL_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "excel_merge",
            run_in_threadpool(merge_excel_files, [str(p) for p in temp_paths], str(result_dir)),
        )
        return {"status": "success", "message": "Excel files merged", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        for p in temp_paths:
            if p.exists():
                try: os.remove(p)
                except PermissionError: pass


# --- PPT Routes ---

@app.post("/api/ppt/to-pdf")
async def api_ppt_to_pdf(
    file: UploadFile = File(...),
):
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PPT_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "ppt_to_pdf", run_in_threadpool(ppt_to_pdf, str(temp_path), str(result_dir))
        )
        return {"status": "success", "message": "PPT converted to PDF (best-effort layout)", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try: os.remove(temp_path)
            except PermissionError: pass


@app.post("/api/ppt/to-images")
async def api_ppt_to_images(
    file: UploadFile = File(...),
    fmt: str = Form("png"),
):
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PPT_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        result = await event_log.timed(
            "ppt_to_images", run_in_threadpool(ppt_to_images_zip, str(temp_path), str(result_dir), fmt)
        )
        return {
            "status": "success",
            "message": f"Rendered {result['slide_count']} slide(s)",
            **download_fields(result["output_path"]),
            "slide_count": result["slide_count"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try: os.remove(temp_path)
            except PermissionError: pass


@app.post("/api/ppt/merge")
async def api_merge_pptx(
    files: List[UploadFile] = File(...),
):
    if not files or len(files) < 2:
        raise HTTPException(status_code=400, detail="Provide at least two PPTX files to merge.")
    temp_paths = await save_uploads(files, PPT_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "ppt_merge",
            run_in_threadpool(merge_pptx, [str(p) for p in temp_paths], str(result_dir)),
        )
        return {"status": "success", "message": "PPTX files merged", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Endpoint failed")
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        for p in temp_paths:
            if p.exists():
                try: os.remove(p)
                except PermissionError: pass


@app.post("/api/workflow/execute")
async def execute_workflow(
    file: UploadFile = File(...),
    steps: str = Form(...),
):
    """Execute a multi-step workflow on a file with SSE progress streaming."""
    from fastapi.responses import StreamingResponse

    safe_filename = secure_filename(file.filename)

    # Validate before logging anything: `steps` is a raw, unbounded client
    # form field, so nothing about it is safe to write to the log until it's
    # confirmed to be a bounded list of step objects.
    try:
        step_list = json.loads(steps)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid steps JSON")
    if not isinstance(step_list, list) or not step_list:
        raise HTTPException(status_code=400, detail="steps must be a non-empty list")
    if len(step_list) > MAX_WORKFLOW_STEPS:
        raise HTTPException(status_code=400, detail=f"Too many steps (max {MAX_WORKFLOW_STEPS})")
    if not all(isinstance(s, dict) for s in step_list):
        raise HTTPException(status_code=400, detail="Each step must be an object")

    logger.info("Workflow started: %s, %d step(s)", safe_filename, len(step_list))

    # Saved after the steps parse, so a malformed request never touches disk.
    # The step list decides what the file really has to be, so the intake here
    # takes the union allowlist rather than one tool family's.
    temp_path = await save_upload(file, ALLOWED_EXTENSIONS)
    # One directory for the whole chain: every step's output and every renamed
    # intermediate lands here, and only the final file is ever registered for
    # download, so the intermediates are unreachable rather than merely unlinked.
    result_dir = new_result_dir()

    # Captured here because generate_progress runs while the response streams,
    # outside the middleware's request context.
    wf_country, wf_session = event_log.get_request_context()
    job_id = app.state.jobs.create()
    workflow_events = asyncio.Queue()

    async def generate_progress():
        """Generator for SSE progress events."""
        current_file = temp_path
        # Each non-final step's output is renamed to a uuid name and consumed by
        # the next step. Those intermediates are never the deliverable (the last
        # step's result is never renamed), so they must be removed — on success
        # AND on failure — or they accumulate in OUTPUT_DIR and fill the VM disk
        # (only the periodic stale-file sweep would eventually reap them).
        # Tracked here, deleted in the `finally` below.
        intermediate_files = []

        def log_step(step_type, ok, started, config, err=None):
            # config may be a non-dict if the client sent a malformed step; the
            # operation itself raises on that and we still want to log the
            # failure cleanly rather than crash the SSE stream.
            use_ai = bool(config.get('use_ai', False)) if isinstance(config, dict) else False
            event_log.log_event(
                step_type,
                success=ok,
                duration_ms=(time.perf_counter() - started) * 1000,
                use_ai=use_ai,
                error=err,
                country=wf_country,
                session_id=wf_session,
            )

        step_started = None  # non-None only while a step's processing call runs

        try:
            # Process each step
            for i, step in enumerate(step_list):
                step_type = step.get('type')
                config = step.get('config', {})
                step_label = step.get('label', step_type)

                # Send "processing" event for this step
                yield f"data: {json.dumps({'event': 'step_start', 'step': i, 'total': len(step_list), 'label': step_label})}\n\n"

                logger.debug("Step %d: %s", i+1, step_type)

                step_started = time.perf_counter()

                if step_type == 'remove_password':
                    password = config.get('password', '')
                    if not password:
                        yield f"data: {json.dumps({'event': 'error', 'detail': 'Password required for unlock step'})}\n\n"
                        step_started = None
                        return
                    output_path = await run_in_threadpool(remove_pdf_password, str(current_file), password, str(result_dir))
                    current_file = Path(output_path)
                    
                elif step_type == 'pdf_to_word':
                    use_ai = config.get('use_ai', False)
                    password = config.get('password')
                    if use_ai:
                        output_path = await run_in_threadpool(pdf_to_word_ai, str(current_file), str(result_dir), password)
                    else:
                        output_path = await run_in_threadpool(pdf_to_docx, str(current_file), str(result_dir), password)
                    current_file = Path(output_path)
                    
                elif step_type == 'heic_to_jpeg':
                    quality = config.get('quality', 95)
                    output_path = await run_in_threadpool(heic_to_jpeg, str(current_file), str(result_dir), quality)
                    current_file = Path(output_path)
                    
                elif step_type == 'resize_image':
                    from scripts.image_utils import resize_image
                    mode = config.get('mode', 'percentage')
                    percentage = config.get('percentage', 50)
                    output_path = await run_in_threadpool(
                        resize_image,
                        str(current_file), 
                        str(result_dir), 
                        mode,
                        percentage=percentage
                    )
                    current_file = Path(output_path)
                    
                elif step_type == 'crop_image':
                    from scripts.image_utils import crop_image
                    x = config.get('x', 0)
                    y = config.get('y', 0)
                    width = config.get('width', 100)
                    height = config.get('height', 100)
                    output_path = await run_in_threadpool(
                        crop_image,
                        str(current_file), 
                        str(result_dir), 
                        x=x, y=y, width=width, height=height
                    )
                    current_file = Path(output_path)

                elif step_type == 'compress_pdf':
                    level = config.get('level', 'medium')
                    password = config.get('password') or None
                    result = await run_in_threadpool(compress_pdf, str(current_file), str(result_dir), level, password)
                    current_file = Path(result['output_path'])

                elif step_type == 'rotate_image':
                    angle = config.get('angle', 90)
                    output_path = await run_in_threadpool(rotate_image, str(current_file), str(result_dir), angle)
                    current_file = Path(output_path)

                elif step_type == 'compress_image':
                    quality = config.get('quality', 70)
                    result = await run_in_threadpool(compress_image, str(current_file), str(result_dir), quality)
                    current_file = Path(result['output_path'])

                elif step_type == 'convert_image':
                    target_format = config.get('target_format', 'jpg')
                    quality = config.get('quality', 90)
                    output_path = await run_in_threadpool(
                        convert_image_format, str(current_file), str(result_dir), target_format, quality
                    )
                    current_file = Path(output_path)

                elif step_type == 'watermark_image':
                    text = config.get('text', 'WATERMARK')
                    position = config.get('position', 'bottom-right')
                    opacity = config.get('opacity', 0.4)
                    color = config.get('color', 'white')
                    output_path = await run_in_threadpool(
                        watermark_image, str(current_file), str(result_dir), text, position, opacity, color
                    )
                    current_file = Path(output_path)

                elif step_type == 'excel_to_pdf':
                    output_path = await run_in_threadpool(excel_to_pdf, str(current_file), str(result_dir))
                    current_file = Path(output_path)

                elif step_type == 'csv_to_xlsx':
                    delimiter = config.get('delimiter', ',')
                    output_path = await run_in_threadpool(csv_to_xlsx, str(current_file), str(result_dir), delimiter)
                    current_file = Path(output_path)

                elif step_type == 'xlsx_to_csv':
                    sheet = config.get('sheet') or None
                    output_path = await run_in_threadpool(xlsx_to_csv, str(current_file), str(result_dir), sheet)
                    current_file = Path(output_path)

                elif step_type == 'ppt_to_pdf':
                    output_path = await run_in_threadpool(ppt_to_pdf, str(current_file), str(result_dir))
                    current_file = Path(output_path)

                elif step_type == 'ppt_to_images':
                    fmt = config.get('fmt', 'png')
                    result = await run_in_threadpool(ppt_to_images_zip, str(current_file), str(result_dir), fmt)
                    current_file = Path(result['output_path'])

                elif step_type == 'rotate_pdf':
                    angle = int(config.get('angle', 90))
                    pages = config.get('pages') or None
                    password = config.get('password') or None
                    output_path = await run_in_threadpool(rotate_pdf, str(current_file), str(result_dir), angle, pages, password)
                    current_file = Path(output_path)

                elif step_type == 'protect_pdf':
                    user_pw = config.get('user_password', '')
                    if not user_pw:
                        yield f"data: {json.dumps({'event': 'error', 'detail': 'user_password required for protect_pdf step'})}\n\n"
                        step_started = None
                        return
                    output_path = await run_in_threadpool(
                        protect_pdf, str(current_file), str(result_dir),
                        user_pw, config.get('owner_password'), True, False, False, config.get('password')
                    )
                    current_file = Path(output_path)

                elif step_type == 'word_to_pdf':
                    output_path = await run_in_threadpool(word_to_pdf, str(current_file), str(result_dir))
                    current_file = Path(output_path)

                elif step_type == 'word_to_pptx':
                    dpi = int(config.get('dpi', 150))
                    output_path = await run_in_threadpool(word_to_pptx, str(current_file), str(result_dir), dpi)
                    current_file = Path(output_path)

                elif step_type == 'pdf_to_excel':
                    password = config.get('password') or None
                    result = await run_in_threadpool(pdf_to_excel, str(current_file), str(result_dir), password)
                    current_file = Path(result['output_path'])

                elif step_type == 'pdf_to_pptx':
                    dpi = int(config.get('dpi', 150))
                    password = config.get('password') or None
                    output_path = await run_in_threadpool(pdf_to_pptx, str(current_file), str(result_dir), dpi, password)
                    current_file = Path(output_path)

                elif step_type == 'pdf_to_epub':
                    password = config.get('password') or None
                    output_path = await run_in_threadpool(pdf_to_epub, str(current_file), str(result_dir), password)
                    current_file = Path(output_path)

                elif step_type == 'extract_text':
                    preserve = config.get('preserve_layout', False)
                    password = config.get('password') or None
                    result = await run_in_threadpool(extract_text_from_pdf, str(current_file), str(result_dir), preserve, password)
                    current_file = Path(result['output_path'])

                elif step_type == 'organize_pdf':
                    page_order = config.get('page_order', [])
                    if not page_order:
                        yield f"data: {json.dumps({'event': 'error', 'detail': 'page_order required for organize_pdf step'})}\n\n"
                        step_started = None
                        return
                    password = config.get('password') or None
                    output_path = await run_in_threadpool(organize_pdf, str(current_file), str(result_dir), page_order, password)
                    current_file = Path(output_path)

                elif step_type == 'add_page_numbers':
                    output_path = await run_in_threadpool(
                        add_page_numbers, str(current_file), str(result_dir),
                        config.get('position', 'bottom-center'),
                        int(config.get('start_number', 1)),
                        int(config.get('font_size', 12)),
                        int(config.get('skip_first', 0)),
                        config.get('fmt', 'decimal'),
                        config.get('password') or None,
                    )
                    current_file = Path(output_path)

                elif step_type == 'repair_pdf':
                    result = await run_in_threadpool(repair_pdf, str(current_file), str(result_dir))
                    current_file = Path(result['output_path'])

                elif step_type == 'annotate_pdf':
                    annotations = config.get('annotations', [])
                    password = config.get('password') or None
                    output_path = await run_in_threadpool(annotate_pdf, str(current_file), str(result_dir), annotations, password)
                    current_file = Path(output_path)

                elif step_type == 'edit_metadata':
                    output_path = await run_in_threadpool(
                        edit_pdf_metadata, str(current_file), str(result_dir),
                        config.get('title'), config.get('author'), config.get('subject'),
                        config.get('keywords'), config.get('creator'),
                        bool(config.get('clear_all', False)), config.get('password') or None,
                    )
                    current_file = Path(output_path)

                else:
                    yield f"data: {json.dumps({'event': 'error', 'detail': f'Unknown step type: {step_type}'})}\n\n"
                    step_started = None
                    return

                log_step(step_type, True, step_started, config)
                step_started = None

                # Rename this step's output off its "clean" branded name before the
                # next step runs. The next step's function derives its own output
                # name from the same original stem, so — for a same-extension step
                # (e.g. rotate_pdf after word_to_pdf) — it would otherwise compute
                # the exact path it's about to read from and fail (or, for tools
                # without pikepdf's overwrite guard, silently corrupt it).
                if i < len(step_list) - 1:
                    intermediate_path = result_dir / f"{uuid.uuid4()}_{current_file.name}"
                    current_file = current_file.replace(intermediate_path)
                    intermediate_files.append(current_file)

                # Send "completed" event for this step
                yield f"data: {json.dumps({'event': 'step_complete', 'step': i, 'total': len(step_list), 'label': step_label})}\n\n"

            # Send final success event
            logger.info("Workflow complete: %s", current_file)
            complete = {
                'event': 'complete',
                'message': f'Workflow completed ({len(step_list)} steps)',
                **download_fields(current_file, wf_session),
            }
            yield f"data: {json.dumps(complete)}\n\n"

        except Exception as e:
            if step_started is not None:
                log_step(step_type, False, step_started, config, err=e)
                step_started = None
            logger.exception("Workflow failed for %s", safe_filename)
            yield f"data: {json.dumps({'event': 'error', 'detail': event_log.scrub_paths(str(e))})}\n\n"
        
        finally:
            if step_started is not None:
                return
            # Clean up the upload plus every intermediate step output. The final
            # deliverable is never renamed into intermediate_files, so it stays
            # for the client's follow-up download; only the throwaway temps go.
            for p in (temp_path, *intermediate_files):
                try:
                    if p.exists():
                        os.remove(p)
                except (PermissionError, OSError):
                    pass
            # A chain that errored before producing anything leaves its result
            # directory empty. Drop it here rather than making the sweeper wait
            # out FILE_TTL_SECONDS on a directory nobody can reach — a failed
            # workflow should cost nothing on disk.
            with suppress(OSError):
                result_dir.rmdir()  # refuses to remove a non-empty directory

    async def run_workflow_job():
        """Run independently of the SSE consumer and retain the terminal event."""
        terminal_event = None
        try:
            async for chunk in generate_progress():
                await workflow_events.put(chunk)
                if chunk.startswith("data: "):
                    event = json.loads(chunk[6:].strip())
                    if event.get("event") in {"complete", "error"}:
                        terminal_event = event
                    else:
                        app.state.jobs.set_progress(job_id, event)
        except Exception as exc:
            logger.exception("Detached workflow failed for %s", safe_filename)
            terminal_event = {
                "event": "error",
                "detail": event_log.scrub_paths(str(exc)),
            }
            await workflow_events.put(f"data: {json.dumps(terminal_event)}\n\n")
        finally:
            if terminal_event is None:
                terminal_event = {
                    "event": "error",
                    "detail": "Workflow ended without a result",
                }
                await workflow_events.put(f"data: {json.dumps(terminal_event)}\n\n")
            # generate_progress has completed its own cleanup before this result
            # becomes visible to polling clients or releases the heavy-job gate.
            app.state.jobs.set_result(job_id, terminal_event)
            await workflow_events.put(None)

    async def stream_workflow_events():
        yield f"data: {json.dumps({'event': 'start', 'filename': safe_filename, 'job_id': job_id})}\n\n"
        while True:
            chunk = await workflow_events.get()
            if chunk is None:
                break
            yield chunk

    # Start before returning the response: processing must not depend on the
    # client reading even the first byte of the SSE stream (#202).
    _start_detached(run_workflow_job())

    return StreamingResponse(
        stream_workflow_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-FF-Job-ID": job_id,
        }
    )


# ─────────────────────────────────────────────────────────────
# Feature #53: Protect PDF
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/protect")
async def api_protect_pdf(
    file: UploadFile = File(...),
    user_password: str = Form(...),
    owner_password: str = Form(None),
    allow_print: bool = Form(True),
    allow_copy: bool = Form(False),
    allow_edit: bool = Form(False),
    password: str = Form(None),
):
    """Add password protection and permissions to a PDF."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_protect",
            run_in_threadpool(
                protect_pdf, str(temp_path), str(result_dir),
                user_password, owner_password, allow_print, allow_copy, allow_edit, password or None
            ),
        )
        return {"status": "success", "message": "PDF protected with password", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


# ─────────────────────────────────────────────────────────────
# Feature #54: Image to PDF
# ─────────────────────────────────────────────────────────────

@app.post("/api/image/to-pdf")
async def api_images_to_pdf(
    files: List[UploadFile] = File(...),
    page_size: str = Form("A4"),
    fit_mode: str = Form("fit"),
    margin_pt: int = Form(36),
):
    """Convert one or more images into a single PDF."""
    validate_range("margin_pt", margin_pt, 0, 200)
    temp_paths = await save_uploads(files, IMAGE_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "images_to_pdf",
            run_in_threadpool(
                images_to_pdf, [str(p) for p in temp_paths], str(result_dir), page_size, fit_mode, margin_pt
            ),
        )
        return {"status": "success", "message": f"Created PDF from {len(files)} image(s)", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        for p in temp_paths:
            if p.exists():
                try:
                    os.remove(p)
                except PermissionError:
                    pass


# ─────────────────────────────────────────────────────────────
# Feature #55: Word to PDF
# ─────────────────────────────────────────────────────────────

@app.post("/api/word/to-pdf")
async def api_word_to_pdf(
    file: UploadFile = File(...),
):
    """Convert a Word document (DOCX/DOC) to PDF."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, WORD_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "word_to_pdf", run_in_threadpool(word_to_pdf, str(temp_path), str(result_dir))
        )
        return {"status": "success", "message": "Word document converted to PDF", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/word/to-pptx")
async def api_word_to_pptx(
    file: UploadFile = File(...),
    dpi: int = Form(150),
):
    """Convert a Word document (DOCX/DOC) to a PowerPoint presentation."""
    validate_range("dpi", dpi, 30, 600)
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, WORD_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "word_to_pptx", run_in_threadpool(word_to_pptx, str(temp_path), str(result_dir), dpi)
        )
        return {"status": "success", "message": "Word document converted to PowerPoint", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


# ─────────────────────────────────────────────────────────────
# Feature #56: PDF to Excel
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/to-excel")
async def api_pdf_to_excel(
    file: UploadFile = File(...),
    password: str = Form(None),
):
    """Extract tables from a PDF and convert to Excel."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        result = await event_log.timed(
            "pdf_to_excel",
            run_in_threadpool(pdf_to_excel, str(temp_path), str(result_dir), password or None),
        )
        return {
            "status": "success",
            "message": f"Extracted {result['tables_found']} table(s) to Excel",
            **download_fields(result["output_path"]),
            "tables_found": result["tables_found"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


# ─────────────────────────────────────────────────────────────
# Feature #57: PDF to PowerPoint
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/to-pptx")
async def api_pdf_to_pptx(
    file: UploadFile = File(...),
    dpi: int = Form(150),
    password: str = Form(None),
):
    """Convert PDF pages to a PowerPoint presentation."""
    validate_range("dpi", dpi, 30, 600)
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_to_pptx",
            run_in_threadpool(pdf_to_pptx, str(temp_path), str(result_dir), dpi, password or None),
        )
        return {"status": "success", "message": "PDF converted to PowerPoint", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


# ─────────────────────────────────────────────────────────────
# Feature #60: PDF to EPUB
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/to-epub")
async def api_pdf_to_epub(
    file: UploadFile = File(...),
    password: str = Form(None),
):
    """Convert a PDF into a reflowable EPUB ebook."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_to_epub",
            run_in_threadpool(pdf_to_epub, str(temp_path), str(result_dir), password or None),
        )
        return {"status": "success", "message": "PDF converted to EPUB", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


# ─────────────────────────────────────────────────────────────
# Feature #58: Extract Text from PDF
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/extract-text")
async def api_extract_text(
    file: UploadFile = File(...),
    preserve_layout: bool = Form(False),
    password: str = Form(None),
):
    """Extract all text content from a PDF to a .txt file."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        result = await event_log.timed(
            "pdf_extract_text",
            run_in_threadpool(
                extract_text_from_pdf, str(temp_path), str(result_dir), preserve_layout, password or None
            ),
        )
        return {
            "status": "success",
            "message": f"Text extracted from {result['page_count']} page(s)",
            **download_fields(result["output_path"]),
            "page_count": result["page_count"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/pdf/ocr")
async def api_ocr_pdf(
    file: UploadFile = File(...),
    lang: str = Form("en"),
    password: str = Form(None),
):
    """Create a searchable PDF by adding an invisible OCR text layer."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        result = await event_log.timed(
            "pdf_ocr",
            run_in_threadpool(
                ocr_pdf_to_searchable_pdf, str(temp_path), str(result_dir), password or None, lang
            ),
        )
        return {
            "status": "success",
            "message": f"Searchable PDF created from {result['page_count']} page(s)",
            **download_fields(result["output_path"]),
            "page_count": result["page_count"],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        logger.exception("Searchable PDF OCR failed for %s", safe_filename)
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


# ─────────────────────────────────────────────────────────────
# Feature #59: Organize PDF
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/organize")
async def api_organize_pdf(
    file: UploadFile = File(...),
    page_order: str = Form(""),
    password: str = Form(None),
    remove_pages: str = Form(None),
):
    """Reorder, delete, or duplicate PDF pages. page_order is comma-separated 1-based page numbers.

    remove_pages (e.g. "2,4-6") keeps every other page in order instead."""
    import json as _json
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        # Parse page_order: accepts "1,3,2" or "[1,3,2]"
        raw = page_order.strip()
        if remove_pages and remove_pages.strip():
            order = []
        elif raw.startswith("["):
            order = _json.loads(raw)
        else:
            order = [int(x.strip()) for x in raw.split(",") if x.strip()]

        output_path = await event_log.timed(
            "pdf_organize",
            run_in_threadpool(
                organize_pdf, str(temp_path), str(result_dir), order, password or None, remove_pages or None
            ),
        )
        if remove_pages and remove_pages.strip():
            return {"status": "success", "message": "Pages removed", **download_fields(output_path)}
        return {"status": "success", "message": f"PDF organized ({len(order)} pages in output)", **download_fields(output_path)}
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


# ─────────────────────────────────────────────────────────────
# Feature #60: Add Page Numbers
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/add-page-numbers")
async def api_add_page_numbers(
    file: UploadFile = File(...),
    position: str = Form("bottom-center"),
    start_number: int = Form(1),
    font_size: int = Form(12),
    skip_first: int = Form(0),
    fmt: str = Form("decimal"),
    password: str = Form(None),
    template: str = Form("{n}"),
    end_page: int = Form(0),
):
    """Insert page numbers onto each PDF page."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_add_page_numbers",
            run_in_threadpool(
                add_page_numbers, str(temp_path), str(result_dir),
                position, start_number, font_size, skip_first, fmt, password or None,
                template, end_page,
            ),
        )
        return {"status": "success", "message": "Page numbers added", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


# ─────────────────────────────────────────────────────────────
# Feature #61: Repair PDF
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/repair")
async def api_repair_pdf(
    file: UploadFile = File(...),
):
    """Attempt to recover/repair a corrupted PDF."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        result = await event_log.timed(
            "pdf_repair", run_in_threadpool(repair_pdf, str(temp_path), str(result_dir))
        )
        return {
            "status": "success",
            "message": f"PDF repair status: {result['repair_status']}",
            **download_fields(result["output_path"]),
            "repair_status": result["repair_status"],
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


# ─────────────────────────────────────────────────────────────
# Feature #62: Create PDF from Scratch
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/create-from-text")
async def api_create_pdf_from_text(
    content: str = Form(...),
    title: str = Form("Document"),
    font_size: int = Form(12),
    page_size: str = Form("A4"),
    margin_pt: int = Form(72),
):
    """Create a new PDF from plain text content."""
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_create_from_text",
            run_in_threadpool(
                create_pdf_from_text, str(result_dir), content, title, font_size, page_size, margin_pt
            ),
        )
        return {"status": "success", "message": "PDF created from text", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))


@app.post("/api/pdf/create-blank")
async def api_create_blank_pdf(
    num_pages: int = Form(1),
    page_size: str = Form("A4"),
):
    """Create a blank PDF with the given number of pages."""
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_create_blank", run_in_threadpool(create_blank_pdf, str(result_dir), num_pages, page_size)
        )
        return {"status": "success", "message": f"Created blank PDF with {num_pages} page(s)", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))


# ─────────────────────────────────────────────────────────────
# Feature #63: Annotate / Edit PDF
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/annotate")
async def api_annotate_pdf(
    file: UploadFile = File(...),
    annotations: str = Form(...),
    password: str = Form(None),
):
    """Add annotations (highlight/underline/strikeout/note/text/redact) to a PDF.
    annotations is a JSON array of annotation objects."""
    import json as _json
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        ann_list = _json.loads(annotations)
        if not isinstance(ann_list, list):
            raise ValueError("annotations must be a JSON array.")

        output_path = await event_log.timed(
            "pdf_annotate",
            run_in_threadpool(annotate_pdf, str(temp_path), str(result_dir), ann_list, password or None),
        )
        return {"status": "success", "message": f"Added {len(ann_list)} annotation(s)", **download_fields(output_path)}
    except (ValueError, _json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


# ─────────────────────────────────────────────────────────────
# Feature #64: PDF Metadata Editor
# ─────────────────────────────────────────────────────────────

@app.post("/api/pdf/metadata")
async def api_edit_pdf_metadata(
    file: UploadFile = File(...),
    title: str = Form(None),
    author: str = Form(None),
    subject: str = Form(None),
    keywords: str = Form(None),
    creator: str = Form(None),
    clear_all: bool = Form(False),
    password: str = Form(None),
):
    """Edit PDF metadata (title, author, subject, keywords, creator)."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    result_dir = new_result_dir()
    try:
        output_path = await event_log.timed(
            "pdf_edit_metadata",
            run_in_threadpool(
                edit_pdf_metadata, str(temp_path), str(result_dir),
                title, author, subject, keywords, creator, clear_all, password or None
            ),
        )
        return {"status": "success", "message": "PDF metadata updated", **download_fields(output_path)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


@app.post("/api/pdf/metadata/read")
async def api_read_pdf_metadata(
    file: UploadFile = File(...),
    password: str = Form(None),
):
    """Read metadata from a PDF without modifying it."""
    safe_filename = secure_filename(file.filename)
    temp_path = await save_upload(file, PDF_EXTENSIONS)
    try:
        metadata = await event_log.timed(
            "pdf_read_metadata", run_in_threadpool(get_pdf_metadata, str(temp_path), password or None)
        )
        return {"status": "success", "metadata": metadata}
    except Exception as e:
        raise HTTPException(status_code=400, detail=event_log.scrub_paths(str(e)))
    finally:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except PermissionError:
                pass


def delete_file_after_download(token: str, path: Path) -> None:
    """Retire a result once it has been served.

    Used as a FastAPI BackgroundTask. Removes the whole per-result directory
    (a workflow chain leaves its intermediates in there too) and forgets the
    token, so a replayed URL 404s instead of racing the filesystem.
    """
    app.state.downloads.discard(token)
    try:
        shutil.rmtree(path.parent, ignore_errors=True)
        logger.debug("Deleted result after download: %s", path)
    except OSError:
        logger.exception("Failed to delete result %s", path)


@app.api_route("/api/download/{token}", methods=["GET", "HEAD"])
async def download_file(token: str, request: Request, background_tasks: BackgroundTasks):
    """Serve a finished result by its opaque token.

    The token is the authorization: it is the only thing that maps to a path,
    and it is not derived from anything the client supplied. There is
    deliberately no fallback to a filename lookup — that fallback *was* the
    vulnerability, since output names are fully deterministic.
    """
    if not _DOWNLOAD_TOKEN_RE.match(token):
        raise HTTPException(status_code=404, detail="File not found")
    _, session_id = event_log.get_request_context()
    file_path = app.state.downloads.resolve(token, session_id)
    if file_path is None or not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if request.method == "GET":
        background_tasks.add_task(delete_file_after_download, token, file_path)
    # The branded name never addresses anything; it is only what the browser
    # offers to save the download as.
    return FileResponse(file_path, filename=file_path.name)

# Note: there is deliberately no /admin/stats route in the public app. Only the
# write side of the event log (recording operations + funnel steps) lives here;
# the aggregation/reporting logic and the admin auth gate live in the private
# deployment's server.py, so the public repo never carries an admin surface.


# Substrings identifying automated clients, matched case-insensitively against
# the User-Agent. Secondary by design: the beacon only fires from JavaScript, so
# a plain crawler never reaches this route at all. This catches the ones that do
# execute JS (headless checkers, preview renderers, uptime monitors) and keeps
# them out of the funnel, where a handful of fake sessions visibly skews the
# rates on a site this young.
_BOT_UA_MARKERS = (
    "bot", "crawler", "spider", "slurp", "headlesschrome", "phantomjs",
    "puppeteer", "playwright", "curl/", "wget/", "python-requests",
    "http-client", "lighthouse", "pingdom", "uptimerobot", "monitoring",
)


def _is_bot_user_agent(user_agent: str) -> bool:
    """True when the User-Agent self-identifies as an automated client."""
    ua = (user_agent or "").lower()
    return any(marker in ua for marker in _BOT_UA_MARKERS)


# --- Anonymous funnel beacon (first-party, no third-party analytics) ---
@app.post("/api/track")
async def api_track(request: Request):
    """Record one anonymous navigation/funnel event (page_view, tool_open,
    file_processed, file_downloaded) into the local event log.

    This is the first-party replacement for a third-party analytics beacon: the
    browser POSTs a tiny JSON body ({event, label, ref}) via navigator.sendBeacon,
    and the session id + coarse country come from the request-context middleware
    (this route is under /api/, so the anonymous ff_sid cookie and CF-IPCountry
    header are already applied). No file names, contents, IPs, or PII are ever
    stored.

    `ref` is the browser's document.referrer, and is reduced to a bare host here
    before anything is written — see event_log.referrer_host(). It has to come
    from the client: the Referer header on this POST is our own page, not the
    site the visitor arrived from. Recorded on page_view only, since that is the
    only event where "where did they come from" is a question with an answer.

    Always answers 204: a tracking beacon must never surface an error to the UI,
    and unknown event names are silently ignored inside log_funnel_event().
    """
    from functools import partial

    # Dropped silently, and still a 204 — an automated client shouldn't be able
    # to tell it was filtered, and a real browser must never see an error here.
    if _is_bot_user_agent(request.headers.get("user-agent", "")):
        return Response(status_code=204)

    try:
        body = await request.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        event = str(body.get("event") or "")[:40]
        label = body.get("label")
        label = label if isinstance(label, str) else None
        referrer = None
        if event == "page_view":
            raw_ref = body.get("ref")
            referrer = event_log.referrer_host(
                raw_ref if isinstance(raw_ref, str) else None,
                self_host=urlsplit(BASE_URL).hostname,
            )
        if event:
            await run_in_threadpool(
                partial(event_log.log_funnel_event, event, label=label, referrer=referrer)
            )
    return Response(status_code=204)


# --- SEO Routes ---
# Content pages are static files in static/pages/. Tool pages are server-rendered
# from scripts/seo_content.py (full HTML — no JS required, so JS-less AI crawlers
# read the title/meta/body directly). Both kinds appear in sitemap.xml.
CONTENT_PAGES = ["about", "faq", "contact", "privacy", "terms"]
TOOL_PAGES = seo_content.TOOL_PAGES

# Back-compat: every indexable slug (tests and tooling expect this iterable).
SEO_PAGES = list(TOOL_PAGES.keys()) + CONTENT_PAGES

# AI retrieval/citation crawlers we explicitly welcome, so Forge Files tools are
# eligible to be cited in AI answers (ChatGPT, Perplexity, Gemini, Claude, ...).
AI_CRAWLERS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User",
    "ClaudeBot", "Claude-Web", "anthropic-ai",
    "PerplexityBot", "Perplexity-User",
    "Google-Extended", "Applebot-Extended",
    "Amazonbot", "Bytespider", "Meta-ExternalAgent", "CCBot",
]


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    lines = ["# Forge Files: free, open-source file tools. All pages are public.", ""]
    for bot in AI_CRAWLERS:
        lines += [f"User-agent: {bot}", "Allow: /", "Disallow: /api/", ""]
    lines += ["User-agent: *", "Allow: /", "Disallow: /api/", ""]
    lines.append(f"Sitemap: {BASE_URL}/sitemap.xml")
    return PlainTextResponse("\n".join(lines) + "\n")


@app.get("/sitemap.xml")
async def sitemap_xml():
    lastmod = CONTENT_LAST_MODIFIED
    # (loc, changefreq, priority, actual content modification date)
    entries = [(BASE_URL + "/", "daily", "1.0", lastmod)]
    entries += [(f"{BASE_URL}/{slug}", "weekly", "0.8", lastmod) for slug in TOOL_PAGES
                if slug not in seo_content.CANONICAL_ALIASES]
    entries += [(f"{BASE_URL}/blog", "weekly", "0.6", lastmod)]
    entries += [(f"{BASE_URL}/blog/{slug}", "monthly", "0.6", blog_content.GUIDES[slug]["date"])
                for slug in blog_content.guide_slugs()]
    entries += [(f"{BASE_URL}/{slug}", "monthly", "0.5", lastmod) for slug in CONTENT_PAGES]
    items = "\n".join(
        f"  <url><loc>{loc}</loc><lastmod>{modified}</lastmod>"
        f"<changefreq>{cf}</changefreq><priority>{pr}</priority></url>"
        for loc, cf, pr, modified in entries
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{items}\n</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


# Human-readable category labels for the llms.txt grouping. Keyed by the "tool"
# field in seo_content.TOOL_PAGES; any category added there without an entry here
# falls back to a title-cased version of the key, so this can't 500 on new tools.
_LLMS_CATEGORIES = {
    "pdf": "PDF tools",
    "image": "Image tools",
    "excel": "Spreadsheet tools",
    "ppt": "Presentation tools",
    "word": "Document tools",
}


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """Plain-text site index for LLM crawlers (the llms.txt convention).

    Generated from the same TOOL_PAGES/CONTENT_PAGES data as sitemap.xml, so the
    two can never drift: adding a tool to seo_content.py lists it here for free.
    Descriptions reuse each page's existing `meta` string rather than a second
    hand-written blurb, for the same reason.
    """
    lines = [
        "# Forge Files",
        "",
        "> Free, open-source online file tools: convert, compress, merge, split and "
        "edit PDFs, images and spreadsheets in the browser. No signup, no watermarks, "
        "no file-size paywall. Uploaded files are deleted from the server "
        "automatically after processing.",
        "",
        "Forge Files is free software (source: https://github.com/BhurkeSiddhesh/File-Forge) "
        "and can be self-hosted. Every tool below is a standalone page that works "
        "without JavaScript for reading purposes; no account is ever required.",
        "",
    ]

    by_category: dict = {}
    for slug, page in TOOL_PAGES.items():
        if slug in seo_content.CANONICAL_ALIASES:
            continue
        by_category.setdefault(page.get("tool") or "other", []).append((slug, page))

    for key in sorted(by_category, key=lambda k: -len(by_category[k])):
        lines.append(f"## {_LLMS_CATEGORIES.get(key, key.replace('-', ' ').title())}")
        lines.append("")
        for slug, page in sorted(by_category[key]):
            name = page.get("app") or slug
            lines.append(f"- [{name}]({BASE_URL}/{slug}): {page.get('meta', '')}")
        lines.append("")

    lines += ["## Guides", ""]
    for slug in blog_content.guide_slugs():
        g = blog_content.GUIDES[slug]
        lines.append(f"- [{g['h1']}]({BASE_URL}/blog/{slug}): {g['meta']}")
    lines.append("")

    lines += ["## About", ""]
    lines += [f"- [{slug.title()}]({BASE_URL}/{slug})" for slug in CONTENT_PAGES]
    lines.append("")

    return PlainTextResponse("\n".join(lines))


@app.api_route("/ads.txt", methods=["GET", "HEAD"], response_class=PlainTextResponse)
async def ads_txt():
    line = os.environ.get("ADSENSE_ADS_TXT", "").strip()
    if not line:
        raise HTTPException(status_code=404, detail="ads.txt not configured")
    return PlainTextResponse(line + "\n")


# Root-path icons. Every page already links /static/favicon.svg, but plenty of
# clients never parse the HTML and probe these fixed root paths directly —
# browsers falling back from an unsupported SVG icon, bookmark/feed readers, and
# iOS when a page is added to the home screen. Serving them here (rather than
# adding a <link> to all eight templates) covers the home page, all tool pages
# and all content pages at once. Must stay above the /{slug} catch-all below,
# which would otherwise swallow these as unknown slugs and 404.
@app.get("/on-device-office", include_in_schema=False)
@app.get("/on-device-office/", include_in_schema=False)
async def serve_on_device_office():
    """Isolated, ad-free page that runs the on-device Office engine."""
    return FileResponse(
        BASE_DIR / "static" / "on-device-office" / "index.html",
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex, nofollow"},
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    return FileResponse(
        BASE_DIR / "static" / "favicon.ico",
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
async def apple_touch_icon():
    return FileResponse(
        BASE_DIR / "static" / "apple-touch-icon.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800"},
    )


# --- Blog / guides (long-tail SEO content) ---------------------------------
# Registered ABOVE the /{slug} catch-all so "/blog" isn't swallowed as an unknown
# tool slug. "/blog/{slug}" is two segments so it never collides with /{slug}.
@app.get("/blog", response_class=HTMLResponse)
async def serve_blog_index():
    return HTMLResponse(_substitute(blog_content.render_blog_index()))


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def serve_blog_guide(slug: str):
    if slug in blog_content.GUIDES:
        return HTMLResponse(_substitute(blog_content.render_guide(slug)))
    raise HTTPException(status_code=404, detail="Guide not found")


@app.get("/{slug}", response_class=HTMLResponse)
async def serve_seo_page(slug: str):
    if slug in seo_content.CANONICAL_ALIASES:
        return RedirectResponse(url="/" + seo_content.CANONICAL_ALIASES[slug], status_code=308)
    if slug in TOOL_PAGES:
        return HTMLResponse(_render_tool_page(slug))
    if slug in CONTENT_PAGES:
        return HTMLResponse(_render_page(f"pages/{slug}.html"))
    raise HTTPException(status_code=404, detail="Page not found")


from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Serve a branded, hard-404 HTML page for missing non-API routes (real status
    404 + real internal links, never a soft-404). API routes and every other
    status code keep the default JSON ``detail`` response."""
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return HTMLResponse(content=_substitute(seo_content.render_404_page()), status_code=404)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


if __name__ == "__main__":  # pragma: no cover — manual dev-server entry point
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
