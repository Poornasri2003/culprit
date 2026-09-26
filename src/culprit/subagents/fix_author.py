"""
FixAuthor subagent — proposes up to 3 ranked candidate patches (§5).

Candidates are ranked by risk: quick patch < proper fix < refactor.
The orchestrator applies the lowest-risk candidate; the test suite decides
whether it is committed (SPEC §6).
"""

from __future__ import annotations

from culprit.domain.exceptions import SubagentError
from culprit.domain.models import FileEdit, Fix, RootCause
from culprit.subagents.base import Subagent


class FixAuthor(Subagent):
    """
    Subagent that authors ranked fix candidates (§5).

    Implements the Strategy interface defined in Subagent.
    """

    async def run(  # type: ignore[override]
        self,
        *,
        root_cause: RootCause,
        prior_failure: str | None = None,
    ) -> list[Fix]:
        """Return up to 3 Fix candidates ranked by risk (lowest first)."""
        self._check_budget(self._bob_client.get_bobcoins_used())

        codebases = self._workspace.get_codebases()
        codebase_list = "\n".join(
            f"  - name={cb.name!r}, root={cb.root_path}" for cb in codebases
        )

        prior_text = f"\nPrevious attempt failure: {prior_failure}\n" if prior_failure else ""

        prompt = f"""You are authoring fix candidates for a confirmed bug root cause.

Root cause:
  codebase: {root_cause.codebase!r}
  file: {root_cause.file!r}
  line: {root_cause.line}
  symbol: {root_cause.symbol!r}
  explanation: {root_cause.explanation}
  confidence: {root_cause.confidence}
{prior_text}
Available codebases (name -> root path):
{codebase_list}

Instructions:
- READ the source files carefully; do NOT modify any file.
- Propose up to 3 candidate patches, ranked from lowest risk to highest:
    1. Quick patch (minimal targeted change)
    2. Proper fix (correct implementation)
    3. Refactor (structural improvement, only if clearly beneficial)
- For each edit, old_text MUST be copied EXACTLY as it appears in the source file — it must be unique within that file.
- Each file path is relative to the codebase root named in target_codebase.
- Reply with ONLY a JSON array — no prose, no markdown fences — in this exact shape:
  [
    {{
      "target_codebase": "<codebase name>",
      "edits": [
        {{"file": "<relative file path>", "old_text": "<exact text to replace>", "new_text": "<replacement text>"}}
      ]
    }}
  ]"""

        result = await self._bob_client.run_agent(prompt, {}, mode="ask")
        # Bob's reply shape varies between runs: accept a bare list, a single
        # candidate object, or an object wrapping the list (e.g. "candidates").
        if isinstance(result, dict):
            if "edits" in result:
                result = [result]
            else:
                wrapped = [v for v in result.values() if isinstance(v, list)]
                if wrapped:
                    result = wrapped[0]
        if not isinstance(result, list):
            raise SubagentError(f"FixAuthor: expected list, got {type(result).__name__}")
        fixes: list[Fix] = []
        for i, item in enumerate(result[:3]):  # up to 3 candidates
            if not isinstance(item, dict):
                raise SubagentError(f"FixAuthor: candidate {i} is not a dict")
            try:
                edits = [
                    FileEdit(
                        file=str(edit["file"]),
                        old_text=str(edit["old_text"]),
                        new_text=str(edit["new_text"]),
                    )
                    for edit in item.get("edits", [])
                ]
                if not edits:
                    raise SubagentError(f"FixAuthor: candidate {i} has no edits")
                # unified_diff and applied_files are filled by GitOps.apply_fix
                fixes.append(Fix(target_codebase=str(item["target_codebase"]), edits=edits))
            except (KeyError, TypeError) as exc:
                raise SubagentError(f"FixAuthor: invalid candidate {i}: {exc}") from exc
        if not fixes:
            raise SubagentError("FixAuthor: no fix candidates returned")
        return fixes
