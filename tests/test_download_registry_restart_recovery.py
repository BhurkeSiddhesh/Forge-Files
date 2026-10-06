"""Regression tests for issue #94: DownloadRegistry kept token -> path mappings
purely in-process, so a server restart (this repo auto-deploys frequently)
wiped every pending/just-finished token even though the physical output file
was still sitting on disk under OUTPUT_DIR -- and the client got told "The
converted file no longer exists" for a file that, in fact, still existed.

new_result_dir() names each result directory after the token itself, so a
fresh registry (empty _entries, exactly what a restart produces) can still
resolve a token by looking under OUTPUT_DIR/<token>. These tests exercise
that recovery path directly against DownloadRegistry, without going through
main.app's real OUTPUT_DIR.
"""
import time

import pytest

import main


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    return main.DownloadRegistry()


def _make_result(output_dir, token, filename="report_forgefiles.org.pdf", age_seconds=0):
    result_dir = output_dir / token
    result_dir.mkdir()
    path = result_dir / filename
    path.write_bytes(b"pdf bytes")
    if age_seconds:
        mtime = time.time() - age_seconds
        import os
        os.utime(path, (mtime, mtime))
    return path


def test_resolve_recovers_a_token_never_registered_in_this_process(tmp_path, registry):
    """Simulates a restart: the file survived on disk, the in-memory map didn't."""
    token = "recovered-token-abc123456789"
    expected = _make_result(tmp_path, token)

    assert registry.resolve(token, session_id=None) == expected


def test_resolve_returns_none_for_a_token_with_no_directory(registry):
    assert registry.resolve("never-existed-1234567890", session_id=None) is None


def test_resolve_does_not_recover_an_empty_result_directory(tmp_path, registry):
    """A workflow that errored before producing anything leaves an empty dir
    (main.py's finally block rmdir()s it, but guard the recovery path too)."""
    token = "empty-dir-token-1234567890"
    (tmp_path / token).mkdir()

    assert registry.resolve(token, session_id=None) is None


def test_resolve_does_not_recover_a_directory_with_multiple_files(tmp_path, registry):
    """A completed result directory always ends up with exactly one file;
    anything else isn't a shape recovery should guess at."""
    token = "multi-file-token-1234567890"
    result_dir = tmp_path / token
    result_dir.mkdir()
    (result_dir / "a.pdf").write_bytes(b"a")
    (result_dir / "b.pdf").write_bytes(b"b")

    assert registry.resolve(token, session_id=None) is None


def test_resolve_honors_ttl_on_a_recovered_entry(tmp_path, registry, monkeypatch):
    """A file old enough to be past RESULT_TTL_SECONDS must not be recovered as
    live just because it's still physically present (the sweeper hasn't
    gotten to it yet, but resolve() must agree with what the sweeper will do)."""
    monkeypatch.setattr(main, "RESULT_TTL_SECONDS", 3600)
    token = "stale-token-1234567890123"
    _make_result(tmp_path, token, age_seconds=7200)

    assert registry.resolve(token, session_id=None) is None


def test_resolve_recovers_a_fresh_file_within_ttl(tmp_path, registry, monkeypatch):
    monkeypatch.setattr(main, "RESULT_TTL_SECONDS", 3600)
    token = "fresh-token-12345678901234"
    expected = _make_result(tmp_path, token, age_seconds=60)

    assert registry.resolve(token, session_id=None) == expected


def test_recovered_entry_is_cached_so_disk_is_not_rescanned_every_call(tmp_path, registry):
    token = "cache-token-123456789012345"
    path = _make_result(tmp_path, token)

    first = registry.resolve(token, session_id="alice")
    assert first == path

    # Delete the directory; if resolve() re-hit the disk it would now return
    # None. It shouldn't -- the recovered entry is cached in _entries, same
    # as one added via add().
    import shutil
    shutil.rmtree(tmp_path / token)

    second = registry.resolve(token, session_id="alice")
    assert second == path


def test_recovered_entry_has_no_owner_so_session_binding_does_not_block_it(tmp_path, registry, monkeypatch):
    """Session ownership can't be recovered from disk. DOWNLOAD_BIND_SESSION
    must not turn every post-restart download into a false-negative 404."""
    monkeypatch.setattr(main, "DOWNLOAD_BIND_SESSION", True)
    token = "no-owner-token-1234567890"
    expected = _make_result(tmp_path, token)

    assert registry.resolve(token, session_id="someones-session") == expected


def test_recover_from_disk_rejects_path_traversal_tokens(tmp_path, registry):
    """resolve() is the only thing standing between an attacker-controlled
    token and OUTPUT_DIR/<token> once the in-memory map misses; it must not
    escape OUTPUT_DIR even if the caller's regex guard (main.py's
    _DOWNLOAD_TOKEN_RE) were ever bypassed or skipped."""
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("do not serve me")

    assert registry.resolve("../outside_secret.txt", session_id=None) is None
