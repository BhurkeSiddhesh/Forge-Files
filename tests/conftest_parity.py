"""Shared helpers for on-device handler parity audits (not collected as tests)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import fitz

HARNESS = Path(__file__).parent / "local_handler_harness.mjs"
NODE = shutil.which("node")


def run_local(tmp_path: Path, api_path: str, files: list[dict], fields: dict | None = None) -> tuple[dict, Path]:
    """Run the real on-device handler; returns (result json, output path)."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"files": files, "fields": fields or {}}))
    out = tmp_path / "local_result.bin"
    if out.exists():
        out.unlink()
    proc = subprocess.run(
        [NODE, str(HARNESS), api_path, str(spec), str(out)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1]), out


def make_pdf(path: Path, pages: int | None = None, sizes: list | None = None, rotations: dict | None = None) -> Path:
    """A PDF whose page i says 'Page i' and (optionally) has its own size and rotation."""
    pages = pages if pages is not None else (len(sizes) if sizes else 5)
    doc = fitz.open()
    for i in range(pages):
        w, h = (sizes[i] if sizes else (595, 842))
        page = doc.new_page(width=w, height=h)
        page.insert_text((72, 100), f"Page {i + 1}", fontsize=18)
    for idx, rot in (rotations or {}).items():
        doc[idx].set_rotation(rot)
    doc.save(str(path))
    doc.close()
    return path


def page_facts(path: Path) -> list[dict]:
    """Independent read-back of each page: text, size, rotation, media/crop box."""
    doc = fitz.open(str(path))
    facts = []
    for page in doc:
        facts.append(
            {
                "text": page.get_text().strip(),
                "rect": [round(v, 1) for v in page.rect],
                "rotation": page.rotation,
                "mediabox": [round(v, 1) for v in page.mediabox],
                "cropbox": [round(v, 1) for v in page.cropbox],
            }
        )
    doc.close()
    return facts
