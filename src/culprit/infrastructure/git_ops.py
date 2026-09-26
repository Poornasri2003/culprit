"""
GitOps — deterministic git operations (commit, revert, diff) per §6, §9.

Uses GitPython. All operations are per-codebase. Never commits credentials
or tokens. Never invokes AI.
"""

from __future__ import annotations

import difflib
import hashlib
import shutil
import tempfile
from pathlib import Path

import git as gitpython

from culprit.domain.exceptions import SubagentError
from culprit.domain.models import Fix, RegressionTest, SourceCodebase


def _backup_dir(root: Path) -> Path:
    """Return the pre-edit backup directory for a codebase.

    Kept OUTSIDE the codebase (in the system temp dir) so pytest never
    collects backed-up test files and git never sees the backups.
    """
    key = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / "culprit_backup" / key



def _read_exact(path: Path) -> str:
    """Read a file without newline translation, so CRLF files stay CRLF."""
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_exact(path: Path, content: str) -> None:
    """Write a file without newline translation (inverse of _read_exact)."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)


def _match_newlines(snippet: str, file_text: str) -> str:
    """Give an edit snippet the same line endings as the file it targets.

    Bob answers with LF line endings; a CRLF file would otherwise never
    match, or be rewritten with mixed endings (a whole-file diff).
    """
    snippet = snippet.replace("\r\n", "\n")
    return snippet.replace("\n", "\r\n") if "\r\n" in file_text else snippet

class GitOps:
    """
    Adapter for git operations during the self-healing loop (§6, §9).

    Pattern: Adapter — wraps GitPython behind a domain-typed interface.
    """

    # ------------------------------------------------------------------
    # apply / revert
    # ------------------------------------------------------------------

    def apply_fix(self, codebase: SourceCodebase, fix: Fix) -> None:
        """
        Back up each target file, then apply fix.edits.

        Each edit's old_text must occur *exactly once* in the file;
        if it does not, SubagentError is raised and NO file is modified.
        Fills fix.unified_diff and fix.applied_files using difflib.
        """
        root = codebase.root_path
        backup_dir = _backup_dir(root)

        # ── Validation pass: check every edit before touching any file ──
        for edit in fix.edits:
            target = root / edit.file
            original = _read_exact(target)
            occurrences = original.count(_match_newlines(edit.old_text, original))
            if occurrences != 1:
                raise SubagentError(
                    f"old_text for '{edit.file}' occurs {occurrences} time(s); "
                    f"expected exactly 1. No files were modified."
                )

        # ── Backup pass ──────────────────────────────────────────────────
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        backup_dir.mkdir(parents=True)
        for edit in fix.edits:
            target = root / edit.file
            backup_path = backup_dir / edit.file
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_path)

        # ── Apply pass + diff accumulation ───────────────────────────────
        diff_lines: list[str] = []
        applied: list[str] = []

        for edit in fix.edits:
            target = root / edit.file
            original = _read_exact(target)
            new_content = original.replace(
                _match_newlines(edit.old_text, original),
                _match_newlines(edit.new_text, original),
                1,
            )

            _write_exact(target, new_content)
            applied.append(edit.file)

            diff = difflib.unified_diff(
                original.replace("\r\n", "\n").splitlines(keepends=True),
                new_content.replace("\r\n", "\n").splitlines(keepends=True),
                fromfile=f"a/{edit.file}",
                tofile=f"b/{edit.file}",
            )
            diff_lines.extend(diff)

        # Mutate the fix object in-place (both fields have defaults so Pydantic
        # allows attribute assignment without model_config frozen=True).
        fix.unified_diff = "".join(diff_lines)
        fix.applied_files = applied

    def revert_fix(self, codebase: SourceCodebase, fix: Fix) -> None:
        """Restore the backed-up originals of every file touched by apply_fix."""
        root = codebase.root_path
        backup_dir = _backup_dir(root)

        for file_rel in fix.applied_files:
            backup_path = backup_dir / file_rel
            if backup_path.exists():
                target = root / file_rel
                shutil.copy2(backup_path, target)

        # Clean up the backup directory
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

    # ------------------------------------------------------------------
    # commit
    # ------------------------------------------------------------------

    def commit(
        self,
        codebase: SourceCodebase,
        fix: Fix,
        regression_test: RegressionTest | None,
        project_root: Path | None = None,
    ) -> str:
        """Stage fix + regression test files and create a commit; return the commit SHA."""
        repo = gitpython.Repo(str(codebase.root_path), search_parent_directories=True)

        # Stage the fix files (relative to codebase root)
        for rel_path in fix.applied_files:
            abs_path = str(codebase.root_path / rel_path)
            repo.index.add([abs_path])

        # Stage the regression test — its path is relative to project_root when supplied,
        # otherwise fall back to codebase root (original behaviour).
        if regression_test is not None:
            reg_root = project_root if project_root is not None else codebase.root_path
            abs_reg = str(reg_root / regression_test.file)
            repo.index.add([abs_reg])

        commit_message = (
            f"culprit: fix {fix.target_codebase}\n\n"
            + (fix.unified_diff[:2000] if fix.unified_diff else "")
        )
        commit_obj = repo.index.commit(commit_message)

        # The fix is now permanent; its backups are no longer needed.
        backup_dir = _backup_dir(codebase.root_path)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        return commit_obj.hexsha

    # ------------------------------------------------------------------
    # diff helper
    # ------------------------------------------------------------------

    def current_diff(self, codebase: SourceCodebase) -> str:
        """Return the current working-tree unified diff for the codebase."""
        repo = gitpython.Repo(str(codebase.root_path), search_parent_directories=True)
        return repo.git.diff()
