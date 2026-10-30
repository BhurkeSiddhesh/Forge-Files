# PyInstaller spec for the Windows desktop build.  Run from public/:
#   pyinstaller desktop/forgefiles.spec --noconfirm
# Produces dist/ForgeFiles/ForgeFiles.exe (onedir: starts far faster than onefile).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

HERE = Path(SPECPATH)
ROOT = HERE.parent  # public/: the app this installer packages

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "scripts" / "tool_registry.json"), "scripts"),
    (str(HERE / "VERSION"), "."),
]
binaries = []
hiddenimports = ["main"]
hiddenimports += collect_submodules("scripts")
hiddenimports += collect_submodules("uvicorn")

# Packages that ship data files / native libs PyInstaller's static analysis misses.
# collect_all would import every submodule (and hang on GUI backends we do not use),
# so collect only data + DLLs, and name the few hidden imports explicitly.
for pkg in ("rapidocr", "onnxruntime", "pymupdf", "pikepdf", "pdf2docx",
            "docxcompose", "babel", "webview", "cv2", "pillow_heif", "reportlab",
            "pythonnet", "clr_loader"):
    try:
        datas += collect_data_files(pkg)
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass
hiddenimports += ["webview.platforms.edgechromium", "webview.platforms.winforms", "clr"]

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tkinter", "paddle", "paddleocr", "matplotlib", "IPython",
              # RapidOCR can use these as alternative backends; we ship ONNX Runtime only.
              "torch", "torchvision", "torchaudio", "tensorflow", "tensorrt", "openvino",
              "rapidocr.inference_engine.torch", "rapidocr.inference_engine.paddle",
              # Nothing in requirements.txt uses these. They only arrive when the build machine's Python has
              # unrelated projects installed (a shared interpreter pulled in about 300 MB of them), so keep
              # them out explicitly. Excel, PDF to Word and OCR were verified with all of them blocked.
              "scipy", "numba", "llvmlite", "pyarrow", "botocore", "boto3", "s3transfer",
              "grpc", "grpc_tools", "sklearn", "skimage"],
)
# The on-device Office engine (soffice.wasm + soffice.data, 247 MB) stays out of the installer; the app downloads
# it after the person agrees (scripts/desktop_engine.py), checked against MANIFEST.json.
_ENGINE_FILES = ("soffice.wasm", "soffice.data")
a.datas = [d for d in a.datas if not (str(d[0]).replace("\\", "/").startswith("static/vendor/lo-wasm/")
                                      and Path(str(d[0])).name in _ENGINE_FILES)]
# OpenCV ships two ffmpeg DLLs (about 54 MB) for reading video; the app only decodes still images.
a.binaries = [b for b in a.binaries if not Path(b[0]).name.lower().startswith("opencv_videoio_ffmpeg")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ForgeFiles",
    console=False,
    icon=str(HERE / "forgefiles.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="ForgeFiles")
