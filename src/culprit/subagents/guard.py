"""
Guard subagent — writes a regression test that would have caught the bug (§5).

Guard never modifies the fix code itself. If no tests exist in any
codebase the orchestrator skips Guard and records status = PARTIAL (E1).
"""

from __future__ import annotations

from culprit.domain.exceptions import SubagentError
from culprit.domain.models import Fix, RegressionTest, RootCause
from culprit.subagents.base import Subagent


class Guard(Subagent):
    """
    Subagent that writes a regression test for the confirmed fix (§5).

    Implements the Strategy interface defined in Subagent.
    """

    async def run(  # type: ignore[override]
        self,
        *,
        root_cause: RootCause,
        fix: Fix,
    ) -> RegressionTest:
        """Return a RegressionTest that would have caught the bug; never edits fix code."""
        self._check_budget(self._bob_client.get_bobcoins_used())

        codebases = self._workspace.get_codebases()
        codebase_list = "\n".join(
            f"  - name={cb.name!r}, root={cb.root_path}" for cb in codebases
        )

        prompt = f"""You are writing a permanent regression test for a confirmed and fixed bug.

Root cause:
  codebase: {root_cause.codebase!r}
  file: {root_cause.file!r}
  line: {root_cause.line}
  symbol: {root_cause.symbol!r}
  explanation: {root_cause.explanation}

Fix applied in codebase: {fix.target_codebase!r}
Files changed (relative to that codebase root): {sorted({e.file for e in fix.edits}) or fix.applied_files}

Available codebases (name -> root path):
{codebase_list}

Instructions:
- READ the source code carefully; do NOT modify any production file or the fix itself.
- Write a pytest that would have caught this bug BEFORE the fix was applied.
- Use in-process testing only (e.g. Flask test_client). Never call a live URL.
- The test file must be named test_culprit_regression.py and placed inside the existing tests folder.
- The file path must be relative to the WORKSPACE ROOT (the folder containing all codebases), e.g. "tests/test_culprit_regression.py".
- pytest runs from the workspace root, where an existing conftest.py already puts the workspace root on sys.path. FIRST read an existing test file in that tests folder and import production code EXACTLY the way it does (e.g. "from shared.pricing import ..." or "from backend.app import app"). Do NOT modify sys.path.
- The test function must be a stable, permanent test (not just reproducing the bug, but verifying correct behaviour).
- Reply with ONLY a single JSON object — no prose, no markdown fences — in this exact shape:
  {{
    "codebase": "<codebase name>",
    "file": "<relative/path/to/test_culprit_regression.py>",
    "test_name": "<test function name>",
    "code": "<full test file content>"
  }}"""

        result = await self._bob_client.run_agent(prompt, {}, mode="ask")
        if not isinstance(result, dict):
            raise SubagentError(f"Guard: expected dict, got {type(result).__name__}")
        try:
            return RegressionTest(
                codebase=str(result["codebase"]),
                file=str(result["file"]),
                test_name=str(result["test_name"]),
                code=str(result["code"]),
            )
        except (KeyError, TypeError) as exc:
            raise SubagentError(f"Guard: invalid reply structure: {exc}") from exc
