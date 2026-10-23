"""Work package 00: every upload route has a consent description and policy."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = json.loads((ROOT / "scripts" / "tool_registry.json").read_text(encoding="utf-8"))
CONSENT_JS = (ROOT / "static" / "local" / "ff-server-gate.js").read_text(encoding="utf-8")
SCRIPT_JS = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

# Support routes that never carry a user file.
NON_UPLOAD_SUPPORT = {
    "/api/jobs/{job_id}",
    "/api/ai-capabilities",
    "/api/track",
    "/api/download/{token}",
    "/premium/jobs/{job_id}",
    "/premium/jobs/{job_id}/files/{name}",
}


# Send & Receive never uploads: its only route is the WebRTC signaling socket,
# and file bytes go peer to peer. These tools must say so, not borrow the gate.
PEER_TO_PEER_TOOLS = {"send_files", "receive_files"}
SIGNALING_ROUTE = "/api/transfer/ws"


def _consent_paths() -> set[str]:
    return set(re.findall(r"^\s*'(/[^']+)': \[", CONSENT_JS, flags=re.M))


def test_every_tool_declares_an_execution_policy_requiring_consent() -> None:
    for tool in REGISTRY["tools"]:
        execution = tool.get("execution")
        assert execution, f"{tool['id']} has no execution policy"
        if tool["id"] in PEER_TO_PEER_TOOLS:
            continue
        assert execution["requires_server_consent"] is True
        assert execution["preferred"] in {"browser", "server"}


def test_peer_to_peer_tools_cannot_upload() -> None:
    tools = [tool for tool in REGISTRY["tools"] if tool["id"] in PEER_TO_PEER_TOOLS]
    assert {tool["id"] for tool in tools} == PEER_TO_PEER_TOOLS
    for tool in tools:
        assert tool["processing"] == "local"
        assert tool["execution"] == {**tool["execution"], "preferred": "browser", "requires_server_consent": False}
        assert tool["api_paths"] == [SIGNALING_ROUTE]
    transfer_js = (ROOT / "static" / "local" / "transfer.js").read_text(encoding="utf-8")
    assert "fetch(" not in transfer_js and "XMLHttpRequest" not in transfer_js and "ffProcess" not in transfer_js


def test_every_upload_route_has_a_consent_description() -> None:
    paths = _consent_paths()
    for tool in REGISTRY["tools"]:
        if tool["id"] in PEER_TO_PEER_TOOLS:
            continue
        for api_path in tool["api_paths"]:
            assert api_path in paths, f"{api_path} ({tool['id']}) has no consent reason"
    for support in REGISTRY["support_api_routes"]:
        if support["path"] in NON_UPLOAD_SUPPORT:
            continue
        assert support["path"] in paths, f"{support['path']} has no consent reason"


def test_no_upload_bypasses_the_consent_gate_in_the_web_app() -> None:
    # Only non-file support calls may use fetch(apiUrl(...)) directly, plus the
    # single gated fetch inside ffServerFetch.
    allowed = ("/api/ai-capabilities", "/api/jobs/")
    for match in re.finditer(r"fetch\(apiUrl\(([^)]*)\)", SCRIPT_JS):
        arg = match.group(1)
        if any(a in arg for a in allowed):
            continue
        assert arg.strip() == "path", f"ungated upload call: fetch(apiUrl({arg}))"


def test_consent_module_loads_before_the_dispatcher() -> None:
    assert INDEX.index("ff-server-gate.js") < INDEX.index("ff-local.js") < INDEX.index("/static/script.js")
