# Forge Files for Windows

The full Forge Files app in a native window. Everything runs on the user's own computer: the
server listens on `127.0.0.1` only, files are never uploaded, and the page is locked down
with a Content-Security-Policy so it cannot contact any other host. The one exception is Send &
Receive: after a one-time consent prompt it opens a WebSocket to the website's signaling hub
(`wss://www.forgefiles.org/api/transfer/ws`, override with `FF_TRANSFER_ORIGIN`) to find the other
device, and the files then travel peer to peer, never through our servers.

| File | What it is |
|---|---|
| `launcher.py` | Starts the local server and opens the window |
| `forgefiles.spec` | PyInstaller recipe (bundles Python and every library) |
| `installer.iss` | Inno Setup script (per-user install, uses the 2 MB WebView2 bootstrapper, which downloads the runtime only on a PC that lacks it) |
| `VERSION` | The app's version. `release.py` sets it; CI re-stamps it from the release tag (`v1.2.3` becomes `1.2.3`) |
| `../scripts/desktop_engine.py` | Downloads the optional 247 MB Office engine after the person agrees (see "Keeping the installer small") |
| `release.py` | Bumps `VERSION` and the update feed together (see below) |

Builds run in GitHub Actions (`.github/workflows/desktop-windows.yml`) so anyone can check that the
download matches the source. Locally: `pip install -r desktop/requirements-desktop.txt`, then
`pyinstaller desktop/forgefiles.spec --noconfirm --distpath desktop/dist --workpath desktop/build`.

## Publishing an update

The installed app is a snapshot of the site, so website changes reach desktop users only through a new
release. One command keeps the installer and the update notice in step:

1. From `public/`: `python desktop/release.py 0.2.0 --notes "Faster PDF to Word."`. It sets
   `desktop/VERSION` and `static/desktop/feed.json` (`latest_version`, `notes`) to the same version.
2. Commit, then take it through `development` and `main` like any change. Once the website serves the new
   feed, installed apps show the bell next to the theme toggle (and "Check for updates" finds it).
3. Tag that commit as soon as it is on `main`: `git tag v0.2.0 <commit> && git push origin v0.2.0`. CI builds
   `ForgeFiles-Setup-0.2.0.exe` and refuses to build if the tag and the feed disagree, so an installer can
   never ship without being announced. Deploy it to production from the workflow as before.

Step 3 should follow step 2 closely: people who click the bell before the new installer is deployed are
sent to `/download`, which still serves the previous one.

## The feed: `static/desktop/feed.json`

The app downloads this one small public file (default
`https://www.forgefiles.org/static/desktop/feed.json`; override with `FF_FEED_URL`).

```json
{
  "schema": 1,
  "latest_version": "0.2.0",
  "download_url": "https://www.forgefiles.org/download",
  "notes": "Faster PDF to Word.",
  "announcements": [
    {
      "id": "launch-acme-1",
      "title": "Product launch: Acme Scanner",
      "text": "One or two plain sentences. Same for everyone, never personalised.",
      "url": "https://example.com/acme",
      "cta": "See the launch",
      "starts": "2026-10-15",
      "expires": "2026-11-15"
    }
  ]
}
```

- Every field is optional. Anything malformed is ignored, never shown.
- Only the first announcement that is currently live (between `starts` and `expires`) is shown.
- Give each announcement a new `id`; a person who closed one will not see it again, but will see the next.
- Links must be plain `https://`. HTML in text is stripped.
- To remove an announcement, delete it or let it expire. To stop update notices, leave `latest_version` at the current release.

## Keeping the installer small

Two things are deliberately **not** in the installer, because together they were over half of it:

- **The Office engine** (LibreOffice WASM, `soffice.wasm` + `soffice.data`, 247 MB). On first launch a card asks
  once: *Add the Office engine?* On **Download** the local server fetches the two files in the background from
  `https://www.forgefiles.org/static/vendor/lo-wasm/<version>/` (override `FF_ENGINE_BASE`), checks size and SHA-256
  against the bundled `MANIFEST.json`, and keeps them in `%LOCALAPPDATA%\ForgeFiles\engines\`. It serves them at
  the same URL the website uses, so Word/Excel/PowerPoint to PDF work unchanged and offline afterwards. A failed
  or corrupt download keeps nothing and can be retried; finished files are not fetched again. *Don't ask again*
  is remembered. `FF_ENGINE_DOWNLOAD=0` switches it off. Until it is downloaded those tools say so.
- **The WebView2 runtime.** The installer carries Microsoft's 2 MB bootstrapper, which downloads the runtime only on a
  PC that lacks it (Windows 11 has it). The old 215 MB offline installer is still used if a build fetches that.

The PyInstaller spec also keeps out libraries that only get in when the build machine's Python is shared with other
projects (scipy, numba, pyarrow, boto, grpc, ...) and OpenCV's video DLLs. OCR, Excel and PDF to Word were checked
with those blocked. Building in a clean virtualenv would shrink it further.

## Privacy

The app makes two kinds of request, both plain `GET`s from the local server (never the page): the update feed above,
and, only after the person clicks Download, the two Office engine files. The feed request is a plain `GET` of that file with the user agent
`ForgeFiles-Desktop/<version>`: no cookies, no identifiers, no file names or contents. It is made by
the local server, not the page. People can switch it off with the "Turn off update checks" link on the
update card, or by setting `FF_FEED=0`. If they are offline, the app shows the last copy it saved, or
nothing. Links in the app open in the normal browser, never inside the app window.
