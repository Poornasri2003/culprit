"""
Abstract Subagent base class (Strategy pattern, §5).

Each concrete subagent implements `run()` with its own input/output
types but shares the lifecycle contract defined here: budget checking,
workspace reference, and the Bob client adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from culprit.config import MAX_BOBCOIN_PER_RUN
from culprit.domain.exceptions import BudgetExhausted
from culprit.infrastructure.bob_client import BobClient
from culprit.infrastructure.workspace import Workspace


class Subagent(ABC):
    """
    Strategy base for all Culprit subagents (§5).

    Concrete subagents inject a Workspace and BobClient at construction;
    run() is the single entry point invoked by the orchestrator.
    """

    def __init__(self, workspace: Workspace, bob_client: BobClient) -> None:
        """Store workspace and Bob client; do not open files or call APIs."""
        self._workspace = workspace
        self._bob_client = bob_client

    @abstractmethod
    async def run(self, **kwargs: Any) -> Any:
        """Execute the subagent task and return its typed output."""

    def _check_budget(self, bobcoins_used: float) -> None:
        """Raise BudgetExhausted if bobcoins_used >= MAX_BOBCOIN_PER_RUN."""
        if bobcoins_used >= MAX_BOBCOIN_PER_RUN:
            raise BudgetExhausted(
                f"Bobcoin budget exhausted: used {bobcoins_used:.4f} of {MAX_BOBCOIN_PER_RUN}"
            )
