"""
CauseTracer subagent — identifies the root cause across codebases (§5).

Walks source code in ALL provided SourceCodebases, follows
imports/API contracts between them, correlates runtime response with
source lines, and assigns a confidence score 0.0–1.0.
"""

from __future__ import annotations

from culprit.domain.exceptions import SubagentError
from culprit.domain.models import Bug, RuntimeEvidence, RootCause
from culprit.subagents.base import Subagent


class CauseTracer(Subagent):
    """
    Subagent that traces the root cause across N codebases (§5).

    Implements the Strategy interface defined in Subagent.
    """

    async def run(  # type: ignore[override]
        self,
        *,
        bug: Bug,
        runtime_evidence: RuntimeEvidence | None,
        prior_failure: str | None = None,
    ) -> RootCause:
        """Return a RootCause with confidence 0.0–1.0; raises SubagentError on bad output."""
        self._check_budget(self._bob_client.get_bobcoins_used())

        codebases = self._workspace.get_codebases()
        codebase_list = "\n".join(
            f"  - name={cb.name!r}, root={cb.root_path}, language={cb.language}"
            for cb in codebases
        )

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

        prompt = f"""You are helping trace the root cause of a bug across multiple codebases.

Bug description: {bug.description}

Runtime evidence:
{evidence_text}
{prior_text}
Available codebases (name -> root path -> language):
{codebase_list}

Instructions:
- READ the source code carefully; do NOT modify any production file.
- Walk across ALL codebases, following imports and API contracts between them.
- Correlate the runtime response with specific source lines.
- Assign a confidence score between 0.0 and 1.0.
- Reply with ONLY a single JSON object — no prose, no markdown fences — in this exact shape:
  {{
    "codebase": "<codebase name>",
    "file": "<path relative to codebase root>",
    "line": <integer line number>,
    "symbol": "<function or class name>",
    "explanation": "<clear explanation of the root cause>",
    "confidence": <float 0.0-1.0>
  }}"""

        result = await self._bob_client.run_agent(prompt, {}, mode="ask")
        if not isinstance(result, dict):
            raise SubagentError(f"CauseTracer: expected dict, got {type(result).__name__}")
        try:
            return RootCause(
                codebase=str(result["codebase"]),
                file=str(result["file"]),
                line=int(result["line"]),
                symbol=str(result["symbol"]),
                explanation=str(result["explanation"]),
                confidence=float(result["confidence"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SubagentError(f"CauseTracer: invalid reply structure: {exc}") from exc
