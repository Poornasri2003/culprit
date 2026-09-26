"""
Tests for subagents (Part C).

All BobClient.run_agent calls are mocked — no real Bob, no network, no cost.
Covers each subagent turning a fake reply into its domain model, and
validation/error paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from culprit.domain.exceptions import BudgetExhausted, SubagentError
from culprit.domain.models import (
    Bug,
    Endpoint,
    Fix,
    RootCause,
    RuntimeEvidence,
    SourceCodebase,
)
from culprit.infrastructure.bob_client import BobClient
from culprit.infrastructure.workspace import Workspace
from culprit.subagents.cause_tracer import CauseTracer
from culprit.subagents.factory import SubagentFactory
from culprit.subagents.fix_author import FixAuthor
from culprit.subagents.guard import Guard
from culprit.subagents.reproducer import FailingTest, Reproducer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path: Path) -> Workspace:
    ws = Workspace(work_dir=tmp_path)
    # Register one fake codebase so prompts are non-empty
    cb_dir = tmp_path / "myapp"
    cb_dir.mkdir()
    (cb_dir / "app.py").write_text("# fake\n")
    ws.load_folder(cb_dir, "myapp")
    return ws


@pytest.fixture()
def bob_client(tmp_path: Path) -> BobClient:
    return BobClient(workspace_root=tmp_path, max_bobcoins=8.0)


@pytest.fixture()
def bug() -> Bug:
    return Bug(
        description="cart total is wrong when discount applied",
        runtime_evidence=[],
        static_evidence=[],
    )


@pytest.fixture()
def runtime_evidence() -> RuntimeEvidence:
    return RuntimeEvidence(
        endpoint=Endpoint(
            url="http://localhost:8000/cart/total",
            method="GET",
            headers={"Authorization": "Bearer token123", "Content-Type": "application/json"},
            expected_status=200,
            actual_status=200,
        ),
        request_body=None,
        response_body='{"total": 99.99}',
        response_status=200,
        latency_ms=42.0,
        stack_trace_hint=None,
    )


@pytest.fixture()
def root_cause() -> RootCause:
    return RootCause(
        codebase="myapp",
        file="pricing.py",
        line=42,
        symbol="apply_discount",
        explanation="Discount is applied before tax calculation instead of after",
        confidence=0.92,
    )


@pytest.fixture()
def fix() -> Fix:
    return Fix(
        target_codebase="myapp",
        unified_diff="--- a/pricing.py\n+++ b/pricing.py\n-total = price - discount\n+total = price * tax_rate - discount",
        applied_files=["pricing.py"],
    )


def _mock_run_agent(client: BobClient, return_value):
    """Patch BobClient.run_agent to return return_value immediately."""
    mock = AsyncMock(return_value=return_value)
    return patch.object(client, "run_agent", mock)


# ---------------------------------------------------------------------------
# SubagentFactory
# ---------------------------------------------------------------------------

class TestSubagentFactory:
    def test_makes_reproducer(self, workspace, bob_client):
        factory = SubagentFactory(workspace, bob_client)
        r = factory.make_reproducer()
        assert isinstance(r, Reproducer)

    def test_makes_cause_tracer(self, workspace, bob_client):
        factory = SubagentFactory(workspace, bob_client)
        ct = factory.make_cause_tracer()
        assert isinstance(ct, CauseTracer)

    def test_makes_fix_author(self, workspace, bob_client):
        factory = SubagentFactory(workspace, bob_client)
        fa = factory.make_fix_author()
        assert isinstance(fa, FixAuthor)

    def test_makes_guard(self, workspace, bob_client):
        factory = SubagentFactory(workspace, bob_client)
        g = factory.make_guard()
        assert isinstance(g, Guard)

    def test_all_share_same_workspace_and_client(self, workspace, bob_client):
        factory = SubagentFactory(workspace, bob_client)
        r = factory.make_reproducer()
        ct = factory.make_cause_tracer()
        assert r._workspace is workspace
        assert ct._bob_client is bob_client


# ---------------------------------------------------------------------------
# Reproducer
# ---------------------------------------------------------------------------

class TestReproducer:
    @pytest.mark.asyncio
    async def test_returns_failing_test(self, workspace, bob_client, bug):
        reply = {"file": "tests/test_culprit_repro.py", "code": "def test_bug(): assert False"}
        with _mock_run_agent(bob_client, reply):
            result = await Reproducer(workspace, bob_client).run(bug=bug, runtime_evidence=None)
        assert isinstance(result, FailingTest)
        assert result.file == "tests/test_culprit_repro.py"
        assert "assert False" in result.code

    @pytest.mark.asyncio
    async def test_prompt_contains_bug_description(self, workspace, bob_client, bug):
        reply = {"file": "tests/test_culprit_repro.py", "code": "pass"}
        captured: list[str] = []

        async def capture_run(prompt, context, *, mode="ask"):
            captured.append(prompt)
            return reply

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await Reproducer(workspace, bob_client).run(bug=bug, runtime_evidence=None)

        assert bug.description in captured[0]

    @pytest.mark.asyncio
    async def test_prompt_contains_codebase_name(self, workspace, bob_client, bug):
        reply = {"file": "tests/test_culprit_repro.py", "code": "pass"}
        captured: list[str] = []

        async def capture_run(prompt, context, *, mode="ask"):
            captured.append(prompt)
            return reply

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await Reproducer(workspace, bob_client).run(bug=bug, runtime_evidence=None)

        assert "myapp" in captured[0]

    @pytest.mark.asyncio
    async def test_prompt_includes_runtime_evidence(self, workspace, bob_client, bug, runtime_evidence):
        reply = {"file": "tests/test_culprit_repro.py", "code": "pass"}
        captured: list[str] = []

        async def capture_run(prompt, context, *, mode="ask"):
            captured.append(prompt)
            return reply

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await Reproducer(workspace, bob_client).run(bug=bug, runtime_evidence=runtime_evidence)

        assert "http://localhost:8000/cart/total" in captured[0]
        assert "99.99" in captured[0]

    @pytest.mark.asyncio
    async def test_runtime_evidence_headers_not_leaked(self, workspace, bob_client, bug, runtime_evidence):
        """Authorization token in runtime evidence headers must not appear in the prompt."""
        reply = {"file": "tests/test_culprit_repro.py", "code": "pass"}
        # The runtime_evidence fixture has Authorization: Bearer token123 in endpoint headers.
        # Those headers are NOT passed through context (context is {}) — the subagent
        # only mentions URL/method/response in the prompt, not the raw headers dict.
        # This test verifies the raw token does NOT leak into the run_agent call context.
        context_captured: list = []

        async def capture_run(prompt, context, *, mode="ask"):
            context_captured.append(context)
            return reply

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await Reproducer(workspace, bob_client).run(bug=bug, runtime_evidence=runtime_evidence)

        # Context passed to run_agent should be empty {}
        assert context_captured[0] == {}

    @pytest.mark.asyncio
    async def test_raises_subagent_error_on_list_reply(self, workspace, bob_client, bug):
        with _mock_run_agent(bob_client, [{"oops": "wrong type"}]):
            with pytest.raises(SubagentError, match="expected dict"):
                await Reproducer(workspace, bob_client).run(bug=bug, runtime_evidence=None)

    @pytest.mark.asyncio
    async def test_raises_subagent_error_on_missing_key(self, workspace, bob_client, bug):
        with _mock_run_agent(bob_client, {"file": "f.py"}):  # missing "code"
            with pytest.raises(SubagentError, match="missing key"):
                await Reproducer(workspace, bob_client).run(bug=bug, runtime_evidence=None)

    @pytest.mark.asyncio
    async def test_raises_budget_exhausted(self, workspace, bug):
        exhausted_client = BobClient(workspace_root=Path("/x"), max_bobcoins=0.0)
        with pytest.raises(BudgetExhausted):
            await Reproducer(workspace, exhausted_client).run(bug=bug, runtime_evidence=None)

    @pytest.mark.asyncio
    async def test_prior_failure_included_in_prompt(self, workspace, bob_client, bug):
        reply = {"file": "tests/test_culprit_repro.py", "code": "pass"}
        captured: list[str] = []

        async def capture_run(prompt, context, *, mode="ask"):
            captured.append(prompt)
            return reply

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await Reproducer(workspace, bob_client).run(
                bug=bug, runtime_evidence=None, prior_failure="previous test timed out"
            )

        assert "previous test timed out" in captured[0]


# ---------------------------------------------------------------------------
# CauseTracer
# ---------------------------------------------------------------------------

class TestCauseTracer:
    @pytest.mark.asyncio
    async def test_returns_root_cause(self, workspace, bob_client, bug):
        reply = {
            "codebase": "myapp",
            "file": "pricing.py",
            "line": 42,
            "symbol": "apply_discount",
            "explanation": "Discount before tax",
            "confidence": 0.88,
        }
        with _mock_run_agent(bob_client, reply):
            result = await CauseTracer(workspace, bob_client).run(bug=bug, runtime_evidence=None)

        assert isinstance(result, RootCause)
        assert result.codebase == "myapp"
        assert result.file == "pricing.py"
        assert result.line == 42
        assert result.symbol == "apply_discount"
        assert result.confidence == pytest.approx(0.88)

    @pytest.mark.asyncio
    async def test_prompt_contains_all_codebase_names(self, workspace, bob_client, bug):
        reply = {
            "codebase": "myapp", "file": "x.py", "line": 1,
            "symbol": "f", "explanation": "e", "confidence": 0.9,
        }
        captured: list[str] = []

        async def capture_run(prompt, context, *, mode="ask"):
            captured.append(prompt)
            return reply

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await CauseTracer(workspace, bob_client).run(bug=bug, runtime_evidence=None)

        assert "myapp" in captured[0]

    @pytest.mark.asyncio
    async def test_raises_subagent_error_on_list_reply(self, workspace, bob_client, bug):
        with _mock_run_agent(bob_client, []):
            with pytest.raises(SubagentError, match="expected dict"):
                await CauseTracer(workspace, bob_client).run(bug=bug, runtime_evidence=None)

    @pytest.mark.asyncio
    async def test_raises_subagent_error_on_missing_key(self, workspace, bob_client, bug):
        with _mock_run_agent(bob_client, {"codebase": "myapp"}):  # missing many keys
            with pytest.raises(SubagentError, match="invalid reply"):
                await CauseTracer(workspace, bob_client).run(bug=bug, runtime_evidence=None)

    @pytest.mark.asyncio
    async def test_raises_subagent_error_on_bad_confidence_type(self, workspace, bob_client, bug):
        reply = {
            "codebase": "myapp", "file": "x.py", "line": 1,
            "symbol": "f", "explanation": "e", "confidence": "not-a-float",
        }
        with _mock_run_agent(bob_client, reply):
            with pytest.raises(SubagentError):
                await CauseTracer(workspace, bob_client).run(bug=bug, runtime_evidence=None)

    @pytest.mark.asyncio
    async def test_raises_budget_exhausted(self, workspace, bug):
        exhausted_client = BobClient(workspace_root=Path("/x"), max_bobcoins=0.0)
        with pytest.raises(BudgetExhausted):
            await CauseTracer(workspace, exhausted_client).run(bug=bug, runtime_evidence=None)

    @pytest.mark.asyncio
    async def test_runtime_evidence_included_in_prompt(self, workspace, bob_client, bug, runtime_evidence):
        reply = {
            "codebase": "myapp", "file": "x.py", "line": 1,
            "symbol": "f", "explanation": "e", "confidence": 0.9,
        }
        captured: list[str] = []

        async def capture_run(prompt, context, *, mode="ask"):
            captured.append(prompt)
            return reply

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await CauseTracer(workspace, bob_client).run(bug=bug, runtime_evidence=runtime_evidence)

        assert "99.99" in captured[0]


# ---------------------------------------------------------------------------
# FixAuthor
# ---------------------------------------------------------------------------

class TestFixAuthor:
    def _valid_fix_reply(self):
        return [
            {
                "target_codebase": "myapp",
                "edits": [
                    {
                        "file": "pricing.py",
                        "old_text": "total = price - discount",
                        "new_text": "total = price * tax_rate - discount",
                    }
                ],
            }
        ]

    @pytest.mark.asyncio
    async def test_returns_list_of_fixes(self, workspace, bob_client, root_cause):
        with _mock_run_agent(bob_client, self._valid_fix_reply()):
            result = await FixAuthor(workspace, bob_client).run(root_cause=root_cause)

        assert isinstance(result, list)
        assert len(result) == 1
        fix = result[0]
        assert isinstance(fix, Fix)
        assert fix.target_codebase == "myapp"
        assert [e.file for e in fix.edits] == ["pricing.py"]

    @pytest.mark.asyncio
    async def test_caps_at_three_candidates(self, workspace, bob_client, root_cause):
        four_fixes = self._valid_fix_reply() * 4
        with _mock_run_agent(bob_client, four_fixes):
            result = await FixAuthor(workspace, bob_client).run(root_cause=root_cause)
        assert len(result) <= 3

    @pytest.mark.asyncio
    async def test_edits_keep_exact_old_and_new_text(self, workspace, bob_client, root_cause):
        with _mock_run_agent(bob_client, self._valid_fix_reply()):
            result = await FixAuthor(workspace, bob_client).run(root_cause=root_cause)
        edit = result[0].edits[0]
        assert edit.old_text == "total = price - discount"
        assert edit.new_text == "total = price * tax_rate - discount"

    @pytest.mark.asyncio
    async def test_raises_subagent_error_on_dict_reply(self, workspace, bob_client, root_cause):
        with _mock_run_agent(bob_client, {"oops": "not a list"}):
            with pytest.raises(SubagentError, match="expected list"):
                await FixAuthor(workspace, bob_client).run(root_cause=root_cause)

    @pytest.mark.asyncio
    async def test_raises_subagent_error_on_empty_list(self, workspace, bob_client, root_cause):
        with _mock_run_agent(bob_client, []):
            with pytest.raises(SubagentError, match="no fix candidates"):
                await FixAuthor(workspace, bob_client).run(root_cause=root_cause)

    @pytest.mark.asyncio
    async def test_raises_subagent_error_on_missing_target_codebase(self, workspace, bob_client, root_cause):
        broken = [{"edits": [{"file": "x.py", "old_text": "a", "new_text": "b"}]}]  # missing target_codebase
        with _mock_run_agent(bob_client, broken):
            with pytest.raises(SubagentError):
                await FixAuthor(workspace, bob_client).run(root_cause=root_cause)

    @pytest.mark.asyncio
    async def test_raises_budget_exhausted(self, workspace, root_cause):
        exhausted_client = BobClient(workspace_root=Path("/x"), max_bobcoins=0.0)
        with pytest.raises(BudgetExhausted):
            await FixAuthor(workspace, exhausted_client).run(root_cause=root_cause)

    @pytest.mark.asyncio
    async def test_prompt_mentions_root_cause(self, workspace, bob_client, root_cause):
        captured: list[str] = []

        async def capture_run(prompt, context, *, mode="ask"):
            captured.append(prompt)
            return self._valid_fix_reply()

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await FixAuthor(workspace, bob_client).run(root_cause=root_cause)

        assert root_cause.symbol in captured[0]
        assert root_cause.explanation in captured[0]

    @pytest.mark.asyncio
    async def test_prompt_instructs_exact_old_text(self, workspace, bob_client, root_cause):
        """Prompt must tell Bob that old_text must be copied EXACTLY and be unique."""
        captured: list[str] = []

        async def capture_run(prompt, context, *, mode="ask"):
            captured.append(prompt)
            return self._valid_fix_reply()

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await FixAuthor(workspace, bob_client).run(root_cause=root_cause)

        assert "EXACTLY" in captured[0] or "exactly" in captured[0]
        assert "unique" in captured[0]


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

class TestGuard:
    def _valid_guard_reply(self):
        return {
            "codebase": "myapp",
            "file": "tests/test_culprit_regression.py",
            "test_name": "test_discount_applied_after_tax",
            "code": "def test_discount_applied_after_tax():\n    assert True",
        }

    @pytest.mark.asyncio
    async def test_returns_regression_test(self, workspace, bob_client, root_cause, fix):
        from culprit.domain.models import RegressionTest
        with _mock_run_agent(bob_client, self._valid_guard_reply()):
            result = await Guard(workspace, bob_client).run(root_cause=root_cause, fix=fix)
        assert isinstance(result, RegressionTest)
        assert result.codebase == "myapp"
        assert result.file == "tests/test_culprit_regression.py"
        assert result.test_name == "test_discount_applied_after_tax"
        assert "def test_" in result.code

    @pytest.mark.asyncio
    async def test_prompt_mentions_root_cause_and_fix(self, workspace, bob_client, root_cause, fix):
        captured: list[str] = []

        async def capture_run(prompt, context, *, mode="ask"):
            captured.append(prompt)
            return self._valid_guard_reply()

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await Guard(workspace, bob_client).run(root_cause=root_cause, fix=fix)

        assert root_cause.symbol in captured[0]
        assert fix.target_codebase in captured[0]

    @pytest.mark.asyncio
    async def test_raises_subagent_error_on_list_reply(self, workspace, bob_client, root_cause, fix):
        with _mock_run_agent(bob_client, []):
            with pytest.raises(SubagentError, match="expected dict"):
                await Guard(workspace, bob_client).run(root_cause=root_cause, fix=fix)

    @pytest.mark.asyncio
    async def test_raises_subagent_error_on_missing_key(self, workspace, bob_client, root_cause, fix):
        with _mock_run_agent(bob_client, {"codebase": "myapp"}):  # missing file, test_name, code
            with pytest.raises(SubagentError, match="invalid reply"):
                await Guard(workspace, bob_client).run(root_cause=root_cause, fix=fix)

    @pytest.mark.asyncio
    async def test_raises_budget_exhausted(self, workspace, root_cause, fix):
        exhausted_client = BobClient(workspace_root=Path("/x"), max_bobcoins=0.0)
        with pytest.raises(BudgetExhausted):
            await Guard(workspace, exhausted_client).run(root_cause=root_cause, fix=fix)

    @pytest.mark.asyncio
    async def test_prompt_instructs_not_to_modify_fix(self, workspace, bob_client, root_cause, fix):
        """Guard's prompt must explicitly say never to modify the fix code."""
        captured: list[str] = []

        async def capture_run(prompt, context, *, mode="ask"):
            captured.append(prompt)
            return self._valid_guard_reply()

        with patch.object(bob_client, "run_agent", side_effect=capture_run):
            await Guard(workspace, bob_client).run(root_cause=root_cause, fix=fix)

        prompt = captured[0].lower()
        # Should mention NOT modifying fix code
        assert "not modify" in prompt or "never" in prompt or "do not modify" in prompt
