"""
BobClient — IBM Bob Shell subprocess adapter with header redaction (§10, §14).

Runs `bob run --format json --mode <mode> --workspace <path>
--max-cost <remaining> --accept-license "<prompt>"` via asyncio subprocess
and parses the JSON result. Headers matching REDACTED_HEADER_NAMES are
stripped from any context before it is placed in a prompt (§10).
Retries once on malformed JSON (E2). Bob Shell authenticates itself
(SSO or BOB_API_KEY); this module never reads or passes that key.
The target service's AuthContext is never given to this class.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Literal

from culprit.config import MAX_BOBCOIN_PER_RUN, REDACTED_HEADER_NAMES
from culprit.domain.exceptions import BobShellError, BudgetExhausted, SubagentError


def _bob_argv() -> list[str]:
    """
    Return the base argv for invoking Bob Shell (no prompt args yet).

    On Windows: [node, <bob.js path>, "run", ...]
    Elsewhere:  [<bob binary>, "run", ...]
    CULPRIT_BOB_ENTRY env var overrides the entry-point path.
    """
    entry_override = os.environ.get("CULPRIT_BOB_ENTRY")
    if entry_override:
        node = shutil.which("node")
        if node is None:
            raise BobShellError("node not found on PATH; required to run Bob Shell")
        return [node, entry_override]

    if sys.platform == "win32":
        node = shutil.which("node")
        if node is None:
            raise BobShellError("node not found on PATH; required to run Bob Shell on Windows")
        appdata = os.environ.get("APPDATA", "")
        bob_js = Path(appdata) / "npm" / "node_modules" / "bobshell" / "dist" / "bob.js"
        return [node, str(bob_js)]

    bob = shutil.which("bob")
    if bob is None:
        raise BobShellError("bob not found on PATH")
    return [bob]


def _extract_json(text: str) -> dict[str, Any] | list[Any]:
    """
    Return the largest JSON object or array embedded in *text*.

    Bob often writes prose (with markdown links such as "[f()](a.py:9)" or
    inline braces) before the answer, so the first "{" or "[" is not
    necessarily the start of the answer. Every candidate start position is
    tried with raw_decode; the decode spanning the most characters wins.

    Raises ValueError if no valid JSON object or array is found.
    """
    decoder = json.JSONDecoder()
    best: dict[str, Any] | list[Any] | None = None
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
        if isinstance(obj, (dict, list)) and end - start > best_span:
            best, best_span = obj, end - start
        pos = end
    if best is None:
        raise ValueError(f"No JSON object or array found in text: {text[:200]!r}")
    return best


class BobClient:
    """
    Adapter around the Bob Shell CLI (§5, §10, §14).

    Pattern: Adapter — presents a stable async interface; hides subprocess
    handling, JSON parsing, retry and budget logic.
    """

    def __init__(self, workspace_root: Path, max_bobcoins: float = MAX_BOBCOIN_PER_RUN) -> None:
        """Store the workspace root and per-run Bobcoin cap; no subprocess yet."""
        self._workspace_root = workspace_root
        self._max_bobcoins = max_bobcoins
        self._bobcoins_used: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_agent(
        self,
        prompt: str,
        context: dict[str, Any],
        *,
        mode: Literal["ask", "agent"] = "ask",
    ) -> dict[str, Any] | list[Any]:
        """
        Run one `bob run` call and return the parsed JSON object Bob was asked to emit.

        --max-cost is set to the remaining budget. Retries once on malformed
        JSON before raising SubagentError (E2). Raises BudgetExhausted when the
        cap is reached (E4) and BobShellError if the process fails.
        """
        remaining = self._max_bobcoins - self._bobcoins_used
        if remaining <= 0:
            raise BudgetExhausted(
                f"Bobcoin budget exhausted (used {self._bobcoins_used:.4f} of {self._max_bobcoins})"
            )

        # Redact sensitive headers in context before building the prompt
        redacted_context = self._redact_context(context)
        full_prompt = self._build_full_prompt(prompt, redacted_context)

        for attempt in range(2):  # try once, retry once on bad JSON (E2)
            stdout, exit_code = await self._invoke(full_prompt, mode, remaining)

            if exit_code != 0:
                raise BobShellError(
                    f"Bob Shell exited with code {exit_code}"
                )

            # Parse the outer Bob result envelope
            last_line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
            try:
                envelope = json.loads(last_line)
            except json.JSONDecodeError:
                if attempt == 0:
                    continue  # retry
                raise SubagentError(
                    f"Bob Shell returned malformed JSON after retry"
                ) from None

            # Check envelope status
            status = envelope.get("status")
            if status != "success":
                raise BobShellError(
                    f"Bob Shell returned status '{status}'"
                )

            # Accumulate cost
            stats = envelope.get("stats", {})
            cost = float(stats.get("session_costs", 0.0))
            self._bobcoins_used += cost

            # Extract the first JSON object/array from last_message
            last_message: str = envelope.get("last_message", "")
            try:
                return _extract_json(last_message)
            except ValueError:
                if attempt == 0:
                    continue  # retry
                raise SubagentError(
                    f"Bob Shell last_message contained no valid JSON after retry"
                ) from None

        # Unreachable, but satisfies type checker
        raise SubagentError("Bob Shell failed after retry")  # pragma: no cover

    def _build_command(self, prompt: str, mode: str, max_cost: float) -> list[str]:
        """Return the argv list for `bob run` (no shell string interpolation)."""
        base = _bob_argv()
        return [
            *base,
            "run",
            "--format", "json",
            "--mode", mode,
            "--max-cost", str(max_cost),
            "--max-turns", "8",
            "--accept-license",
            "--trust",
            "-w", str(self._workspace_root),
            prompt,
        ]

    def redact_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Return a copy of headers with REDACTED_HEADER_NAMES values replaced by '<redacted>'."""
        return {
            k: ("<redacted>" if k.lower() in REDACTED_HEADER_NAMES else v)
            for k, v in headers.items()
        }

    def get_bobcoins_used(self) -> float:
        """Return cumulative bobcoin spend for the current run."""
        return self._bobcoins_used

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _redact_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """Recursively redact header values in a context dict."""
        result: dict[str, Any] = {}
        for k, v in context.items():
            if k.lower() == "headers" and isinstance(v, dict):
                result[k] = self.redact_headers(v)
            elif isinstance(v, dict):
                result[k] = self._redact_context(v)
            else:
                result[k] = v
        return result

    def _build_full_prompt(self, prompt: str, context: dict[str, Any]) -> str:
        """Combine prompt and context into the full text sent to Bob."""
        if not context:
            return prompt
        ctx_text = json.dumps(context, indent=2)
        return f"{prompt}\n\nContext:\n{ctx_text}"

    async def _invoke(
        self, prompt: str, mode: str, max_cost: float
    ) -> tuple[str, int]:
        """
        Spawn Bob Shell as a subprocess and return (stdout, returncode).
        Raises BobShellError on timeout (180 s).
        """
        cmd = self._build_command(prompt, mode, max_cost)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                # Bob Shell reads piped stdin as extra context; an inherited,
                # never-closed stdin makes it wait forever.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Inherit environment so BOB_API_KEY flows through untouched
            )
            try:
                stdout_bytes, _stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=180
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise BobShellError("Bob Shell timed out after 180 s")
        except FileNotFoundError as exc:
            raise BobShellError(f"Bob Shell executable not found: {exc}") from exc

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        return stdout, proc.returncode or 0
