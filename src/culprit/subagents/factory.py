"""
SubagentFactory — instantiates the four concrete subagents (§5).

Centralises dependency injection so the orchestrator never constructs
subagents directly. The factory is the only place that names concrete
subagent classes.
"""

from __future__ import annotations

from culprit.infrastructure.bob_client import BobClient
from culprit.infrastructure.workspace import Workspace
from culprit.subagents.cause_tracer import CauseTracer
from culprit.subagents.fix_author import FixAuthor
from culprit.subagents.guard import Guard
from culprit.subagents.reproducer import Reproducer


class SubagentFactory:
    """Creates fully-wired subagent instances for the orchestrator (§5)."""

    def __init__(self, workspace: Workspace, bob_client: BobClient) -> None:
        """Store shared dependencies; no subagents are created yet."""
        self._workspace = workspace
        self._bob_client = bob_client

    def make_reproducer(self) -> Reproducer:
        """Return a new Reproducer bound to the stored workspace and client."""
        return Reproducer(self._workspace, self._bob_client)

    def make_cause_tracer(self) -> CauseTracer:
        """Return a new CauseTracer bound to the stored workspace and client."""
        return CauseTracer(self._workspace, self._bob_client)

    def make_fix_author(self) -> FixAuthor:
        """Return a new FixAuthor bound to the stored workspace and client."""
        return FixAuthor(self._workspace, self._bob_client)

    def make_guard(self) -> Guard:
        """Return a new Guard bound to the stored workspace and client."""
        return Guard(self._workspace, self._bob_client)
