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
            original = target.read_text(encoding="utf-8")
            occurrences = original.count(edit.old_text)
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
            original = target.read_text(encoding="utf-8")
            new_content = original.replace(edit.old_text, edit.new_text, 1)

            target.write_text(new_content, encoding="utf-8")
            applied.append(edit.file)

            diff = difflib.unified_diff(
                original.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
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
    ) -> str:
        """Stage fix + regression test files and create a commit; return the commit SHA."""
        repo = gitpython.Repo(str(codebase.root_path), search_parent_directories=True)

        files_to_stage: list[str] = list(fix.applied_files)
        if regression_test is not None:
            files_to_stage.append(regression_test.file)

        for rel_path in files_to_stage:
            abs_path = str(codebase.root_path / rel_path)
            repo.index.add([abs_path])

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
