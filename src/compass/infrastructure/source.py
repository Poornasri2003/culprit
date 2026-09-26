"""Turn `--repo` (URL, local path, or .zip) into a workspace checkout directory."""
from __future__ import annotations

from pathlib import Path

from compass.infrastructure import git_ops


def resolve_source(spec: str, workspace: Path) -> Path:
    """Return a `checkout/` directory inside `workspace` containing the repo files.

    Accepted forms:
      * git URL     — anything that starts with a scheme (`https://`, `git@`, `ssh://`, `git://`)
      * .zip file   — a local .zip archive path
      * local dir   — an existing directory on disk
    """
    if _looks_like_url(spec):
        return git_ops.clone_git(spec, workspace)

    p = Path(spec)
    if p.suffix.lower() == ".zip":
        return git_ops.extract_zip(p, workspace)
    return git_ops.copy_local(p, workspace)


def _looks_like_url(spec: str) -> bool:
    lower = spec.lower()
    if lower.startswith(("http://", "https://", "git://", "ssh://", "file://")):
        return True
    # git@host:path.git style
    if "@" in spec and ":" in spec and not spec[1:3] == ":\\":  # avoid Windows drive letters
        return True
    return False
