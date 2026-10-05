# Vendored third-party browser libraries

Checked in rather than pulled from a CDN because the Capacitor app has no
network guarantee at load time (the whole point of on-device processing is that
it works offline) and a CDN origin would have to be allowed through the app's
content-security policy. Vendoring keeps the bundle self-contained.

| File | Package | Version | License |
|---|---|---|---|
| `pdf-lib.min.js` | [`pdf-lib`](https://www.npmjs.com/package/pdf-lib) | 1.17.1 | MIT (`pdf-lib.LICENSE.md`) |
| `jszip.min.js` | [`jszip`](https://www.npmjs.com/package/jszip) | 3.10.2 | MIT OR GPL-3.0-or-later, used under MIT (`jszip.LICENSE.md`) |
| `exceljs.min.js` | [`exceljs`](https://www.npmjs.com/package/exceljs) | 4.4.0 | MIT (`exceljs.LICENSE.md`) |
| `pdfjs/` | [`pdfjs-dist`](https://www.npmjs.com/package/pdfjs-dist) | 6.4.299 | Apache-2.0 (`pdfjs/LICENSE`; wasm decoders carry their own `LICENSE_*` files) |
| `qpdf/` | [`@neslinesli93/qpdf-wasm`](https://www.npmjs.com/package/@neslinesli93/qpdf-wasm) | 0.3.0 | ISC (`qpdf/package.json`) |
| `libheif/heic-to.js` | [`heic-to`](https://www.npmjs.com/package/heic-to) / libheif | 1.6.5 / 1.23.5 | LGPL-3.0 (`libheif/LICENSE`) |
| `docx.iife.js` | [`docx`](https://www.npmjs.com/package/docx) | 9.8.1 | MIT (`docx.LICENSE.md`) |
| `pptxgen.min.js` | [`pptxgenjs`](https://www.npmjs.com/package/pptxgenjs) | 4.0.1 | MIT (`pptxgenjs.LICENSE.md`) |
| `tesseract/` | [`tesseract.js`](https://www.npmjs.com/package/tesseract.js) + [`tesseract.js-core`](https://www.npmjs.com/package/tesseract.js-core) + English model | 7.0.0 / 7.0.0 / `4.0.0_best_int` | Apache-2.0 (`tesseract/LICENSE.*.md`) |

`exceljs.min.js` is the untouched `dist/exceljs.min.js`; it defines `window.ExcelJS` and is loaded on demand
by `static/local/ff-local.js` (`loadExcelJs`) the first time an Excel/spreadsheet tool runs on-device.
Its SHA-256 is `7e49da68588e250dbb8bba190d2caa8ab3787cc0284bda1d8b2f805c4df742c9`.

`jszip.min.js` is the untouched `dist/jszip.min.js`; it defines `window.JSZip` and is loaded on demand
by `static/local/ff-local.js` (`loadJsZip`) the first time a tool builds a ZIP on-device. Its SHA-256 is
`7f839b2d4688b845c105ebf5d2f9803075f91ea0fe72bdaac176c3a04dd3d2c1`.

`docx.iife.js` is the untouched `dist/index.iife.js` from the `docx@9.8.1` npm tarball (tarball SHA-1 `e0277f846ebc1a1c9f819b5085bfedeebf84df1a`
matches the registry); it defines `window.docx` and is loaded on demand by `static/local/ops-pdf-word.js` the first time PDF to Word runs
on-device, so no other page pays its ~1.2 MB. Its SHA-256 is `1a1c55af6242bc9bc1d89cba76f656e79e9b4b3c43228e17bee8cda0b9dffad1`.

`pptxgen.min.js` is the untouched `dist/pptxgen.min.js` from the `pptxgenjs@4.0.1` npm tarball (tarball SHA-1
`cd0f202f62f74d950bcd217e90b766da8f73742e` matches the registry); it defines `window.PptxGenJS`, needs the global `JSZip` above, and is loaded
on demand by `static/local/ops-pdf-pptx.js` the first time PDF to PowerPoint runs on-device. Its SHA-256 is `097f0b92e15035a72bba72b59ef1ece62ab45ec6075ac85fe0e2d80d3f59b8e3`.

`tesseract/` is the on-device OCR engine for OCR PDF, loaded only when that tool first runs (never from a CDN: the worker, the core and the
language data are all same-origin). Files: `tesseract.min.js` (defines `window.Tesseract`) and `worker.min.js` from `tesseract.js@7.0.0`
(tarball SHA-1 `4106fb6245efab40c57b94bc1798368807526be8`); `core/tesseract-core-simd-lstm.wasm.js` and the non-SIMD fallback
`core/tesseract-core-lstm.wasm.js` from `tesseract.js-core@7.0.0` (SHA-1 `596aa1ab5c130adab12f21059e6aa1a1cecc0bab`; the relaxed-SIMD, non-LSTM and
separate `.wasm` builds are deliberately not shipped); and `lang/eng.traineddata.gz`, the `4.0.0_best_int` English model from
`@tesseract.js-data/eng@1.0.0` (SHA-1 `285a3f1fb419e8e67bdee93ce288b02bb9097f0a`), about 2.8 MiB.
`tesseract/MANIFEST.json` records every file's package, version, licence, size and SHA-256; the loader refuses to run if a hash in it does not match.
Only English ships on-device: Hindi, Marathi, Tamil and Telugu stay on the server until their accuracy is proven.

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

## pdf.js (`pdfjs/`)

An ES-module build, so it is `import()`ed by `static/local/ff-local.js` (`loadPdfJs`), not script-tagged,
and the server must send `.mjs` as `text/javascript` (`public/main.py` registers it; covered by
`tests/test_static_module_mime.py`). Contents, all same-origin and never fetched from a CDN:

| Path | Why |
|---|---|
| `pdf.min.mjs`, `pdf.worker.min.mjs` | the library and its worker (about 1.7 MB) |
| `wasm/` | JPEG 2000 and JBIG2 image decoders |
| `standard_fonts/` | metrics for the 14 standard PDF fonts when a file does not embed them |
| `cmaps/` | CJK character maps for non-embedded CJK fonts |
| `iccs/` | ICC profile for CalRGB colour handling |

Update with `npm pack pdfjs-dist@<version>`, copy `build/pdf.min.mjs`, `build/pdf.worker.min.mjs`, `LICENSE`,
`cmaps/`, `standard_fonts/`, `wasm/`, `iccs/` into `pdfjs/`, then bump `PDFJS_VERSION` in `ff-local.js`.
Total size is about 5.3 MB, loaded only when PDF to JPG/PNG runs on-device.

## Phase 3 engines

`qpdf/qpdf.js` and `qpdf/qpdf.wasm` are the untouched 0.3.0 distribution used
only after a user starts structural PDF compression. SHA-256:
`c0e8fe62e0c3385dd8cb5d6b613f74d87a4138a3a3343e2add45a067a14d0884`
and `abd933f4ccace4f732999381b21aec8b7e3726f18a5b167fafd57f88dd440876`.

`libheif/heic-to.js` is the untouched IIFE build of heic-to 1.6.5, containing
libheif 1.23.5, and is loaded only for HEIC/HEIF conversion. Its SHA-256 is
`c94d3bce5d9886be1989c270e53c98585ba67af1863fc156b4c72a27c4a18bc1`.
The LGPL-covered file remains separate so it can be replaced independently.
