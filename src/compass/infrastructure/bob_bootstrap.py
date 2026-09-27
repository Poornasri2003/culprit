"""Self-contained Bob Shell runtime bootstrap — no sudo, no apt, no Docker layer.

Cloud platforms like Streamlit Community Cloud only let you install Python
packages (pyproject.toml) and apt packages (packages.txt) — there is no hook
to run an arbitrary `npm install -g`. This module works around that by
downloading the official Node.js binary distribution straight from
nodejs.org into a local cache directory, then using that Node's bundled npm
to install `bobshell` into the same cache directory with `--prefix`. Nothing
here touches system directories, so it works identically whether the host
is Windows, a Streamlit Cloud container, a Hugging Face Space, or a VPS.

Resolution order (see `resolve_bob_command`):
  1. `COMPASS_BOB_ENTRY` env var — explicit override, unchanged from before.
  2. A `bob` binary already on PATH — e.g. installed globally by the user.
  3. The Windows npm-global layout Bob's installer writes by default.
  4. This bootstrap — download once, cache forever (keyed by Node version).

The bootstrap is idempotent and safe to call on every subagent invocation;
after the first run it just returns cached paths in milliseconds.
"""
from __future__ import annotations

import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from compass.domain.exceptions import BobShellError

NODE_VERSION = "22.11.0"  # pinned — bump deliberately, not "latest", for reproducibility

# `bobshell` is NOT published on the public npm registry (confirmed: a plain
# `npm install -g bobshell` 404s against registry.npmjs.org). It ships as a
# bundled companion to IBM's official "IBM Bob" desktop installer. There is
# currently no known public URL this bootstrap can fetch it from — so unlike
# Node.js (downloaded straight from nodejs.org below, which is fine), we
# require the caller to supply a legitimate source explicitly rather than
# silently failing against a registry that will never have it.
#
# COMPASS_BOBSHELL_SOURCE accepts anything `npm install -g <spec>` accepts:
# a tarball URL (https://.../bobshell-2.0.5.tgz), a local path to a .tgz,
# a private registry spec, or a git URL — whatever IBM's official
# distribution channel for headless/server use turns out to be.
BOBSHELL_SOURCE_ENV = "COMPASS_BOBSHELL_SOURCE"


def _cache_root() -> Path:
    """Where the bootstrapped runtime lives. Overridable for tests / custom hosts."""
    override = os.environ.get("COMPASS_BOOTSTRAP_DIR")
    if override:
        return Path(override)
    # /tmp (or the OS temp dir) is writable on every platform we target,
    # including read-only-root containers where $HOME may not be.
    return Path(tempfile.gettempdir()) / "compass-bob-runtime"


def _platform_tag() -> tuple[str, str]:
    """Return (os_tag, arch_tag) matching nodejs.org's release filenames."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        os_tag = "win"
    elif system == "darwin":
        os_tag = "darwin"
    elif system == "linux":
        os_tag = "linux"
    else:
        raise BobShellError(f"unsupported platform for Bob bootstrap: {system}")

    if machine in ("x86_64", "amd64"):
        arch_tag = "x64"
    elif machine in ("arm64", "aarch64"):
        arch_tag = "arm64"
    else:
        raise BobShellError(f"unsupported CPU architecture for Bob bootstrap: {machine}")

    return os_tag, arch_tag


def _node_download_url(version: str) -> tuple[str, str]:
    """Return (url, archive_kind) for the Node.js distribution matching this host."""
    os_tag, arch_tag = _platform_tag()
    if os_tag == "win":
        name = f"node-v{version}-{os_tag}-{arch_tag}"
        return f"https://nodejs.org/dist/v{version}/{name}.zip", "zip"
    name = f"node-v{version}-{os_tag}-{arch_tag}"
    return f"https://nodejs.org/dist/v{version}/{name}.tar.xz", "tar"


def _extract_node(archive_bytes: bytes, kind: str, dest: Path) -> Path:
    """Extract the downloaded archive into `dest`, returning the extracted root dir."""
    dest.mkdir(parents=True, exist_ok=True)
    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            zf.extractall(dest)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:xz") as tf:
            tf.extractall(dest)
    # Both archive types contain exactly one top-level directory.
    entries = [p for p in dest.iterdir() if p.is_dir()]
    if len(entries) != 1:
        raise BobShellError(f"unexpected Node archive layout under {dest}: {entries}")
    return entries[0]


def _node_binary_path(node_root: Path) -> Path:
    os_tag, _ = _platform_tag()
    if os_tag == "win":
        return node_root / "node.exe"
    return node_root / "bin" / "node"


def _npm_cli_js(node_root: Path) -> Path:
    os_tag, _ = _platform_tag()
    if os_tag == "win":
        return node_root / "node_modules" / "npm" / "bin" / "npm-cli.js"
    return node_root / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"


def _download(url: str, *, timeout: int = 120) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "compass-bob-bootstrap"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted, pinned host)
        return resp.read()


def _find_bob_js(npm_global_dir: Path) -> Path | None:
    """`npm install -g --prefix <dir>` lands the package under a platform-specific
    subpath (`lib/node_modules/...` on POSIX, `node_modules/...` on Windows).
    Glob instead of hardcoding so both layouts resolve the same way."""
    matches = list(npm_global_dir.glob("**/bobshell/dist/bob.js"))
    return matches[0] if matches else None


def ensure_bob_runtime() -> tuple[str, str]:
    """Guarantee a working (node_binary, bob_js) pair, downloading/installing on
    first call and reusing the cache on every call after (including across
    process restarts, as long as the cache directory survives).

    Returns (node_binary_path, bob_js_path) as strings, ready to prepend to a
    subprocess argv: `[node_binary, bob_js, "run", ...]`.
    """
    cache = _cache_root()
    marker = cache / "resolved.json"

    if marker.exists():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            node_bin, bob_js = data["node"], data["bob_js"]
            if Path(node_bin).exists() and Path(bob_js).exists():
                return node_bin, bob_js
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # fall through and rebuild

    node_root_marker = cache / f"node-v{NODE_VERSION}"
    node_bin_path = _node_binary_path(node_root_marker)

    if not node_bin_path.exists():
        url, kind = _node_download_url(NODE_VERSION)
        archive_bytes = _download(url)
        extracted = _extract_node(archive_bytes, kind, cache / "_extract_tmp")
        if node_root_marker.exists():
            shutil.rmtree(node_root_marker)
        extracted.rename(node_root_marker)
        shutil.rmtree(cache / "_extract_tmp", ignore_errors=True)
        node_bin_path = _node_binary_path(node_root_marker)
        if not node_bin_path.exists():
            raise BobShellError(f"Node extraction did not produce a binary at {node_bin_path}")
        if _platform_tag()[0] != "win":
            node_bin_path.chmod(node_bin_path.stat().st_mode | stat.S_IEXEC)

    npm_global_dir = cache / "npm-global"
    bob_js_path = _find_bob_js(npm_global_dir)

    if bob_js_path is None:
        pkg_spec = os.environ.get(BOBSHELL_SOURCE_ENV, "").strip()
        if not pkg_spec:
            raise BobShellError(
                "Cannot install Bob Shell automatically: `bobshell` is not on the "
                "public npm registry (it ships bundled with IBM's official Bob "
                "installer, not via `npm install -g bobshell`). Set the "
                f"{BOBSHELL_SOURCE_ENV} environment variable to a legitimate source "
                "npm can install from (a tarball URL, a private registry spec, or "
                "a git URL) — check bob.ibm.com or your hackathon docs for the "
                "official headless/server distribution. Alternatively, run Compass "
                "on a host where Bob was already installed via the official "
                "installer, so a `bob` binary is already on PATH."
            )
        npm_cli = _npm_cli_js(node_root_marker)
        if not npm_cli.exists():
            raise BobShellError(f"npm CLI not found at {npm_cli} after Node extraction")
        npm_global_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = cache / "npm-cache"
        result = subprocess.run(
            [str(node_bin_path), str(npm_cli), "install", "-g", pkg_spec,
             "--prefix", str(npm_global_dir), "--cache", str(cache_dir),
             "--no-audit", "--no-fund", "--loglevel", "error"],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            raise BobShellError(
                f"npm install -g {pkg_spec!r} failed ({result.returncode}): "
                f"{result.stderr.strip()[:500]}"
            )
        bob_js_path = _find_bob_js(npm_global_dir)
        if bob_js_path is None:
            raise BobShellError(f"install succeeded but bob.js not found under {npm_global_dir}")

    resolved = {"node": str(node_bin_path), "bob_js": str(bob_js_path)}
    marker.write_text(json.dumps(resolved), encoding="utf-8")
    return resolved["node"], resolved["bob_js"]


def is_bootstrapped() -> bool:
    """Cheap check for the UI: has the runtime already been prepared (no download needed)?"""
    marker = _cache_root() / "resolved.json"
    if not marker.exists():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return Path(data["node"]).exists() and Path(data["bob_js"]).exists()
    except (json.JSONDecodeError, KeyError, OSError):
        return False
