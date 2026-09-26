"""Sandboxed per-run workspace: an isolated directory with no network by default."""
from __future__ import annotations

import contextlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class Workspace:
    root: Path

    @classmethod
    @contextlib.contextmanager
    def create(cls, parent: Path) -> Iterator["Workspace"]:
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="compass-", dir=parent) as tmp:
            yield cls(root=Path(tmp))
