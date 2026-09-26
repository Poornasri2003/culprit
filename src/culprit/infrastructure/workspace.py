"""
Workspace — loads and exposes all source codebases for a run (§6, §9).

Handles local --folder paths and --git remote repo clones. Enforces
MAX_CODEBASES (§3). Never touches credentials.
"""

from __future__ import annotations

from pathlib import Path

from culprit.config import MAX_CODEBASES
from culprit.domain.exceptions import ConfigurationError
from culprit.domain.models import SourceCodebase

# Map file extension -> language name (lower-case, human-readable)
_EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".r": "r",
    ".sh": "shell",
    ".bash": "shell",
}


def _detect_language(root: Path) -> str:
    """Return the dominant language in *root* based on file-extension counts."""
    counts: dict[str, int] = {}
    for f in root.rglob("*"):
        if f.is_file():
            ext = f.suffix.lower()
            if ext in _EXT_TO_LANGUAGE:
                lang = _EXT_TO_LANGUAGE[ext]
                counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return "unknown"
    return max(counts, key=lambda k: counts[k])


class Workspace:
    """
    Aggregates all SourceCodebases available to a run (§6).

    Pattern: Adapter — hides filesystem and git I/O behind a stable interface.
    """

    def __init__(self, work_dir: Path) -> None:
        """Initialise with a temporary working directory; no I/O yet."""
        self._work_dir = work_dir
        self._codebases: list[SourceCodebase] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_folder(self, path: Path, name: str) -> SourceCodebase:
        """Register a local folder as a SourceCodebase; raises if MAX_CODEBASES exceeded."""
        if len(self._codebases) >= MAX_CODEBASES:
            raise ConfigurationError(
                f"MAX_CODEBASES ({MAX_CODEBASES}) exceeded; cannot add '{name}'"
            )
        language = _detect_language(path)
        cb = SourceCodebase(
            name=name,
            root_path=path.resolve(),
            language=language,
            entry_points=[],
        )
        self._codebases.append(cb)
        return cb

    def load_git(self, url: str, name: str) -> SourceCodebase:  # noqa: ARG002
        """Clone a remote repo and register it; raises if MAX_CODEBASES exceeded."""
        raise NotImplementedError("stretch goal", "SPEC §15")

    def get_codebases(self) -> list[SourceCodebase]:
        """Return all registered SourceCodebase objects."""
        return list(self._codebases)

    def resolve_file(self, codebase_name: str, relative_path: str) -> Path:
        """Return the absolute Path for a file inside a named codebase."""
        for cb in self._codebases:
            if cb.name == codebase_name:
                return cb.root_path / relative_path
        raise KeyError(f"No codebase named '{codebase_name}'")

    def cleanup(self) -> None:
        """Remove all cloned repos and temp artefacts created during this run."""
        # No cloned repos in the MVP; nothing to remove.
