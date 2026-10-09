# Forge Files for Windows

The full Forge Files app in a native window. Everything runs on the user's own computer: the
server listens on `127.0.0.1` only, files never leave the machine, and the page is locked down
with a Content-Security-Policy so it cannot contact any other host.

| File | What it is |
|---|---|
| `launcher.py` | Starts the local server and opens the window |
| `forgefiles.spec` | PyInstaller recipe (bundles Python and every library) |
| `installer.iss` | Inno Setup script (per-user install, bundles the WebView2 runtime) |
| `VERSION` | The app's version. CI stamps it from the release tag (`v1.2.3` becomes `1.2.3`) |

Builds run in GitHub Actions (`.github/workflows/desktop-windows.yml`) so anyone can check that the
download matches the source. Locally: `pip install -r desktop/requirements-desktop.txt`, then
`pyinstaller desktop/forgefiles.spec --noconfirm --distpath desktop/dist --workpath desktop/build`.

## Publishing an update

1. Tag a release: `git tag v0.2.0 && git push origin v0.2.0`. CI builds `ForgeFiles-Setup-0.2.0.exe`.
2. Host the installer somewhere people can download it and put that link in `/download`.
3. Edit `static/desktop/feed.json` (below) and deploy the website. Installed apps pick it up at their
   next launch (checks are cached for 6 hours) and show a "Version 0.2.0 is available" card.

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

## Privacy

The only network request the app makes is a plain `GET` of that file with the user agent
`ForgeFiles-Desktop/<version>`: no cookies, no identifiers, no file names or contents. It is made by
the local server, not the page. People can switch it off with the "Turn off update checks" link on the
update card, or by setting `FF_FEED=0`. If they are offline, the app shows the last copy it saved, or
nothing. Links in the app open in the normal browser, never inside the app window.
