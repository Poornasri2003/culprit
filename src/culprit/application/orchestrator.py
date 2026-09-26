"""
Orchestrator — the self-healing debug loop (§6).

Controls the attempt loop up to MAX_ATTEMPTS, runs the four subagents
(concurrently via anyio), calls the watsonx.ai Adjudicator before every
fix application, applies or reverts fixes, runs tests, and accumulates
Attempt records.

All hard limits (MAX_ATTEMPTS, CONFIDENCE_FLOOR, etc.) are enforced here
deterministically — never delegated to AI (§9).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import anyio

from culprit.application.report_builder import ReportBuilder
from culprit.config import (
    CONFIDENCE_FLOOR,
    MAX_ATTEMPTS,
    MAX_WALLCLOCK_SECONDS,
)
from culprit.domain.exceptions import (
    BobShellError,
    BudgetExhausted,
    BugNotObservable,
    MissingCodebaseError,
    SubagentError,
    WatsonxError,
)
from culprit.domain.models import (
    Adjudication,
    AdjudicationVerdict,
    Attempt,
    Bug,
    CulpritReport,
    Endpoint,
    Fix,
    ReportStatus,
    RootCause,
    RuntimeEvidence,
    SourceCodebase,
)
from culprit.infrastructure.git_ops import GitOps
from culprit.infrastructure.http_client import HttpClient
from culprit.infrastructure.test_runner import TestRunner, project_root
from culprit.infrastructure.watsonx_client import WatsonxClient
from culprit.infrastructure.workspace import Workspace
from culprit.subagents.factory import SubagentFactory
from culprit.subagents.reproducer import FailingTest


class Orchestrator:
    """
    Controls the full Culprit self-healing loop (§6).

    Pattern: Orchestrator — coordinates subagents, infrastructure, and
    domain rules without embedding any business logic inside adapters.
    """

    def __init__(
        self,
        workspace: Workspace,
        factory: SubagentFactory,
        watsonx_client: WatsonxClient,
        test_runner: TestRunner,
        git_ops: GitOps,
        http_client: HttpClient | None,
        report_builder: ReportBuilder,
        dry_run: bool,
        progress_cb: Callable[[str], None] | None = None,
        endpoint: Endpoint | None = None,
    ) -> None:
        """Wire all dependencies; no I/O at construction time."""
        self._endpoint = endpoint
        self._workspace = workspace
        self._factory = factory
        self._watsonx = watsonx_client
        self._test_runner = test_runner
        self._git_ops = git_ops
        self._http_client = http_client
        self._report_builder = report_builder
        self._dry_run = dry_run
        self._progress_cb = progress_cb or (lambda msg: None)

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    async def run(self, bug: Bug) -> CulpritReport:
        """
        Execute up to MAX_ATTEMPTS of the self-healing loop; return the final CulpritReport.

        Adjudication runs before every fix application (§6, §12).
        A REJECT verdict prevents apply; reasoning feeds the next attempt.
        On --dry-run: adjudication still runs; nothing is applied or committed (E6).
        """
        wall_start = time.monotonic()
        codebases = self._workspace.get_codebases()
        prior_failure: str | None = None
        commit_sha: str | None = None

        for attempt_num in range(1, MAX_ATTEMPTS + 1):
            # Wall-clock guard
            if time.monotonic() - wall_start > MAX_WALLCLOCK_SECONDS:
                self._progress_cb("⏰ Wall-clock limit reached")
                break

            self._progress_cb(f"🔁 Attempt {attempt_num}/{MAX_ATTEMPTS}")

            attempt, final_status, commit_sha = await self._run_attempt(
                attempt_num=attempt_num,
                bug=bug,
                codebases=codebases,
                prior_failure=prior_failure,
                wall_start=wall_start,
            )
            self._report_builder.add_attempt(attempt)
            self._report_builder.set_bobcoins_used(
                self._factory.make_reproducer()._bob_client.get_bobcoins_used()
            )

            if final_status is not None:
                report = self._report_builder.build(final_status)
                object.__setattr__(report, "_commit_sha", commit_sha)
                return report

            prior_failure = attempt.failure_reason

        # All attempts exhausted without a fixed outcome
        report = self._report_builder.build(ReportStatus.NEEDS_HUMAN)
        object.__setattr__(report, "_commit_sha", None)
        return report

    # -----------------------------------------------------------------------
    # One attempt
    # -----------------------------------------------------------------------

    async def _run_attempt(
        self,
        attempt_num: int,
        bug: Bug,
        codebases: list[SourceCodebase],
        prior_failure: str | None,
        wall_start: float,
    ) -> tuple[Attempt, ReportStatus | None, str | None]:
        """
        Run one attempt and return (Attempt, final_status_or_None, commit_sha_or_None).

        final_status=None means: keep looping (not done yet).
        """
        subagent_outputs: dict[str, object] = {}
        repro_file_path: Path | None = None
        regression_file_path: Path | None = None

        try:
            # ── a. Optional URL probe ──────────────────────────────────────
            runtime_evidence: RuntimeEvidence | None = None
            if self._http_client is not None and self._endpoint is not None:
                self._progress_cb(
                    f"  🌐 Probing live {self._endpoint.method} {self._endpoint.url}…"
                )
                runtime_evidence = await self._http_client.probe(self._endpoint)
                self._progress_cb(
                    f"     ↳ HTTP {runtime_evidence.response_status}: "
                    f"{runtime_evidence.response_body.strip()[:120]}"
                )
                subagent_outputs["runtime_evidence"] = runtime_evidence.model_dump()

            # ── b. Reproducer ‖ CauseTracer (parallel) ────────────────────
            self._progress_cb("  🔍 Running Reproducer + CauseTracer in parallel…")
            failing_test, root_cause = await self._reproduce_and_trace(
                bug=bug,
                runtime_evidence=runtime_evidence,
                prior_failure=prior_failure,
            )
            subagent_outputs["failing_test"] = {"file": failing_test.file, "code": failing_test.code}
            subagent_outputs["root_cause"] = root_cause.model_dump()
            self._report_builder.set_root_cause(root_cause)

            # ── c. Write & run the repro test ────────────────────────────
            self._progress_cb("  🧪 Writing and running repro test…")
            proj_root = project_root(codebases)
            failing_test.file = _contained_test_path(proj_root, failing_test.file)
            repro_file_path = proj_root / failing_test.file
            repro_file_path.parent.mkdir(parents=True, exist_ok=True)
            repro_file_path.write_text(failing_test.code, encoding="utf-8")

            repro_result = self._test_runner.run_single(repro_file_path, cwd=proj_root)
            if _is_collection_error(repro_result.output):
                # The test crashed before asserting anything (bad import,
                # syntax error): that proves nothing about the bug.
                raise SubagentError(
                    "Reproducer's test could not be collected (import/setup error), "
                    "not a genuine failure"
                )
            if repro_result.passed:
                # Test passes → bug not observable (E9)
                _safe_delete(repro_file_path)
                repro_file_path = None
                raise BugNotObservable(
                    "Reproducer's failing test passed on current code — bug not observable"
                )

            # ── d. Confidence check ───────────────────────────────────────
            if root_cause.confidence < CONFIDENCE_FLOOR:
                self._progress_cb(
                    f"  ⚠️  Confidence {root_cause.confidence:.2f} < {CONFIDENCE_FLOOR} (E5)"
                )
                attempt = Attempt(
                    num=attempt_num,
                    subagent_outputs=subagent_outputs,
                    failure_reason=f"Confidence {root_cause.confidence:.2f} below floor {CONFIDENCE_FLOOR}",
                )
                _safe_delete(repro_file_path)
                return attempt, ReportStatus.NEEDS_HUMAN, None

            # ── e. FixAuthor + Adjudication ───────────────────────────────
            self._progress_cb("  ✏️  Running FixAuthor…")
            fix_author = self._factory.make_fix_author()
            candidates = await fix_author.run(root_cause=root_cause, prior_failure=prior_failure)
            subagent_outputs["fix_candidates"] = len(candidates)

            approved_fix, adjudications, last_reasoning = await self._select_approved_fix(
                root_cause=root_cause, candidates=candidates
            )
            for adj in adjudications:
                self._report_builder.add_adjudication(adj)

            if approved_fix is None:
                self._progress_cb("  ❌ All fix candidates rejected")
                attempt = Attempt(
                    num=attempt_num,
                    subagent_outputs=subagent_outputs,
                    failure_reason=last_reasoning or "All candidates rejected",
                )
                _safe_delete(repro_file_path)
                return attempt, None, None  # try next attempt

            subagent_outputs["approved_fix"] = approved_fix.target_codebase

            # ── f. dry-run: stop here ─────────────────────────────────────
            if self._dry_run:
                self._progress_cb("  🌵 --dry-run: stopping before apply")
                self._report_builder.set_fix(approved_fix)
                attempt = Attempt(
                    num=attempt_num,
                    subagent_outputs=subagent_outputs,
                )
                _safe_delete(repro_file_path)
                return attempt, ReportStatus.PARTIAL, None

            # ── g. Guard + apply ──────────────────────────────────────────
            self._progress_cb("  🛡  Running Guard…")
            guard = self._factory.make_guard()
            regression_test = await guard.run(root_cause=root_cause, fix=approved_fix)
            subagent_outputs["regression_test"] = regression_test.file

            regression_test.file = _contained_test_path(proj_root, regression_test.file)
            regression_file_path = proj_root / regression_test.file
            regression_file_path.parent.mkdir(parents=True, exist_ok=True)
            regression_file_path.write_text(regression_test.code, encoding="utf-8")

            # Apply the fix
            target_codebase = _find_codebase(codebases, approved_fix.target_codebase)
            self._progress_cb("  🔧 Applying fix…")
            self._git_ops.apply_fix(target_codebase, approved_fix)

            # ── h. Run tests ──────────────────────────────────────────────
            self._progress_cb("  🧪 Running full test suite…")
            test_result = self._test_runner.run(codebases)

            if test_result.passed:
                # Delete the repro test before committing (it must not be permanent)
                _safe_delete(repro_file_path)
                repro_file_path = None

                self._progress_cb("  ✅ Tests passed — committing…")
                self._report_builder.set_fix(approved_fix)
                self._report_builder.set_regression_test(regression_test)
                try:
                    commit_sha = self._git_ops.commit(
                        target_codebase, approved_fix, regression_test, proj_root
                    )
                    # The regression test is now committed: keep it on disk.
                    regression_file_path = None
                except Exception:
                    # Never leave a half-applied fix behind.
                    self._git_ops.revert_fix(target_codebase, approved_fix)
                    raise
                attempt = Attempt(
                    num=attempt_num,
                    subagent_outputs=subagent_outputs,
                    test_result=test_result,
                )
                return attempt, ReportStatus.FIXED, commit_sha
            else:
                # Tests failed — revert and record failure
                self._progress_cb("  ❌ Tests failed — reverting fix…")
                self._git_ops.revert_fix(target_codebase, approved_fix)
                failure_reason = f"Tests failed: {', '.join(test_result.failed_tests[:5])}"
                attempt = Attempt(
                    num=attempt_num,
                    subagent_outputs=subagent_outputs,
                    test_result=test_result,
                    failure_reason=failure_reason,
                )
                _safe_delete(repro_file_path)
                _safe_delete(regression_file_path)
                repro_file_path = None
                regression_file_path = None
                return attempt, None, None  # try next attempt

        except BugNotObservable as exc:
            self._progress_cb(f"  ⚠️  Bug not observable (E9): {exc}")
            attempt = Attempt(
                num=attempt_num,
                subagent_outputs=subagent_outputs,
                failure_reason=str(exc),
            )
            return attempt, ReportStatus.NEEDS_HUMAN, None

        except BudgetExhausted as exc:
            self._progress_cb(f"  💰 Budget exhausted (E4): {exc}")
            attempt = Attempt(
                num=attempt_num,
                subagent_outputs=subagent_outputs,
                failure_reason=str(exc),
            )
            return attempt, ReportStatus.PARTIAL, None

        except MissingCodebaseError as exc:
            self._progress_cb(f"  ⚠️  Missing codebase (E10): {exc}")
            attempt = Attempt(
                num=attempt_num,
                subagent_outputs=subagent_outputs,
                failure_reason=str(exc),
            )
            return attempt, ReportStatus.NEEDS_HUMAN, None

        except (SubagentError, BobShellError) as exc:
            self._progress_cb(f"  ⚠️  Subagent error: {exc}")
            attempt = Attempt(
                num=attempt_num,
                subagent_outputs=subagent_outputs,
                failure_reason=str(exc),
            )
            return attempt, None, None  # try next attempt

        finally:
            # Always delete temporary test files, even on errors
            _safe_delete(repro_file_path)
            _safe_delete(regression_file_path)

    # -----------------------------------------------------------------------
    # Parallel Reproducer + CauseTracer
    # -----------------------------------------------------------------------

    async def _reproduce_and_trace(
        self,
        bug: Bug,
        runtime_evidence: RuntimeEvidence | None,
        prior_failure: str | None,
    ) -> tuple[FailingTest, RootCause]:
        """Run Reproducer and CauseTracer concurrently via anyio (the only parallel step)."""
        failing_test_holder: list[FailingTest] = []
        root_cause_holder: list[RootCause] = []

        reproducer = self._factory.make_reproducer()
        cause_tracer = self._factory.make_cause_tracer()

        async def run_reproducer() -> None:
            result = await reproducer.run(
                bug=bug,
                runtime_evidence=runtime_evidence,
                prior_failure=prior_failure,
            )
            failing_test_holder.append(result)

        async def run_cause_tracer() -> None:
            result = await cause_tracer.run(
                bug=bug,
                runtime_evidence=runtime_evidence,
                prior_failure=prior_failure,
            )
            root_cause_holder.append(result)

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(run_reproducer)
                tg.start_soon(run_cause_tracer)
        except BaseExceptionGroup as group:
            # Unwrap so callers see the domain exception (SubagentError,
            # BudgetExhausted, ...) and can map it per ARCHITECTURE §23.
            raise group.exceptions[0] from None

        return failing_test_holder[0], root_cause_holder[0]

    # -----------------------------------------------------------------------
    # Adjudication
    # -----------------------------------------------------------------------

    async def _select_approved_fix(
        self,
        root_cause: RootCause,
        candidates: list[Fix],
    ) -> tuple[Fix | None, list[Adjudication], str | None]:
        """
        Adjudicate candidates lowest-risk first.
        Returns (first_approved_fix_or_None, all_adjudications, last_reasoning).
        """
        adjudications: list[Adjudication] = []
        last_reasoning: str | None = None

        for candidate in candidates:
            adj = await self._adjudicate(root_cause, candidate)
            if adj is not None:
                adjudications.append(adj)
                last_reasoning = adj.reasoning
                if adj.verdict == AdjudicationVerdict.APPROVE:
                    return candidate, adjudications, last_reasoning
            else:
                # Watsonx not configured / error → fail-open: take this candidate
                self._progress_cb("  ⚠️  Adjudicator unavailable — failing open")
                return candidate, adjudications, None

        return None, adjudications, last_reasoning

    async def _adjudicate(
        self,
        root_cause: RootCause,
        fix: Fix,
    ) -> Adjudication | None:
        """
        Await WatsonxClient.adjudicate; return None on any WatsonxError or if not configured.
        Calls report_builder.set_adjudicator_available(False) when returning None.
        """
        if not self._watsonx.is_configured():
            self._report_builder.set_adjudicator_available(False)
            return None

        source_context = self._build_source_context(root_cause)
        try:
            return await self._watsonx.adjudicate(root_cause, fix, source_context)
        except WatsonxError:
            self._report_builder.set_adjudicator_available(False)
            return None

    def _build_source_context(self, root_cause: RootCause) -> str:
        """Assemble redacted source context lines around root_cause.file:line."""
        try:
            abs_path = self._workspace.resolve_file(root_cause.codebase, root_cause.file)
            lines = abs_path.read_text(encoding="utf-8").splitlines()
            start = max(0, root_cause.line - 10)
            end = min(len(lines), root_cause.line + 10)
            context_lines = lines[start:end]
            return "\n".join(context_lines)
        except (KeyError, OSError):
            return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contained_test_path(proj_root: Path, rel: str) -> str:
    """Return rel as a path relative to proj_root that stays inside it.

    Bob sometimes answers with paths relative to a codebase ("../tests/x.py")
    instead of the project root; anything that escapes the project root is
    placed in proj_root/tests/ under its own file name.
    """
    root = proj_root.resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root):
        candidate = root / "tests" / Path(rel).name
    return candidate.relative_to(root).as_posix()


def _is_collection_error(pytest_output: str) -> bool:
    """True when pytest failed to import/collect a test rather than run it."""
    return "ERROR collecting" in pytest_output or "errors during collection" in pytest_output         or "error during collection" in pytest_output

def _safe_delete(path: Path | None) -> None:
    """Delete a file if it exists; silently ignore errors."""
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _find_codebase(codebases: list[SourceCodebase], name: str) -> SourceCodebase:
    """Find a codebase by name; raise MissingCodebaseError if not found."""
    for cb in codebases:
        if cb.name == name:
            return cb
    raise MissingCodebaseError(
        f"Codebase '{name}' not found in workspace. Available: "
        + ", ".join(cb.name for cb in codebases)
    )
