"""
tests/test_infrastructure.py — pytest tests for the infrastructure layer.

Covers:
  - GitOps.apply_fix / revert_fix (happy path)
  - GitOps: old_text not found (SubagentError, no file modified)
  - TestRunner.has_tests (true / false cases)
  - AuthBuilder: missing BEARER_TOKEN raises AuthError
  - HttpClient: sensitive headers are redacted in returned RuntimeEvidence

No network calls and no real Bob are required.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from culprit.domain.exceptions import AuthError, SubagentError, UnreachableError
from culprit.domain.models import (
    AuthContext,
    AuthScheme,
    Endpoint,
    FileEdit,
    Fix,
    SourceCodebase,
)
from culprit.infrastructure.auth import AuthBuilder
from culprit.infrastructure.git_ops import GitOps
from culprit.infrastructure.http_client import HttpClient
from culprit.infrastructure.test_runner import TestRunner


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_codebase(root: Path, name: str = "test_cb") -> SourceCodebase:
    return SourceCodebase(
        name=name,
        root_path=root,
        language="python",
        entry_points=[],
    )


def _make_fix(file_rel: str, old_text: str, new_text: str) -> Fix:
    return Fix(
        target_codebase="test_cb",
        edits=[FileEdit(file=file_rel, old_text=old_text, new_text=new_text)],
    )


# ─────────────────────────────────────────────────────────────────────────────
# GitOps — apply_fix / revert_fix
# ─────────────────────────────────────────────────────────────────────────────


class TestGitOpsApplyRevert:
    def test_apply_fix_replaces_text(self, tmp_path: Path) -> None:
        cb = _make_codebase(tmp_path)
        target = tmp_path / "module.py"
        target.write_text("def foo():\n    return 1\n")

        fix = _make_fix("module.py", "    return 1\n", "    return 42\n")
        GitOps().apply_fix(cb, fix)

        assert target.read_text() == "def foo():\n    return 42\n"

    def test_apply_fix_populates_unified_diff(self, tmp_path: Path) -> None:
        cb = _make_codebase(tmp_path)
        target = tmp_path / "module.py"
        target.write_text("x = 1\n")

        fix = _make_fix("module.py", "x = 1\n", "x = 99\n")
        GitOps().apply_fix(cb, fix)

        assert "x = 1" in fix.unified_diff
        assert "x = 99" in fix.unified_diff

    def test_apply_fix_populates_applied_files(self, tmp_path: Path) -> None:
        cb = _make_codebase(tmp_path)
        (tmp_path / "a.py").write_text("OLD\n")

        fix = _make_fix("a.py", "OLD\n", "NEW\n")
        GitOps().apply_fix(cb, fix)

        assert fix.applied_files == ["a.py"]

    def test_revert_fix_restores_original(self, tmp_path: Path) -> None:
        cb = _make_codebase(tmp_path)
        target = tmp_path / "module.py"
        original_text = "def foo():\n    return 1\n"
        target.write_text(original_text)

        fix = _make_fix("module.py", "    return 1\n", "    return 42\n")
        ops = GitOps()
        ops.apply_fix(cb, fix)
        assert target.read_text() != original_text  # sanity

        ops.revert_fix(cb, fix)
        assert target.read_text() == original_text

    def test_revert_removes_backup_directory(self, tmp_path: Path) -> None:
        cb = _make_codebase(tmp_path)
        (tmp_path / "a.py").write_text("A\n")

        fix = _make_fix("a.py", "A\n", "B\n")
        ops = GitOps()
        ops.apply_fix(cb, fix)
        ops.revert_fix(cb, fix)

        assert not (tmp_path / ".culprit_backup").exists()


# ─────────────────────────────────────────────────────────────────────────────
# GitOps — old_text not found raises SubagentError; no file is modified
# ─────────────────────────────────────────────────────────────────────────────


class TestGitOpsOldTextNotFound:
    def test_raises_subagent_error_when_old_text_missing(self, tmp_path: Path) -> None:
        cb = _make_codebase(tmp_path)
        target = tmp_path / "module.py"
        original = "def bar(): pass\n"
        target.write_text(original)

        fix = _make_fix("module.py", "THIS_DOES_NOT_EXIST", "replacement")

        with pytest.raises(SubagentError):
            GitOps().apply_fix(cb, fix)

    def test_no_file_modified_when_old_text_missing(self, tmp_path: Path) -> None:
        cb = _make_codebase(tmp_path)
        target = tmp_path / "module.py"
        original = "def bar(): pass\n"
        target.write_text(original)

        fix = _make_fix("module.py", "THIS_DOES_NOT_EXIST", "replacement")

        try:
            GitOps().apply_fix(cb, fix)
        except SubagentError:
            pass

        # File must be untouched
        assert target.read_text() == original

    def test_raises_subagent_error_when_old_text_appears_multiple_times(
        self, tmp_path: Path
    ) -> None:
        cb = _make_codebase(tmp_path)
        target = tmp_path / "module.py"
        target.write_text("x = 1\nx = 1\n")

        fix = _make_fix("module.py", "x = 1\n", "x = 2\n")

        with pytest.raises(SubagentError):
            GitOps().apply_fix(cb, fix)


# ─────────────────────────────────────────────────────────────────────────────
# TestRunner.has_tests
# ─────────────────────────────────────────────────────────────────────────────


class TestHasTests:
    def test_returns_true_when_test_file_present(self, tmp_path: Path) -> None:
        (tmp_path / "test_something.py").write_text("def test_x(): pass\n")
        cb = _make_codebase(tmp_path)
        assert TestRunner().has_tests(cb) is True

    def test_returns_false_when_no_test_files(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n")
        cb = _make_codebase(tmp_path)
        assert TestRunner().has_tests(cb) is False

    def test_returns_false_for_empty_directory(self, tmp_path: Path) -> None:
        cb = _make_codebase(tmp_path)
        assert TestRunner().has_tests(cb) is False

    def test_finds_test_file_in_subdirectory(self, tmp_path: Path) -> None:
        sub = tmp_path / "tests"
        sub.mkdir()
        (sub / "test_core.py").write_text("def test_y(): pass\n")
        cb = _make_codebase(tmp_path)
        assert TestRunner().has_tests(cb) is True


# ─────────────────────────────────────────────────────────────────────────────
# AuthBuilder — missing BEARER_TOKEN raises AuthError
# ─────────────────────────────────────────────────────────────────────────────


class TestAuthBuilder:
    def test_bearer_raises_auth_error_when_token_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BEARER_TOKEN", raising=False)
        with pytest.raises(AuthError):
            AuthBuilder().build(AuthScheme.BEARER)

    def test_bearer_returns_auth_context_when_token_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BEARER_TOKEN", "secret-token-value")
        ctx = AuthBuilder().build(AuthScheme.BEARER)
        assert ctx.scheme == AuthScheme.BEARER
        # Token must be present in the object (it's just excluded from serialisation)
        assert ctx.token == "secret-token-value"

    def test_none_scheme_returns_auth_context_with_no_token(self) -> None:
        ctx = AuthBuilder().build(AuthScheme.NONE)
        assert ctx.scheme == AuthScheme.NONE
        assert ctx.token is None

    def test_basic_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            AuthBuilder().build(AuthScheme.BASIC)

    def test_oauth2_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            AuthBuilder().build(AuthScheme.OAUTH2)

    def test_apikey_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            AuthBuilder().build(AuthScheme.APIKEY)


# ─────────────────────────────────────────────────────────────────────────────
# HttpClient — header redaction in returned RuntimeEvidence
# ─────────────────────────────────────────────────────────────────────────────


class TestHttpClientHeaderRedaction:
    """Verify that sensitive headers are redacted in RuntimeEvidence (§10)."""

    @pytest.mark.asyncio
    async def test_authorization_header_redacted(self) -> None:
        """Authorization header sent on the request must appear as '<redacted>'."""
        auth_ctx = AuthContext(scheme=AuthScheme.BEARER, token="super-secret")
        endpoint = Endpoint(
            url="http://example.com/api",
            method="GET",
            headers={},
            expected_status=200,
        )

        # Build a fake httpx response whose request carries the Auth header
        fake_request = httpx.Request(
            "GET",
            "http://example.com/api",
            headers={"Authorization": "Bearer super-secret"},
        )
        fake_response = httpx.Response(
            200,
            text='{"ok": true}',
            request=fake_request,
        )

        client = HttpClient(auth_ctx)
        with patch.object(
            client._client, "request", new=AsyncMock(return_value=fake_response)
        ):
            evidence = await client.probe(endpoint)

        # httpx normalises header names to lowercase; accept either casing
        auth_val = (
            evidence.endpoint.headers.get("authorization")
            or evidence.endpoint.headers.get("Authorization")
        )
        assert auth_val == "<redacted>"
        assert "super-secret" not in str(evidence.endpoint.headers)

    @pytest.mark.asyncio
    async def test_x_api_key_header_redacted(self) -> None:
        auth_ctx = AuthContext(scheme=AuthScheme.NONE)
        endpoint = Endpoint(
            url="http://example.com/api",
            method="GET",
            headers={},
            expected_status=200,
        )

        fake_request = httpx.Request(
            "GET",
            "http://example.com/api",
            headers={"X-API-Key": "my-key-12345"},
        )
        fake_response = httpx.Response(200, text="ok", request=fake_request)

        client = HttpClient(auth_ctx)
        with patch.object(
            client._client, "request", new=AsyncMock(return_value=fake_response)
        ):
            evidence = await client.probe(endpoint)

        assert evidence.endpoint.headers.get("x-api-key") == "<redacted>"

    @pytest.mark.asyncio
    async def test_cookie_header_redacted(self) -> None:
        auth_ctx = AuthContext(scheme=AuthScheme.NONE)
        endpoint = Endpoint(
            url="http://example.com/",
            method="GET",
            headers={},
            expected_status=200,
        )

        fake_request = httpx.Request(
            "GET",
            "http://example.com/",
            headers={"Cookie": "session=abc123"},
        )
        fake_response = httpx.Response(200, text="ok", request=fake_request)

        client = HttpClient(auth_ctx)
        with patch.object(
            client._client, "request", new=AsyncMock(return_value=fake_response)
        ):
            evidence = await client.probe(endpoint)

        assert evidence.endpoint.headers.get("cookie") == "<redacted>"

    @pytest.mark.asyncio
    async def test_non_sensitive_header_not_redacted(self) -> None:
        auth_ctx = AuthContext(scheme=AuthScheme.NONE)
        endpoint = Endpoint(
            url="http://example.com/",
            method="GET",
            headers={},
            expected_status=200,
        )

        fake_request = httpx.Request(
            "GET",
            "http://example.com/",
            headers={"Content-Type": "application/json"},
        )
        fake_response = httpx.Response(200, text="ok", request=fake_request)

        client = HttpClient(auth_ctx)
        with patch.object(
            client._client, "request", new=AsyncMock(return_value=fake_response)
        ):
            evidence = await client.probe(endpoint)

        content_type = evidence.endpoint.headers.get(
            "content-type"
        ) or evidence.endpoint.headers.get("Content-Type")
        assert content_type == "application/json"

    @pytest.mark.asyncio
    async def test_unreachable_error_on_timeout(self) -> None:
        auth_ctx = AuthContext(scheme=AuthScheme.NONE)
        endpoint = Endpoint(
            url="http://10.255.255.1/",
            method="GET",
            headers={},
            expected_status=200,
        )

        client = HttpClient(auth_ctx)
        with patch.object(
            client._client,
            "request",
            new=AsyncMock(side_effect=httpx.ConnectTimeout("timed out")),
        ):
            with pytest.raises(UnreachableError):
                await client.probe(endpoint)

    @pytest.mark.asyncio
    async def test_returns_evidence_for_non_200_status(self) -> None:
        """Any HTTP status (e.g. 500) must produce RuntimeEvidence, not raise."""
        auth_ctx = AuthContext(scheme=AuthScheme.NONE)
        endpoint = Endpoint(
            url="http://example.com/broken",
            method="GET",
            headers={},
            expected_status=200,
        )

        fake_request = httpx.Request("GET", "http://example.com/broken")
        fake_response = httpx.Response(500, text="Internal Server Error", request=fake_request)

        client = HttpClient(auth_ctx)
        with patch.object(
            client._client, "request", new=AsyncMock(return_value=fake_response)
        ):
            evidence = await client.probe(endpoint)

        assert evidence.response_status == 500
