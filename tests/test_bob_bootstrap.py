"""Tests for the Bob runtime bootstrap — pure logic only, no network calls.

We deliberately do NOT test a real ensure_bob_runtime() download+install in
CI: it hits nodejs.org and, more importantly, `bobshell` is not on the
public npm registry (see module docstring in bob_bootstrap.py), so any
attempt to actually install it will fail unless COMPASS_BOBSHELL_SOURCE
points at a legitimate distribution. These tests cover what we CAN verify
without a real Bob distribution: platform/URL resolution, cache-marker
reuse, and the honest error when no source is configured.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from compass.domain.exceptions import BobShellError
from compass.infrastructure.bob_bootstrap import (
    _node_download_url,
    _platform_tag,
    ensure_bob_runtime,
    is_bootstrapped,
)


def test_platform_tag_matches_this_host() -> None:
    os_tag, arch_tag = _platform_tag()
    assert os_tag in ("win", "darwin", "linux")
    assert arch_tag in ("x64", "arm64")


def test_node_download_url_is_well_formed() -> None:
    url, kind = _node_download_url("22.11.0")
    assert url.startswith("https://nodejs.org/dist/v22.11.0/")
    assert kind in ("zip", "tar")
    assert url.endswith(".zip" if kind == "zip" else ".tar.xz")


def test_is_bootstrapped_false_for_empty_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPASS_BOOTSTRAP_DIR", str(tmp_path / "nonexistent"))
    assert is_bootstrapped() is False


def test_is_bootstrapped_true_when_marker_and_files_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    cache = tmp_path / "cache"
    cache.mkdir()
    fake_node = cache / "node"
    fake_bob_js = cache / "bob.js"
    fake_node.write_text("", encoding="utf-8")
    fake_bob_js.write_text("", encoding="utf-8")
    (cache / "resolved.json").write_text(
        json.dumps({"node": str(fake_node), "bob_js": str(fake_bob_js)}), encoding="utf-8"
    )
    monkeypatch.setenv("COMPASS_BOOTSTRAP_DIR", str(cache))
    assert is_bootstrapped() is True


def test_ensure_bob_runtime_raises_clear_error_without_a_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without COMPASS_BOBSHELL_SOURCE set, we must fail loudly and explain why —
    never silently try (and 404 against) the public npm registry."""
    monkeypatch.setenv("COMPASS_BOOTSTRAP_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("COMPASS_BOBSHELL_SOURCE", raising=False)

    # This will attempt a real Node.js download (network), which is the one
    # legitimate public artifact in this chain. If offline, skip rather than
    # fail the suite on an environment limitation unrelated to the logic
    # under test.
    try:
        with pytest.raises(BobShellError, match="not on the public npm registry"):
            ensure_bob_runtime()
    except BobShellError as e:
        if "urlopen" in str(e) or "Network" in str(e) or "timed out" in str(e).lower():
            pytest.skip(f"no network available to download Node.js: {e}")
        raise
    except OSError as e:
        pytest.skip(f"no network available to download Node.js: {e}")
