"""Guide — reading tour and three starter tasks. Read-only."""
from __future__ import annotations

import json
from typing import Any

from compass.domain.models import Guide
from compass.subagents.base import Subagent

SYSTEM_PROMPT = """\
You are Compass's Guide.

Pick tour stops by dependency depth, not by file size. A good tour teaches
the shape of the system in about 30 minutes.

Draft starter tasks a new teammate can finish in half a day — no research
spikes, no infra changes, no rewrites. Each task must have three hint
levels: `small` (a nudge), `medium` (a pointer), `large` (nearly the answer).

Emit exactly one JSON object matching the Guide schema, with 6–10 tour stops
and exactly three starter tasks.
"""


class GuideSubagent(Subagent[Guide]):
    name = "guide"
    output_model = Guide
    system_prompt = SYSTEM_PROMPT
    mode = "ask"

    def build_task(self, context: dict[str, Any]) -> str:
        role = context.get("role", "any")
        difficulty = context.get("difficulty", "easy")
        return (
            f"Write a 30-minute reading tour and 3 starter tasks (difficulty={difficulty}) "
            f"for role={role}. The repo is at the current workspace. Use git log to find "
            f"hot files, and TODOs / thin tests to seed starter tasks.\n\n"
            f"Inventory + architecture + dev-loop so far:\n"
            f"{json.dumps({k: v for k, v in context.items() if k != 'role'}, indent=2)[:3500]}"
        )
