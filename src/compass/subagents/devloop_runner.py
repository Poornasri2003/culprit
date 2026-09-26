"""DevLoopRunner — Bob proposes commands, runs them, only records what worked."""
from __future__ import annotations

from typing import Any

from compass.domain.models import DevLoop
from compass.subagents.base import Subagent

SYSTEM_PROMPT = """\
You are Compass's DevLoopRunner.

You may only claim a command works if you *ran it in this session* and it
returned exit code 0. If a command fails, either propose a fix and retry
once, or record it with `verdict: "failed"` and move on. Never invent
commands you did not execute.

Order matters: install prerequisites before build; build before test; test
before lint. Emit exactly one JSON object matching the DevLoop schema.
"""


class DevLoopRunnerSubagent(Subagent[DevLoop]):
    name = "devloop_runner"
    output_model = DevLoop
    system_prompt = SYSTEM_PROMPT
    mode = "agent"  # allowed to invoke run_command

    def build_task(self, context: dict[str, Any]) -> str:
        return (
            "Establish the developer dev-loop for the repository rooted at the "
            "current workspace. Try install / build / test / lint commands "
            "appropriate to the package manager. Cap each command at 60 seconds. "
            "Record real exit codes, durations, and tail output."
        )
