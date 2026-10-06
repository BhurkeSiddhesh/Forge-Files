"""Dropped workflow streams remain recoverable through the shared job registry."""
from __future__ import annotations

import asyncio
import io
import json
import threading
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

import main


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _sse_event(chunk) -> dict:
    if isinstance(chunk, bytes):
        chunk = chunk.decode()
    assert chunk.startswith("data: ")
    return json.loads(chunk[6:].strip())


async def _wait_for_job(job_id: str, timeout: float = 3.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        entry = main.app.state.jobs.get(job_id)
        if entry and entry["status"] == "done":
            return entry
        await asyncio.sleep(0.01)
    raise AssertionError("workflow job did not reach a terminal state")


@pytest.mark.anyio
async def test_workflow_finishes_after_sse_consumer_disconnects(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    uploads.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(main, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(main, "OUTPUT_DIR", outputs)

    started = threading.Event()
    finish = threading.Event()
    observed_input = {}

    def slow_rotate(input_path, output_dir, angle, pages, password):
        observed_input["path"] = Path(input_path)
        started.set()
        assert finish.wait(2), "test never released the workflow step"
        target = Path(output_dir) / "result_forgefiles.org.pdf"
        target.write_bytes(Path(input_path).read_bytes())
        return str(target)

    monkeypatch.setattr(main, "rotate_pdf", slow_rotate)
    upload = UploadFile(filename="shared.pdf", file=io.BytesIO(b"%PDF-1.4 test"))
    response = await main.execute_workflow(
        file=upload,
        steps=json.dumps([{"type": "rotate_pdf", "config": {"angle": 90}}]),
    )

    stream = response.body_iterator
    start = _sse_event(await anext(stream))
    assert start["event"] == "start"
    assert response.headers["X-FF-Job-ID"] == start["job_id"]
    await stream.aclose()  # simulate a dropped browser/mobile SSE connection

    assert await asyncio.to_thread(started.wait, 2)
    assert observed_input["path"].exists(), "disconnect deleted the active upload"
    pending = main.app.state.jobs.get(start["job_id"])
    assert pending["status"] == "pending"
    assert pending["progress"]["event"] == "step_start"

    finish.set()
    job = await _wait_for_job(start["job_id"])
    assert job["event"]["event"] == "complete"
    assert job["event"]["download_token"]
    assert not observed_input["path"].exists()
    result = main.app.state.downloads.resolve(job["event"]["download_token"], None)
    assert result is not None and result.exists()


@pytest.mark.anyio
async def test_workflow_failure_is_stable_after_disconnect(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    outputs = tmp_path / "outputs"
    uploads.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(main, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(main, "OUTPUT_DIR", outputs)

    started = threading.Event()
    finish = threading.Event()

    def failing_rotate(*_args):
        started.set()
        assert finish.wait(2)
        raise RuntimeError("deliberate workflow failure")

    monkeypatch.setattr(main, "rotate_pdf", failing_rotate)
    upload = UploadFile(filename="shared.pdf", file=io.BytesIO(b"%PDF-1.4 test"))
    response = await main.execute_workflow(
        file=upload,
        steps=json.dumps([{"type": "rotate_pdf", "config": {"angle": 90}}]),
    )
    stream = response.body_iterator
    start = _sse_event(await anext(stream))
    await stream.aclose()
    assert await asyncio.to_thread(started.wait, 2)
    finish.set()

    job = await _wait_for_job(start["job_id"])
    assert job["event"]["event"] == "error"
    assert "deliberate workflow failure" in job["event"]["detail"]


def test_workflow_client_polls_the_job_registry_after_stream_loss():
    source = (Path(__file__).parents[1] / "static" / "script.js").read_text(
        encoding="utf-8"
    )
    workflow = source[source.index("async function runWorkflow()") :]
    workflow = workflow[: workflow.index("const MIN_STEP_VISIBLE_MS")]
    assert "jobId = data.job_id" in workflow
    assert "pollJobStatus(jobId, statusText" in workflow
    assert "'workflow'" in workflow


def test_job_registry_prune_removes_only_expired_entries():
    """JobRegistry.get() only expires an entry when something polls that exact
    job_id. A workflow whose SSE consumer never disconnects — the common case,
    the tab stayed open and read 'complete' straight off the stream — never
    calls get() for its own job_id, so before this fix the entry sat in
    app.state.jobs._entries forever: one leaked dict per job, for the life of
    the process, on every box running this app. prune() must reclaim exactly
    the entries old enough that get() would have expired them anyway, and
    leave live ones (pending or just-finished) alone.
    """
    registry = main.JobRegistry()

    old_done = registry.create()
    registry.set_result(old_done, {"event": "complete", "download_token": "t1"})
    old_pending = registry.create()
    fresh_done = registry.create()
    registry.set_result(fresh_done, {"event": "complete", "download_token": "t2"})

    # Age the two "old" entries past FILE_TTL_SECONDS directly, the same way
    # get()'s own expiry check works, without waiting in real time.
    for job_id in (old_done, old_pending):
        registry._entries[job_id]["created"] -= (main.FILE_TTL_SECONDS + 1)

    removed = registry.prune()

    assert removed == 2
    assert set(registry._entries) == {fresh_done}
    # fresh_done must still be fully intact, not merely "not deleted".
    assert registry.get(fresh_done)["event"]["download_token"] == "t2"


def test_cleanup_stale_files_loop_sweeps_the_job_registry():
    """Reconnect/recovery for both execute_workflow and
    api_convert_to_word_stream route through this one registry; verify the
    periodic sweep (the only place anything would ever call prune() in a
    process where no client happens to reconnect) actually drains it, mirroring
    the already-wired rate_limiter.prune() call right above it."""
    import inspect

    body = inspect.getsource(main.cleanup_stale_files_loop)
    assert "app.state.jobs.prune()" in body
    rate_limiter_idx = body.index("app.state.rate_limiter.prune()")
    jobs_idx = body.index("app.state.jobs.prune()")
    assert rate_limiter_idx < jobs_idx, "jobs.prune() should run alongside rate_limiter.prune()"
