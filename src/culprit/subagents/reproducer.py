"""
Reproducer subagent — produces a FailingTest (§5).

If a live URL was provided, the orchestrator has already probed it and
passes the captured RuntimeEvidence in as context. The Reproducer ALWAYS
writes a pytest that exercises the code path IN-PROCESS (e.g. Flask
test_client), never the live URL: a running server keeps old code loaded,
so a live-URL test could never see the fix (SPEC §5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from culprit.domain.exceptions import SubagentError
from culprit.domain.models import Bug, RuntimeEvidence
from culprit.subagents.base import Subagent


@dataclass
class FailingTest:
    """A pytest file that demonstrates the bug (output of Reproducer)."""

    file: str
    code: str


class Reproducer(Subagent):
    """
    Subagent that creates a reproducible failing test (§5).

    Implements the Strategy interface defined in Subagent.
    """

    async def run(  # type: ignore[override]
        self,
        *,
        bug: Bug,
        runtime_evidence: RuntimeEvidence | None,
        prior_failure: str | None = None,
    ) -> FailingTest:
        """Produce an in-process FailingTest, using runtime_evidence as context if present."""
        self._check_budget(self._bob_client.get_bobcoins_used())

        # Build codebase list for the prompt
        codebases = self._workspace.get_codebases()
        codebase_list = "\n".join(
            f"  - name={cb.name!r}, root={cb.root_path}" for cb in codebases
        )

        # Build evidence section
        evidence_lines: list[str] = []
        if runtime_evidence is not None:
            ev = runtime_evidence
            evidence_lines.append(f"HTTP {ev.endpoint.method} {ev.endpoint.url}")
            evidence_lines.append(f"Response status: {ev.response_status}")
            evidence_lines.append(f"Response body: {ev.response_body}")
            if ev.stack_trace_hint:
                evidence_lines.append(f"Stack trace hint:\n{ev.stack_trace_hint}")
        evidence_text = "\n".join(evidence_lines) if evidence_lines else "(no live URL evidence)"

        prior_text = f"\nPrevious attempt failure: {prior_failure}\n" if prior_failure else ""

        prompt = f"""You are helping debug a software bug. Your job is to write a failing pytest.

Bug description: {bug.description}

Runtime evidence:
{evidence_text}
{prior_text}
Available codebases (name -> root path relative to workspace):
{codebase_list}

Instructions:
- READ the source code carefully; do NOT modify any production file.
- Write a pytest that FAILS on the current code and PASSES once the bug is fixed.
- Use in-process testing only (e.g. Flask test_client). Never call a live URL.
- The test file must be named test_culprit_repro.py and placed inside the existing tests folder.
- The file path must be relative to the WORKSPACE ROOT (the folder containing all codebases), e.g. "tests/test_culprit_repro.py".
- pytest runs from the workspace root, where an existing conftest.py already puts the workspace root on sys.path. FIRST read an existing test file in that tests folder and import production code EXACTLY the way it does. Do NOT modify sys.path.
- The test must fail on an ASSERTION about the wrong value, never on an import or setup error.
- Reply with ONLY a single JSON object — no prose, no markdown fences — in this exact shape:
  {{"file": "<relative/path/to/test_culprit_repro.py>", "code": "<full test file content>"}}"""

        result = await self._bob_client.run_agent(prompt, {}, mode="ask")
        if not isinstance(result, dict):
            raise SubagentError(f"Reproducer: expected dict, got {type(result).__name__}")
        try:
            return FailingTest(
                file=str(result["file"]),
                code=str(result["code"]),
            )
        except KeyError as exc:
            raise SubagentError(f"Reproducer: missing key {exc} in reply") from exc
