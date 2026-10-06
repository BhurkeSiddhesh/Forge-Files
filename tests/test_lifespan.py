"""The public app's lifespan handler (replaces the deprecated @app.on_event hooks).

Pins two things the old `@app.on_event("startup")` version got wrong:
  * the sweeper task is referenced for as long as it runs (a bare
    `asyncio.create_task(...)` result is garbage-collectable mid-flight), and
  * it has a teardown at all — before, the loop was only ever killed with the
    process.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

import main


def test_no_deprecated_on_event_hooks_remain():
    """FastAPI has deprecated on_event since 0.93 and will eventually drop it."""
    assert main.app.router.on_startup == []
    assert main.app.router.on_shutdown == []
    assert main.app.router.lifespan_context is not None


def test_lifespan_starts_sweeper_and_cancels_it_on_shutdown(monkeypatch):
    seen = {}

    async def fake_loop():
        seen["task"] = asyncio.current_task()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise

    monkeypatch.setattr(main, "cleanup_stale_files_loop", fake_loop)

    async def run():
        async with main.lifespan(main.app):
            await asyncio.sleep(0)  # let the sweeper task reach its first await
            assert "task" in seen, "sweeper was never started"
            assert not seen["task"].done()
        assert seen.get("cancelled"), "sweeper kept running past shutdown"
        assert seen["task"].cancelled()

    asyncio.run(run())


def test_lifespan_holds_a_reference_to_the_sweeper_task(monkeypatch):
    """The task must stay strongly referenced while the app is up."""
    import gc

    async def fake_loop():
        await asyncio.sleep(3600)

    monkeypatch.setattr(main, "cleanup_stale_files_loop", fake_loop)

    async def run():
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            gc.collect()
            running = [
                t for t in asyncio.all_tasks() if t.get_coro().__qualname__.endswith("fake_loop")
            ]
            assert running, "sweeper task was collected while the app was running"

    asyncio.run(run())


def test_lifespan_sweeps_upload_and_output_dirs(monkeypatch):
    """The real sweeper runs at least one pass over both directories at boot."""
    swept = []
    monkeypatch.setattr(
        main, "_delete_stale_files", lambda directory, ttl: swept.append((directory, ttl))
    )

    async def run():
        async with main.lifespan(main.app):
            for _ in range(200):
                if len(swept) >= 2:
                    break
                await asyncio.sleep(0.01)

    asyncio.run(run())
    assert [d for d, _ in swept] == [main.UPLOAD_DIR, main.OUTPUT_DIR]
    # Uploads keep the long safety-net TTL; finished results use the short one.
    assert [ttl for _, ttl in swept] == [main.FILE_TTL_SECONDS, main.RESULT_TTL_SECONDS]


def test_warmup_is_skipped_unless_opted_in(monkeypatch):
    """WARMUP_AI unset (the default) must not import/instantiate an OCR backend."""
    monkeypatch.delenv("WARMUP_AI", raising=False)
    called = False

    def boom():  # pragma: no cover - must never run
        nonlocal called
        called = True

    monkeypatch.setattr("scripts.ocr_engine.get_ocr_engine", boom)
    asyncio.run(main._warmup_ai())
    assert called is False


def test_warmup_failure_does_not_block_boot(monkeypatch):
    """A broken/absent OCR backend must degrade to a warning, not a crashed app."""
    monkeypatch.setenv("WARMUP_AI", "1")
    monkeypatch.setattr(main, "DISABLE_AI", False)

    def boom():
        raise RuntimeError("no onnxruntime here")

    monkeypatch.setattr("scripts.ocr_engine.get_ocr_engine", boom)
    asyncio.run(main._warmup_ai())  # must not raise


def test_app_serves_requests_through_a_full_lifespan_cycle(monkeypatch):
    """End-to-end: entering/exiting the TestClient context runs startup+shutdown."""
    monkeypatch.setattr(main, "_delete_stale_files", lambda directory, ttl: None)
    with TestClient(main.app) as client:
        assert client.get("/health").status_code in (200, 404)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
