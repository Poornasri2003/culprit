"""Sandboxed command execution for DevLoopRunner.

Guarantees:
  * runs inside the given workspace (no cwd escape),
  * enforces the per-command timeout and output byte cap,
  * refuses commands on the deny-list without invoking a shell.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from compass.config import SandboxLimits
from compass.domain.exceptions import SandboxViolation

# Programs / patterns that never run, even if Bob asks.
DENYLIST_TOKENS = frozenset({
    "rm -rf /",
    "mkfs",
    "shutdown",
    "reboot",
    "dd if=/dev/",
    ":(){:|:&};:",  # fork bomb
})
DENYLIST_PROGRAMS = frozenset({"sudo", "su", "chpasswd", "useradd", "userdel", "passwd"})


@dataclass
class CommandResult:
    exit_code: int
    duration_seconds: float
    tail_stdout: str
    tail_stderr: str


def check_allowed(cmd: str) -> None:
    lowered = cmd.strip().lower()
    for token in DENYLIST_TOKENS:
        if token in lowered:
            raise SandboxViolation(f"denied command (matched {token!r}): {cmd!r}")
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError as e:
        raise SandboxViolation(f"unparseable command: {cmd!r} ({e})") from e
    if parts and parts[0] in DENYLIST_PROGRAMS:
        raise SandboxViolation(f"denied program: {parts[0]!r}")


def run(cmd: str, *, cwd: Path, limits: SandboxLimits) -> CommandResult:
    """Run `cmd` in `cwd` with limits. Never invokes /bin/sh -c."""
    check_allowed(cmd)
    # TODO(compass): implement with subprocess.run(shlex.split(cmd), timeout=..., capture_output=True)
    #                and truncate stdout/stderr to `limits.cmd_output_max_bytes`.
    raise NotImplementedError("command_runner.run")
