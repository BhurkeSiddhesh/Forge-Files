"""Work package 40: Workflow interop tests between on-device runner and server.

Verifies:
  1. Pure local workflow executes sequentially on-device and produces valid output verifiable by Python.
  2. Hybrid workflow declines cleanly without upload when consent is refused.
  3. Server endpoint parity and error contract.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = REPO_ROOT / "public" / "static"

HARNESS_SCRIPT = """
import vm from 'node:vm';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const [, staticDir, apiPath, specPath, outPath] = process.argv;
const spec = JSON.parse(readFileSync(specPath, 'utf8'));

const URLShim = function (...a) { return new URL(...a); };
URLShim.createObjectURL = () => 'blob:mock/1';
URLShim.revokeObjectURL = () => {};

const sandbox = {
    console, Blob, File, FormData, Response, TextEncoder, TextDecoder,
    Buffer, Uint8Array, ArrayBuffer, ReadableStream,
    FileReader: class {
        readAsArrayBuffer(b) { b.arrayBuffer().then(buf => { this.result = buf; this.onload && this.onload(); }); }
        readAsText(b) { b.text().then(txt => { this.result = txt; this.onload && this.onload(); }); }
    },
    Image: class {}, URL: URLShim, setTimeout, clearTimeout, AbortController,
    document: {
        currentScript: null, head: { appendChild() {} },
        createElement: (t) => t === 'canvas' ? { toBlob() {}, getContext: () => null } : {}
    },
    fetch: (url, init) => {
        return Promise.resolve(new Response('data: {"event":"complete","status":"success"}\\n\\n', {
            status: 200, headers: { 'Content-Type': 'text/event-stream' }
        }));
    }
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.window.apiUrl = p => p;
vm.createContext(sandbox);

for (const f of [
    'vendor/exceljs.min.js',
    'vendor/jszip.min.js',
    'local/ff-server-gate.js',
    'local/ff-local.js',
    'local/ops-excel.js',
    'local/ops-workflow.js'
]) {
    vm.runInContext(readFileSync(join(staticDir, f), 'utf8'), sandbox, { filename: f });
}

let asked = null;
sandbox.ffConsent.handler = (info, req) => {
    asked = req || info;
    return spec.consent === true;
};

const fd = new FormData();
for (const f of (spec.files || [])) {
    fd.append(f.field, new Blob([readFileSync(f.path)]), f.name || 'file.bin');
}
for (const [k, v] of Object.entries(spec.fields || {})) {
    fd.append(k, String(v));
}

const res = await sandbox.ffProcess(apiPath, fd);

// Read events if event-stream
const events = [];
if (res.headers.get('content-type')?.includes('text/event-stream')) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\\n\\n');
        buffer = lines.pop();
        for (const line of lines) {
            if (line.startsWith('data: ')) {
                events.push(JSON.parse(line.substring(6)));
            }
        }
    }
}

const completeEvent = events.find(e => e.event === 'complete');
if (completeEvent && completeEvent.download_token) {
    const item = sandbox.ffLocal.resolve(completeEvent.download_token);
    if (item && item.blob) {
        writeFileSync(outPath, Buffer.from(await item.blob.arrayBuffer()));
    }
}

const out = {
    status: res.status,
    events: events,
    asked: asked,
};
console.log(JSON.stringify(out));
"""


def _run_local_workflow(tmp_path: Path, files: list[dict], steps: list[dict], consent: bool = False) -> tuple[dict, Path]:
    spec_path = tmp_path / "wf_spec.json"
    spec_path.write_text(
        json.dumps({
            "files": files,
            "fields": {"steps": json.dumps(steps)},
            "consent": consent,
        }),
        encoding="utf-8",
    )
    out_path = tmp_path / "out_workflow_result.bin"
    if out_path.exists():
        out_path.unlink()

    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", HARNESS_SCRIPT, str(STATIC_DIR), "/api/workflow/execute", str(spec_path), str(out_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, f"Node script error: {proc.stderr}\n{proc.stdout}"
    output_line = proc.stdout.strip().splitlines()[-1]
    return json.loads(output_line), out_path


def test_pure_local_workflow_roundtrip(tmp_path: Path):
    """Local workflow runs csv_to_xlsx followed by xlsx_to_csv, keeping intermediate in memory."""
    csv_file = tmp_path / "flow_input.csv"
    csv_file.write_text("Product,Quantity\nLaptop,5\nMouse,25\n", encoding="utf-8")

    steps = [
        {"type": "csv_to_xlsx", "label": "CSV to XLSX", "config": {}},
        {"type": "xlsx_to_csv", "label": "XLSX to CSV", "config": {}},
    ]

    result, out_file = _run_local_workflow(
        tmp_path,
        [{"field": "file", "path": str(csv_file), "name": "flow_input.csv"}],
        steps,
    )

    assert result["status"] == 200
    events = result["events"]
    assert any(e.get("event") == "start" for e in events)
    assert any(e.get("event") == "complete" and e.get("local") is True for e in events)

    # Output file exists and matches roundtrip CSV content
    assert out_file.exists()
    with out_file.open("r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
    assert reader[0] == ["Product", "Quantity"]
    assert reader[1] == ["Laptop", "5"]
    assert reader[2] == ["Mouse", "25"]


def test_hybrid_workflow_declined_stops_cleanly(tmp_path: Path):
    """Hybrid workflow stops on server step when consent is declined."""
    csv_file = tmp_path / "decline_input.csv"
    csv_file.write_text("A,B\n1,2\n", encoding="utf-8")

    steps = [
        {"type": "csv_to_xlsx", "label": "Step 1 Local", "config": {}},
        {"type": "excel_to_pdf", "label": "Step 2 Server Only", "config": {}},
    ]

    result, _ = _run_local_workflow(
        tmp_path,
        [{"field": "file", "path": str(csv_file), "name": "decline_input.csv"}],
        steps,
        consent=False,
    )

    assert result["status"] == 200
    events = result["events"]
    error_event = next((e for e in events if e.get("event") == "error"), None)
    assert error_event is not None
    assert "Cancelled. Your file was not uploaded." in error_event["detail"]

    # Asked consent naming the step and intermediate
    asked = result["asked"]
    assert asked is not None
    assert "Step 2 Server Only" in asked["reason"]


def test_server_workflow_contract():
    """Verify server /api/workflow/execute returns 400 on empty steps."""
    client = TestClient(app)
    response = client.post(
        "/api/workflow/execute",
        files={"file": ("dummy.txt", b"hello", "text/plain")},
        data={"steps": "[]"},
    )
    assert response.status_code == 400
    assert "steps must be a non-empty list" in response.json()["detail"]
