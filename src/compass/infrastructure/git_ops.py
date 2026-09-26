"""Cloning a git URL, copying a local path, and extracting a ZIP.

Each path returns a `checkout` directory inside the sandbox workspace, which is
what the subagents read from. We use the `git` CLI (not GitPython) so the only
new dependency is one the user's machine already has.
"""
from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

from compass.domain.exceptions import CompassError

CHECKOUT_DIRNAME = "checkout"


class SourceError(CompassError):
    """The requested source could not be resolved into a workspace checkout."""


def clone_git(url: str, dest: Path, *, depth: int = 1) -> Path:
    """Clone a git URL into `dest/checkout` and return that path."""
    checkout = dest / CHECKOUT_DIRNAME
    result = subprocess.run(
        ["git", "clone", "--depth", str(depth), url, str(checkout)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SourceError(f"git clone failed ({result.returncode}): {result.stderr.strip()}")
    return checkout


def copy_local(path: Path, dest: Path) -> Path:
    """Copy a local repo (or plain directory) into `dest/checkout`."""
    if not path.exists() or not path.is_dir():
        raise SourceError(f"local path does not exist or is not a directory: {path}")
    checkout = dest / CHECKOUT_DIRNAME
    # Skip the .git dir so downstream tools don't see it; we still have git for git_log elsewhere.
    shutil.copytree(path, checkout, ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv"))
    return checkout


def extract_zip(zip_path: Path, dest: Path) -> Path:
    """Extract a .zip archive into `dest/checkout`, flattening a single top-level folder."""
    if not zip_path.exists() or not zip_path.is_file():
        raise SourceError(f"zip file does not exist: {zip_path}")
    checkout = dest / CHECKOUT_DIRNAME
    checkout.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            # Reject zip-slip: refuse any entry whose resolved path escapes checkout.
            for member in zf.namelist():
                target = (checkout / member).resolve()
                if checkout.resolve() not in target.parents and target != checkout.resolve():
                    raise SourceError(f"zip contains unsafe path: {member}")
            zf.extractall(checkout)
    except zipfile.BadZipFile as e:
        raise SourceError(f"not a valid zip file: {zip_path}") from e

    # Common case: a zip contains one top-level directory (e.g. `myrepo-main/`).
    # Flatten it so callers can treat the checkout root as the repo root.
    entries = [p for p in checkout.iterdir() if p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for child in list(inner.iterdir()):
            shutil.move(str(child), str(checkout / child.name))
        inner.rmdir()
    return checkout


def git_log_touching(path: Path, *, limit: int = 20) -> list[dict[str, str]]:
    """Recent commits that touched `path`. Empty list when there's no .git — that's fine."""
    if not (path / ".git").exists():
        return []
    result = subprocess.run(
        ["git", "-C", str(path), "log", f"-n{limit}", "--pretty=format:%h%x1f%an%x1f%s"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    rows: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        sha, author, subject = (line.split("\x1f", 2) + ["", "", ""])[:3]
        rows.append({"sha": sha, "author": author, "subject": subject})
    return rows
