"""Cartographer — produces a verified architecture map. Read-only."""
from __future__ import annotations

import json
from typing import Any

from compass.domain.models import ArchitectureMap
from compass.subagents.base import Subagent

SYSTEM_PROMPT = """\
You are Compass's Cartographer.

Every edge in your map must be backed by evidence. Do not include an edge
unless you can cite the file and line that proves it. Prefer fewer, correct
edges over many speculative ones.

The Mermaid diagram must include only modules and edges you have already
listed. Emit exactly one JSON object matching the ArchitectureMap schema.
"""


class CartographerSubagent(Subagent[ArchitectureMap]):
    name = "cartographer"
    output_model = ArchitectureMap
    system_prompt = SYSTEM_PROMPT
    mode = "ask"

    def build_task(self, context: dict[str, Any]) -> str:
        inv = context.get("inventory", {})
        return (
            "Draw a verified architecture map for the repository rooted at the "
            "current workspace. Trace imports and cross-file references; every "
            "Edge.evidence must be a real file:line you inspected.\n\n"
            "Inventory (for orientation):\n"
            f"{json.dumps(inv, indent=2)[:3000]}"
        )
