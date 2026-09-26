"""Base class for Compass subagents.

Design note: Bob Shell has its own file-reading, ripgrep, and command-running
tools built into `bob run --workspace`. That means a Compass subagent is not a
tool loop we drive from Python — it's a *prompt* that asks Bob to explore the
workspace and emit one JSON object matching a Pydantic schema. Bob does the
exploration; we validate the answer.

Each subclass sets:
  * ``name`` — one of the Literal names in SubagentReport.
  * ``output_model`` — the Pydantic schema for the JSON reply.
  * ``system_prompt`` — the standing role/rules for the subagent.
  * ``mode`` — "ask" for read-only exploration; "agent" when Bob may need to
    execute commands (DevLoopRunner).

Each subclass implements ``build_task(context) -> str``: the concrete task
prompt appended to the system prompt for a specific run.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from compass.domain.exceptions import SchemaError
from compass.domain.models import SubagentReport
from compass.infrastructure.bob_client import BobClient

TOutput = TypeVar("TOutput", bound=BaseModel)


class Subagent(ABC, Generic[TOutput]):
    name: str
    output_model: type[TOutput]
    system_prompt: str
    mode: str = "agent"

    def __init__(self, bob: BobClient) -> None:
        self._bob = bob

    @abstractmethod
    def build_task(self, context: dict[str, Any]) -> str:
        """Return the per-run task text appended after the system prompt."""

    def run(
        self,
        *,
        workspace: Path,
        context: dict[str, Any] | None = None,
    ) -> tuple[TOutput, SubagentReport]:
        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        prompt = self._compose_prompt(context or {})

        payload, usage = self._bob.run_agent(workspace=workspace, prompt=prompt, mode=self.mode)  # type: ignore[arg-type]
        retries = 0
        try:
            result = self.output_model.model_validate(payload)
        except ValidationError as first:
            retries = 1
            retry_prompt = (
                f"{prompt}\n\n"
                f"Your previous JSON did not validate against the schema. "
                f"Errors:\n{first}\n"
                f"Reply with a corrected single JSON object. Do not explain."
            )
            payload, more = self._bob.run_agent(
                workspace=workspace, prompt=retry_prompt, mode=self.mode,  # type: ignore[arg-type]
            )
            usage.prompt_tokens += more.prompt_tokens
            usage.completion_tokens += more.completion_tokens
            usage.bobcoins += more.bobcoins
            try:
                result = self.output_model.model_validate(payload)
            except ValidationError as second:
                raise SchemaError(f"{self.name} failed schema twice: {second}") from second

        report = SubagentReport(
            name=self.name,  # type: ignore[arg-type]
            started_at=started.isoformat(),
            duration_seconds=time.perf_counter() - t0,
            tokens_prompt=usage.prompt_tokens,
            tokens_completion=usage.completion_tokens,
            retries=retries,
            ok=True,
        )
        return result, report

    def _compose_prompt(self, context: dict[str, Any]) -> str:
        task = self.build_task(context)
        schema_hint = (
            "Return exactly one JSON object matching this schema:\n"
            f"{self.output_model.model_json_schema()}\n"
            "Do not wrap it in prose beyond what is needed."
        )
        return f"{self.system_prompt}\n\n{task}\n\n{schema_hint}"
