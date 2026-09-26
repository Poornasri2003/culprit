"""Filesystem traversal + ripgrep-backed search, respecting .gitignore."""
from __future__ import annotations

from pathlib import Path


def list_dir(root: Path, path: str = "") -> list[str]:
    """List entries under `root/path`, respecting .gitignore."""
    raise NotImplementedError("file_index.list_dir")


def read_file(root: Path, path: str, *, start: int | None = None, end: int | None = None) -> str:
    """Read `root/path` between `start`..`end` lines (1-indexed, inclusive)."""
    raise NotImplementedError("file_index.read_file")


def search(root: Path, pattern: str, *, glob: str | None = None) -> list[tuple[str, int, str]]:
    """Ripgrep search: returns (path, line_no, line) tuples."""
    raise NotImplementedError("file_index.search")


def find_references(root: Path, symbol: str) -> list[tuple[str, int, str]]:
    """Best-effort cross-file references to `symbol` (fallback: ripgrep on word boundary)."""
    raise NotImplementedError("file_index.find_references")
