"""Drives the four Bob subagents in order and assembles the OnboardingPack.

Callable from the CLI, the FastAPI server, and the Streamlit UI. Emits
progress via an optional callback so the UI can render a live stepper.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from rich.console import Console

from compass.application.report_builder import write_pack
from compass.config import BobConfig, Paths, SandboxLimits
from compass.domain.exceptions import BudgetExceeded
from compass.domain.models import OnboardingPack, SubagentReport
from compass.infrastructure.bob_client import BobClient
from compass.infrastructure.source import resolve_source
from compass.infrastructure.workspace import Workspace
from compass.subagents.cartographer import CartographerSubagent
from compass.subagents.devloop_runner import DevLoopRunnerSubagent
from compass.subagents.guide import GuideSubagent
from compass.subagents.scout import ScoutSubagent

ProgressCb = Callable[[str, str], None]  # (step, message) -> None


class Orchestrator:
    def __init__(self, on_progress: ProgressCb | None = None) -> None:
        self._bob = BobClient(config=BobConfig.from_env())
        self._limits = SandboxLimits.from_env()
        self._paths = Paths()
        self._paths.ensure()
        self._console = Console()
        self._on_progress = on_progress or (lambda step, msg: self._console.print(f"[bold]{step}[/bold] {msg}"))

    def run(
        self,
        *,
        repo: str,
        role: str | None,
        difficulty: str,
        output_dir: Path,
        allow_large: bool = False,
    ) -> OnboardingPack:
        t0 = time.perf_counter()
        reports: list[SubagentReport] = []

        with Workspace.create(self._paths.workspaces) as ws:
            self._on_progress("clone", f"resolving source: {repo}")
            checkout = resolve_source(repo, ws.root)
            self._on_progress("clone", f"workspace ready at {checkout}")

            # --- Scout ---
            self._on_progress("scout", "starting inventory …")
            inventory, r = ScoutSubagent(self._bob).run(workspace=checkout)
            reports.append(r)
            self._on_progress("scout", f"done in {r.duration_seconds:.1f}s · {self._bob.bobcoins_used:.4f} Bobcoins so far")

            if not allow_large and inventory.size_estimate_tokens > self._limits.tokens_total_max:
                raise BudgetExceeded(
                    f"estimated {inventory.size_estimate_tokens} tokens "
                    f"> cap {self._limits.tokens_total_max}; pass --yes-large to proceed"
                )

            # --- Cartographer ---
            self._on_progress("cartographer", "drawing the map …")
            architecture, r = CartographerSubagent(self._bob).run(
                workspace=checkout,
                context={"inventory": inventory.model_dump()},
            )
            reports.append(r)
            self._on_progress("cartographer", f"done in {r.duration_seconds:.1f}s · {self._bob.bobcoins_used:.4f} Bobcoins so far")

            # --- DevLoopRunner ---
            # This is the slow one — Bob actually runs install / build / test commands.
            # Expect 30–120 s depending on the project.
            self._on_progress("devloop", "running install / build / test — this can take a minute …")
            devloop, r = DevLoopRunnerSubagent(self._bob).run(workspace=checkout)
            reports.append(r)
            self._on_progress("devloop", f"done in {r.duration_seconds:.1f}s · {self._bob.bobcoins_used:.4f} Bobcoins so far")

            # --- Guide ---
            self._on_progress("guide", "writing tour + starter tasks …")
            guide, r = GuideSubagent(self._bob).run(
                workspace=checkout,
                context={
                    "role": role or "any",
                    "difficulty": difficulty,
                    "inventory": inventory.model_dump(),
                    "architecture": architecture.model_dump(),
                    "devloop": devloop.model_dump(),
                },
            )
            reports.append(r)
            self._on_progress("guide", f"done in {r.duration_seconds:.1f}s · {self._bob.bobcoins_used:.4f} Bobcoins so far")

            pack = OnboardingPack(
                repo=repo,
                role=role,
                inventory=inventory,
                architecture=architecture,
                devloop=devloop,
                guide=guide,
                subagents=reports,
                total_wall_clock_seconds=time.perf_counter() - t0,
                total_tokens=sum(r.tokens_prompt + r.tokens_completion for r in reports),
                total_bobcoins=sum(r.bobcoins for r in reports),
            )
            write_pack(pack, output_dir)
            self._on_progress(
                "done",
                f"pack written to {output_dir} · {self._bob.bobcoins_used:.4f} Bobcoins total "
                f"· {pack.total_wall_clock_seconds:.1f}s wall-clock",
            )
            return pack
