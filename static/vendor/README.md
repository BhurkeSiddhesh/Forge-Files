# Vendored third-party browser libraries

Checked in rather than pulled from a CDN because the Capacitor app has no
network guarantee at load time (the whole point of on-device processing is that
it works offline) and a CDN origin would have to be allowed through the app's
content-security policy. Vendoring keeps the bundle self-contained.

| File | Package | Version | License |
|---|---|---|---|
| `pdf-lib.min.js` | [`pdf-lib`](https://www.npmjs.com/package/pdf-lib) | 1.17.1 | MIT (`pdf-lib.LICENSE.md`) |
| `jszip.min.js` | [`jszip`](https://www.npmjs.com/package/jszip) | 3.10.2 | MIT OR GPL-3.0-or-later, used under MIT (`jszip.LICENSE.md`) |

`jszip.min.js` is the untouched `dist/jszip.min.js`; it defines `window.JSZip` and is loaded on demand
by `static/local/ff-local.js` (`loadJsZip`) the first time a tool builds a ZIP on-device. Its SHA-256 is
`7f839b2d4688b845c105ebf5d2f9803075f91ea0fe72bdaac176c3a04dd3d2c1`.

`pdf-lib.min.js` is the untouched UMD build (`dist/pdf-lib.min.js` from the npm
tarball); it defines `window.PDFLib`. It is **not** loaded by `index.html` — it
is fetched on demand the first time a PDF is processed on-device, so visitors
who never touch a PDF tool never pay its ~512 KB (see
`static/local/ff-local.js` → `ffLocalLoadPdfLib`).

## Updating

```bash
npm pack pdf-lib@<version>
tar xzf pdf-lib-<version>.tgz package/dist/pdf-lib.min.js package/LICENSE.md
cp package/dist/pdf-lib.min.js  public/static/vendor/pdf-lib.min.js
cp package/LICENSE.md           public/static/vendor/pdf-lib.LICENSE.md
```

Then bump the `?v=` cache-buster on the loader in `static/local/ff-local.js`
and the version in the table above.

Expected checksum of the current file:

```
0f9a5cad07941f0826586c94e089d89b918c46e5c17cf2d5a3c6f666e3bc694f  pdf-lib.min.js
```
