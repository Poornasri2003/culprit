"""Bob Shell subprocess adapter.

Bob Shell is a local CLI (`bob run …`) that authenticates itself from the
`BOB_API_KEY` env var — this module never reads or forwards that key. We just
give it a workspace directory and a prompt, and it comes back with a JSON
envelope on stdout whose `last_message` contains the JSON payload we asked
for.

Command shape (ported from the previous Culprit prototype):
    bob run --format json --mode <ask|agent> --max-cost <n>
            --max-turns <n> --accept-license --trust
            -w <workspace> "<prompt>"

Envelope shape:
    {"status": "success", "stats": {"session_costs": <float>, ...},
     "last_message": "<free text that contains one JSON object>"}
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from compass.config import BobConfig, SandboxLimits
from compass.domain.exceptions import BobBudgetExhausted, BobShellError, SchemaError


def _bob_argv() -> list[str]:
    """Return the base argv for invoking Bob Shell (before `run` and its flags).

    * Honours COMPASS_BOB_ENTRY when set (points at bob.js and uses node).
    * On Windows, defaults to `%APPDATA%\\npm\\node_modules\\bobshell\\dist\\bob.js` via node.
    * Elsewhere, uses `bob` on PATH.
    """
    override = os.environ.get("COMPASS_BOB_ENTRY")
    if override:
        node = shutil.which("node")
        if node is None:
            raise BobShellError("`node` not found on PATH; required to run bob.js")
        return [node, override]

    if sys.platform == "win32":
        node = shutil.which("node")
        if node is None:
            raise BobShellError("`node` not found on PATH; required to run Bob Shell on Windows")
        appdata = os.environ.get("APPDATA", "")
        bob_js = Path(appdata) / "npm" / "node_modules" / "bobshell" / "dist" / "bob.js"
        if not bob_js.exists():
            raise BobShellError(
                f"Bob Shell entry not found at {bob_js}. "
                f"Install with `npm install -g bobshell`, or set COMPASS_BOB_ENTRY."
            )
        return [node, str(bob_js)]

    bob = shutil.which("bob")
    if bob is None:
        raise BobShellError(
            "`bob` not found on PATH. Install Bob Shell locally, or set COMPASS_BOB_ENTRY."
        )
    return [bob]


def _extract_json(text: str) -> Any:
    """Return the largest JSON object or array embedded anywhere in `text`.

    Bob's `last_message` is free-form prose that contains one JSON block. Naïve
    ``json.loads`` on the whole message fails; searching for the first `{` also
    fails when Bob writes prose like "look at [f()](a.py:9)" first. We probe
    every candidate start and keep the longest successful decode.
    """
    decoder = json.JSONDecoder()
    best: Any = None
    best_span = 0
    pos = 0
    while True:
        starts = [i for i in (text.find("{", pos), text.find("[", pos)) if i != -1]
        if not starts:
            break
        start = min(starts)
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        if isinstance(obj, (dict, list)) and (end - start) > best_span:
            best, best_span = obj, end - start
        pos = end
    if best is None:
        raise ValueError(f"no JSON object found in Bob output: {text[:200]!r}")
    return best


@dataclass
class BobUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    bobcoins: float = 0.0


@dataclass
class BobClient:
    """Adapter around the Bob Shell CLI. One client per Compass run."""

    config: BobConfig
    limits: SandboxLimits = field(default_factory=SandboxLimits.from_env)
    max_bobcoins: float = field(default_factory=lambda: float(os.getenv("COMPASS_MAX_BOBCOIN_PER_RUN", "5.0")))
    timeout_seconds: int = field(default_factory=lambda: int(os.getenv("COMPASS_BOB_TIMEOUT_SECONDS", "180")))
    max_turns: int = field(default_factory=lambda: int(os.getenv("COMPASS_BOB_MAX_TURNS", "8")))
    _bobcoins_used: float = 0.0

    def run_agent(
        self,
        *,
        workspace: Path,
        prompt: str,
        mode: Literal["ask", "agent"] = "agent",
    ) -> tuple[Any, BobUsage]:
        """Invoke `bob run` once and return the JSON payload it produced.

        Retries once on malformed JSON. Raises ``BobBudgetExhausted`` when the
        Bobcoin budget is spent, and ``BobShellError`` on any subprocess or
        envelope failure.
        """
        remaining = self.max_bobcoins - self._bobcoins_used
        if remaining <= 0:
            raise BobBudgetExhausted(
                f"Bobcoin budget exhausted (used {self._bobcoins_used:.4f} of {self.max_bobcoins})"
            )

        argv = self._build_command(prompt=prompt, mode=mode, workspace=workspace, max_cost=remaining)

        for attempt in range(2):
            try:
                result = subprocess.run(
                    argv,
                    input="",  # never leave stdin open — Bob reads it as extra context
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise BobShellError(f"Bob Shell timed out after {self.timeout_seconds}s") from exc
            except FileNotFoundError as exc:
                raise BobShellError(f"Bob Shell executable not found: {exc}") from exc

            if result.returncode != 0:
                raise BobShellError(
                    f"Bob Shell exited {result.returncode}: {result.stderr.strip()[:400]}"
                )

            stdout = result.stdout.strip()
            last_line = stdout.splitlines()[-1] if stdout else ""
            try:
                envelope = json.loads(last_line)
            except json.JSONDecodeError:
                if attempt == 0:
                    continue
                raise SchemaError("Bob Shell returned no JSON envelope after retry")

            if envelope.get("status") != "success":
                raise BobShellError(f"Bob Shell status={envelope.get('status')!r}")

            stats = envelope.get("stats", {}) or {}
            cost = float(stats.get("session_costs", 0.0) or 0.0)
            self._bobcoins_used += cost
            # Bob names input/output token stats inconsistently across versions.
            # Accept every spelling we've seen and keep 0 if none show up.
            prompt = int(
                stats.get("prompt_tokens")
                or stats.get("input_tokens")
                or stats.get("total_input_tokens")
                or 0
            )
            completion = int(
                stats.get("completion_tokens")
                or stats.get("output_tokens")
                or stats.get("total_output_tokens")
                or 0
            )
            usage = BobUsage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                bobcoins=cost,
            )

            try:
                payload = _extract_json(envelope.get("last_message", ""))
            except ValueError:
                if attempt == 0:
                    continue
                raise SchemaError("Bob's last_message had no JSON after retry")
            return payload, usage

        # Unreachable but keeps the type checker happy.
        raise BobShellError("Bob Shell failed after retry")

    # --- private -------------------------------------------------------

    def _build_command(
        self, *, prompt: str, mode: str, workspace: Path, max_cost: float
    ) -> list[str]:
        base = _bob_argv()
        return [
            *base,
            "run",
            "--format", "json",
            "--mode", mode,
            "--max-cost", str(max_cost),
            "--max-turns", str(self.max_turns),
            "--accept-license",
            "--trust",
            "-w", str(workspace),
            prompt,
        ]

    @property
    def bobcoins_used(self) -> float:
        return self._bobcoins_used
