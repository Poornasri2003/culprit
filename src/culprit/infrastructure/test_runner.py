"""
TestRunner — executes pytest for the codebases and parses results (§6, §9).

All test execution is deterministic subprocess work; no AI involvement.
Raises no domain exceptions — failures are represented as TestResult.passed=False.

pytest runs ONCE, in the project root: the deepest folder containing every
codebase. Multi-service projects (e.g. sample_app/{backend,shared,frontend}
with shared tests in sample_app/tests) keep their tests and conftest.py
beside the services, not inside any single one.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from culprit.domain.models import SourceCodebase, TestResult

# Seconds to wait for pytest before treating the run as failed.
_PYTEST_TIMEOUT_SECONDS: int = 120


def project_root(codebases: list[SourceCodebase]) -> Path:
    """Return the deepest directory that contains every codebase root."""
    roots = [str(cb.root_path.resolve()) for cb in codebases]
    return Path(os.path.commonpath(roots))


class TestRunner:
    """
    Runs pytest for the codebases and returns a TestResult (§6, §9).

    Pattern: Adapter — wraps subprocess pytest invocation.
    """

    __test__ = False  # stop pytest from trying to collect this class

    def run(self, codebases: list[SourceCodebase]) -> TestResult:
        """
        Invoke pytest once in the project root; return TestResult.

        Never raises — test failures are expressed through TestResult.passed.
        """
        root = project_root(codebases)
        if not self._has_test_files(root):
            return TestResult(
                passed=True,
                output=f"[{root.name}] No test files found — skipped.\n",
                failed_tests=[],
            )

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=_PYTEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                passed=False,
                output=f"[{root.name}] pytest timed out after {_PYTEST_TIMEOUT_SECONDS}s.\n",
                failed_tests=[],
            )
        except Exception as exc:  # noqa: BLE001
            return TestResult(
                passed=False,
                output=f"[{root.name}] Failed to run pytest: {exc}\n",
                failed_tests=[],
            )

        combined = result.stdout + result.stderr
        # Collect FAILED lines (pytest -q emits "FAILED path::test_name")
        failed = [
            line[len("FAILED "):].strip()
            for line in combined.splitlines()
            if line.startswith("FAILED ")
        ]
        return TestResult(
            passed=result.returncode == 0,
            output=f"[{root.name}]\n{combined}",
            failed_tests=failed,
        )

    def run_single(self, test_file: Path, cwd: Path | None = None) -> TestResult:
        """
        Invoke pytest on a single file; return TestResult.

        Used by the orchestrator to check whether the repro test actually fails.
        Never raises — failures are expressed through TestResult.passed.
        """
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)],
                # Run from the project root so its conftest.py (sys.path setup) loads.
                cwd=str(cwd or test_file.parent),
                capture_output=True,
                text=True,
                timeout=_PYTEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                passed=False,
                output=f"[single] pytest timed out on {test_file.name}\n",
                failed_tests=[],
            )
        except Exception as exc:  # noqa: BLE001
            return TestResult(
                passed=False,
                output=f"[single] Failed to run pytest on {test_file.name}: {exc}\n",
                failed_tests=[],
            )

        combined = result.stdout + result.stderr
        failed = [
            line[len("FAILED "):].strip()
            for line in combined.splitlines()
            if line.startswith("FAILED ")
        ]
        return TestResult(
            passed=result.returncode == 0,
            output=f"[single]\n{combined}",
            failed_tests=failed,
        )

    def has_tests(self, codebase: SourceCodebase) -> bool:
        """Return True if the codebase contains at least one pytest-discoverable test file."""
        return self._has_test_files(codebase.root_path)

    def has_any_tests(self, codebases: list[SourceCodebase]) -> bool:
        """Return True if the project root for these codebases contains any test file (E1)."""
        return self._has_test_files(project_root(codebases))

    @staticmethod
    def _has_test_files(root: Path) -> bool:
        return any(root.rglob("test_*.py"))
