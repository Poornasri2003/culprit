"""
Tests for the Orchestrator (new Part 4).

Fake subagents / fake GitOps — no real Bob, no network.
Covers:
  - FIXED path: repro fails → confidence OK → lowest-risk fix → tests pass → commit
  - E9 path: repro test passes on current code → NEEDS_HUMAN
  - E5 path: confidence below floor → NEEDS_HUMAN
  - Candidate selection: the lowest-risk (first) candidate is applied
  - dry-run: nothing applied or committed
  - Temp test files are always deleted even on errors
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
import anyio

from culprit.application.orchestrator import Orchestrator
from culprit.application.report_builder import ReportBuilder
from culprit.config import CONFIDENCE_FLOOR
from culprit.domain.exceptions import SubagentError
from culprit.domain.models import (
    Bug,
    FileEdit,
    Fix,
    RegressionTest,
    ReportStatus,
    RootCause,
    SourceCodebase,
    TestResult,
)
from culprit.infrastructure.git_ops import GitOps
from culprit.infrastructure.test_runner import TestRunner
from culprit.infrastructure.workspace import Workspace
from culprit.subagents.factory import SubagentFactory
from culprit.subagents.reproducer import FailingTest


# ---------------------------------------------------------------------------
# Shared test fixtures and factories
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """A minimal project root with a tests/ folder."""
    (tmp_path / "tests").mkdir()
    return tmp_path


@pytest.fixture()
def workspace(tmp_project: Path) -> Workspace:
    ws = Workspace(work_dir=tmp_project)
    cb_dir = tmp_project / "myapp"
    cb_dir.mkdir()
    (cb_dir / "app.py").write_text("total = price - discount\n")
    (cb_dir / "tests").mkdir()
    ws.load_folder(cb_dir, "myapp")
    return ws


@pytest.fixture()
def bug() -> Bug:
    return Bug(
        description="cart total wrong",
        runtime_evidence=[],
        static_evidence=[],
    )


@pytest.fixture()
def root_cause() -> RootCause:
    return RootCause(
        codebase="myapp",
        file="app.py",
        line=1,
        symbol="total",
        explanation="discount applied before tax",
        confidence=0.92,
    )


@pytest.fixture()
def good_fix() -> Fix:
    return Fix(
        target_codebase="myapp",
        edits=[FileEdit(file="app.py", old_text="total = price - discount", new_text="total = price * 1.1 - discount")],
    )


@pytest.fixture()
def regression_test_obj() -> RegressionTest:
    return RegressionTest(
        codebase="myapp",
        file="tests/test_culprit_regression.py",
        test_name="test_discount_after_tax",
        code="def test_discount_after_tax():\n    assert True\n",
    )


@pytest.fixture()
def failing_test_obj() -> FailingTest:
    return FailingTest(
        file="tests/test_culprit_repro.py",
        code="def test_repro():\n    assert False\n",
    )


def _make_orchestrator(
    workspace: Workspace,
    bug: Bug,
    *,
    failing_test: FailingTest,
    root_cause: RootCause,
    fixes: list[Fix],
    regression_test: RegressionTest,
    repro_passes: bool = False,   # True → E9
    tests_pass: bool = True,
    dry_run: bool = False,
    tmp_path: Path | None = None,
) -> tuple[Orchestrator, MagicMock, MagicMock, MagicMock]:
    """
    Build an Orchestrator with all dependencies mocked.
    Returns (orchestrator, mock_factory, mock_git_ops, mock_test_runner).
    """
    # --- Fake factory with async subagents ---
    mock_reproducer = MagicMock()
    mock_reproducer.run = AsyncMock(return_value=failing_test)

    mock_cause_tracer = MagicMock()
    mock_cause_tracer.run = AsyncMock(return_value=root_cause)

    mock_fix_author = MagicMock()
    mock_fix_author.run = AsyncMock(return_value=fixes)

    mock_guard = MagicMock()
    mock_guard.run = AsyncMock(return_value=regression_test)

    mock_factory = MagicMock(spec=SubagentFactory)
    mock_factory.make_reproducer.return_value = mock_reproducer
    mock_factory.make_cause_tracer.return_value = mock_cause_tracer
    mock_factory.make_fix_author.return_value = mock_fix_author
    mock_factory.make_guard.return_value = mock_guard

    # make_reproducer()._bob_client.get_bobcoins_used() for cost tracking
    mock_reproducer._bob_client = MagicMock()
    mock_reproducer._bob_client.get_bobcoins_used.return_value = 0.5

    # --- Fake git ops ---
    mock_git_ops = MagicMock(spec=GitOps)
    mock_git_ops.apply_fix.return_value = None
    mock_git_ops.revert_fix.return_value = None
    mock_git_ops.commit.return_value = "abc1234deadbeef"

    # --- Fake test runner ---
    repro_result = TestResult(
        passed=repro_passes,
        output="repro output",
        failed_tests=[] if repro_passes else ["test_repro"],
    )
    full_result = TestResult(
        passed=tests_pass,
        output="full suite output",
        failed_tests=[] if tests_pass else ["test_something"],
    )
    mock_test_runner = MagicMock(spec=TestRunner)
    mock_test_runner.run_single.return_value = repro_result
    mock_test_runner.run.return_value = full_result

    # --- Report builder ---
    report_builder = ReportBuilder(bug)

    proj_root = tmp_path or Path("/tmp/fake")

    orchestrator = Orchestrator(
        workspace=workspace,
        factory=mock_factory,
        test_runner=mock_test_runner,
        git_ops=mock_git_ops,
        http_client=None,
        report_builder=report_builder,
        dry_run=dry_run,
    )
    return orchestrator, mock_factory, mock_git_ops, mock_test_runner


# ---------------------------------------------------------------------------
# FIXED path
# ---------------------------------------------------------------------------

class TestFixedPath:
    @pytest.mark.asyncio
    async def test_fixed_status_on_happy_path(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            repro_passes=False,
            tests_pass=True,
            tmp_path=tmp_path,
        )
        report = await orch.run(bug)
        assert report.status == ReportStatus.FIXED

    @pytest.mark.asyncio
    async def test_commit_called_on_fixed(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            tests_pass=True,
            tmp_path=tmp_path,
        )
        await orch.run(bug)
        assert git_ops.commit.called

    @pytest.mark.asyncio
    async def test_apply_fix_called_on_fixed(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            tests_pass=True,
            tmp_path=tmp_path,
        )
        await orch.run(bug)
        assert git_ops.apply_fix.called

    @pytest.mark.asyncio
    async def test_lowest_risk_candidate_is_applied(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        riskier_fix = Fix(
            target_codebase="myapp",
            edits=[FileEdit(file="app.py", old_text="total = price - discount", new_text="total = refactored()")],
        )
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix, riskier_fix],
            regression_test=regression_test_obj,
            tests_pass=True,
            tmp_path=tmp_path,
        )
        await orch.run(bug)
        applied = git_ops.apply_fix.call_args.args[1]
        assert applied is good_fix

    @pytest.mark.asyncio
    async def test_repro_test_file_deleted_after_fixed(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            tests_pass=True,
            tmp_path=tmp_path,
        )
        # The repro test file path is written then deleted before commit
        proj_root = from_workspace_project_root(workspace)
        repro_path = proj_root / failing_test_obj.file
        await orch.run(bug)
        assert not repro_path.exists(), "Repro test file must be deleted after FIXED"


# ---------------------------------------------------------------------------
# E9 — BugNotObservable
# ---------------------------------------------------------------------------

class TestE9BugNotObservable:
    @pytest.mark.asyncio
    async def test_needs_human_when_repro_passes(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            repro_passes=True,  # repro test passes → E9
            tmp_path=tmp_path,
        )
        report = await orch.run(bug)
        assert report.status == ReportStatus.NEEDS_HUMAN

    @pytest.mark.asyncio
    async def test_no_commit_on_e9(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            repro_passes=True,
            tmp_path=tmp_path,
        )
        await orch.run(bug)
        git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_repro_file_deleted_on_e9(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            repro_passes=True,
            tmp_path=tmp_path,
        )
        proj_root = from_workspace_project_root(workspace)
        repro_path = proj_root / failing_test_obj.file
        await orch.run(bug)
        assert not repro_path.exists(), "Repro test file must be deleted even on E9"


# ---------------------------------------------------------------------------
# E5 — Low confidence
# ---------------------------------------------------------------------------

class TestE5LowConfidence:
    @pytest.mark.asyncio
    async def test_needs_human_on_low_confidence(
        self, workspace, bug, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        low_conf_root_cause = RootCause(
            codebase="myapp", file="app.py", line=1, symbol="total",
            explanation="discount before tax", confidence=CONFIDENCE_FLOOR - 0.01
        )
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=low_conf_root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            repro_passes=False,
            tmp_path=tmp_path,
        )
        report = await orch.run(bug)
        assert report.status == ReportStatus.NEEDS_HUMAN

    @pytest.mark.asyncio
    async def test_no_fix_applied_on_low_confidence(
        self, workspace, bug, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        low_conf = RootCause(
            codebase="myapp", file="app.py", line=1, symbol="total",
            explanation="e", confidence=CONFIDENCE_FLOOR - 0.01
        )
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=low_conf,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            tmp_path=tmp_path,
        )
        await orch.run(bug)
        git_ops.apply_fix.assert_not_called()


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_does_not_apply_fix(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            dry_run=True,
            tmp_path=tmp_path,
        )
        await orch.run(bug)
        git_ops.apply_fix.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_commit(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            dry_run=True,
            tmp_path=tmp_path,
        )
        await orch.run(bug)
        git_ops.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_returns_partial(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            dry_run=True,
            tmp_path=tmp_path,
        )
        report = await orch.run(bug)
        assert report.status == ReportStatus.PARTIAL

    @pytest.mark.asyncio
    async def test_dry_run_repro_file_deleted(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            dry_run=True,
            tmp_path=tmp_path,
        )
        proj_root = from_workspace_project_root(workspace)
        repro_path = proj_root / failing_test_obj.file
        await orch.run(bug)
        assert not repro_path.exists()


# ---------------------------------------------------------------------------
# Temp files always deleted
# ---------------------------------------------------------------------------

class TestTempFilesCleanup:
    @pytest.mark.asyncio
    async def test_repro_file_deleted_on_subagent_error(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        """Even when FixAuthor raises, temp files must be cleaned up."""
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            tmp_path=tmp_path,
        )
        # Make FixAuthor raise after the repro file would have been written
        factory.make_fix_author.return_value.run = AsyncMock(
            side_effect=SubagentError("Bob broke")
        )
        proj_root = from_workspace_project_root(workspace)
        repro_path = proj_root / failing_test_obj.file
        await orch.run(bug)
        assert not repro_path.exists(), "Repro file must be cleaned up even on SubagentError"

    @pytest.mark.asyncio
    async def test_both_files_deleted_when_tests_fail(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            tests_pass=False,
            tmp_path=tmp_path,
        )
        proj_root = from_workspace_project_root(workspace)
        repro_path = proj_root / failing_test_obj.file
        regr_path = proj_root / regression_test_obj.file
        await orch.run(bug)
        assert not repro_path.exists()
        assert not regr_path.exists()


# ---------------------------------------------------------------------------
# Tests-fail → revert
# ---------------------------------------------------------------------------

class TestTestsFailRevert:
    @pytest.mark.asyncio
    async def test_revert_called_when_tests_fail(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            tests_pass=False,
            tmp_path=tmp_path,
        )
        await orch.run(bug)
        assert git_ops.revert_fix.called

    @pytest.mark.asyncio
    async def test_no_commit_when_tests_fail(
        self, workspace, bug, root_cause, good_fix, regression_test_obj, failing_test_obj, tmp_path
    ):
        orch, factory, git_ops, test_runner = _make_orchestrator(
            workspace, bug,
            failing_test=failing_test_obj,
            root_cause=root_cause,
            fixes=[good_fix],
            regression_test=regression_test_obj,
            tests_pass=False,
            tmp_path=tmp_path,
        )
        await orch.run(bug)
        git_ops.commit.assert_not_called()


# ---------------------------------------------------------------------------
# ReportBuilder
# ---------------------------------------------------------------------------

class TestReportBuilder:
    def test_build_fixed_report(self, bug):
        from culprit.application.report_builder import ReportBuilder
        rb = ReportBuilder(bug)
        report = rb.build(ReportStatus.FIXED)
        assert report.status == ReportStatus.FIXED
        assert report.bug is bug
        assert report.elapsed_seconds >= 0
        assert report.bobcoins_used == 0.0

    def test_accumulates_attempts(self, bug):
        from culprit.application.report_builder import ReportBuilder
        from culprit.domain.models import Attempt
        rb = ReportBuilder(bug)
        rb.add_attempt(Attempt(num=1, subagent_outputs={}))
        rb.add_attempt(Attempt(num=2, subagent_outputs={}))
        report = rb.build(ReportStatus.PARTIAL)
        assert len(report.attempts) == 2

    def test_set_bobcoins_used(self, bug):
        from culprit.application.report_builder import ReportBuilder
        rb = ReportBuilder(bug)
        rb.set_bobcoins_used(3.14)
        report = rb.build(ReportStatus.FIXED)
        assert report.bobcoins_used == pytest.approx(3.14)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def from_workspace_project_root(workspace: Workspace) -> Path:
    """Return the project_root for the workspace's codebases."""
    from culprit.infrastructure.test_runner import project_root
    return project_root(workspace.get_codebases())
