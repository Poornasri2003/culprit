"""
ReportBuilder — assembles the final CulpritReport (Builder pattern).

Accumulates data from the orchestrator loop incrementally and produces
a single, validated CulpritReport at the end of the run.
"""

from __future__ import annotations

import time

from culprit.domain.models import (
    Adjudication,
    Attempt,
    Bug,
    CulpritReport,
    Fix,
    ReportStatus,
    RegressionTest,
    RootCause,
)


class ReportBuilder:
    """
    Incrementally assembles a CulpritReport during the orchestration loop.

    Pattern: Builder — separates the construction of the complex report
    object from the loop logic in Orchestrator.
    """

    def __init__(self, bug: Bug) -> None:
        """Initialise with the Bug; start the elapsed-time clock."""
        self._bug = bug
        self._start_time = time.monotonic()
        self._attempts: list[Attempt] = []
        self._adjudications: list[Adjudication] = []
        self._root_cause: RootCause | None = None
        self._fix: Fix | None = None
        self._regression_test: RegressionTest | None = None
        self._adjudicator_available: bool = True
        self._bobcoins_used: float = 0.0

    def add_attempt(self, attempt: Attempt) -> None:
        """Append an Attempt record to the accumulator."""
        self._attempts.append(attempt)

    def add_adjudication(self, adjudication: Adjudication) -> None:
        """Append an Adjudication record to the accumulator."""
        self._adjudications.append(adjudication)

    def set_root_cause(self, root_cause: RootCause) -> None:
        """Record the confirmed RootCause for the report."""
        self._root_cause = root_cause

    def set_fix(self, fix: Fix) -> None:
        """Record the applied Fix (only set when fix was actually applied)."""
        self._fix = fix

    def set_regression_test(self, regression_test: RegressionTest) -> None:
        """Record the regression test written by Guard."""
        self._regression_test = regression_test

    def set_adjudicator_available(self, available: bool) -> None:
        """Record whether watsonx.ai was reachable during this run."""
        self._adjudicator_available = available

    def set_bobcoins_used(self, amount: float) -> None:
        """Record the final bobcoin consumption from BobClient."""
        self._bobcoins_used = amount

    def build(self, status: ReportStatus) -> CulpritReport:
        """Finalise elapsed_seconds and return the validated CulpritReport."""
        elapsed = time.monotonic() - self._start_time
        return CulpritReport(
            status=status,
            bug=self._bug,
            root_cause=self._root_cause,
            fix=self._fix,
            regression_test=self._regression_test,
            attempts=list(self._attempts),
            elapsed_seconds=elapsed,
            bobcoins_used=self._bobcoins_used,
            adjudications=list(self._adjudications),
            adjudicator_available=self._adjudicator_available,
        )
