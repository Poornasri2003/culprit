"""
Tests for BobClient (Part C).

All subprocess calls are mocked — no real Bob Shell, no network, no cost.
Covers:
  - Fenced-JSON extraction from last_message
  - Cost accumulation across multiple calls
  - BudgetExhausted when remaining budget <= 0
  - BobShellError on non-zero exit code
  - BobShellError on status != "success"
  - One retry on malformed JSON, then SubagentError
  - Header redaction
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from culprit.domain.exceptions import BobShellError, BudgetExhausted, SubagentError
from culprit.infrastructure.bob_client import BobClient, _extract_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bob_stdout(
    status: str = "success",
    session_costs: float = 0.05,
    last_message: str = '{"result": "ok"}',
) -> bytes:
    """Return mock Bob Shell stdout bytes (single last line)."""
    envelope = {
        "type": "result",
        "status": status,
        "stats": {"session_costs": session_costs},
        "last_message": last_message,
    }
    return (json.dumps(envelope) + "\n").encode()


def _make_client(max_bobcoins: float = 8.0) -> BobClient:
    return BobClient(workspace_root=Path("/fake/workspace"), max_bobcoins=max_bobcoins)


def _patch_invoke(client: BobClient, stdout: bytes, returncode: int = 0):
    """Patch BobClient._invoke to return the given stdout and returncode."""
    async def fake_invoke(prompt, mode, max_cost):
        return stdout.decode("utf-8"), returncode
    return patch.object(client, "_invoke", side_effect=fake_invoke)


# ---------------------------------------------------------------------------
# _extract_json unit tests
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_object(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_plain_array(self):
        assert _extract_json('[1, 2, 3]') == [1, 2, 3]

    def test_object_after_prose(self):
        text = 'Here is the answer:\n{"key": "value"}'
        assert _extract_json(text) == {"key": "value"}

    def test_fenced_json_object(self):
        text = 'Some text\n```json\n{"x": 42}\n```\nmore text'
        assert _extract_json(text) == {"x": 42}

    def test_fenced_json_array(self):
        text = '```json\n[{"a": 1}]\n```'
        assert _extract_json(text) == [{"a": 1}]

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON"):
            _extract_json("no json here at all")

    def test_nested_object(self):
        obj = {"stats": {"session_costs": 0.123}, "last_message": "hello"}
        assert _extract_json(json.dumps(obj)) == obj


# ---------------------------------------------------------------------------
# BobClient.redact_headers
# ---------------------------------------------------------------------------

class TestRedactHeaders:
    def test_redacts_authorization(self):
        client = _make_client()
        result = client.redact_headers({"Authorization": "Bearer secret", "Content-Type": "application/json"})
        assert result["Authorization"] == "<redacted>"
        assert result["Content-Type"] == "application/json"

    def test_redacts_x_api_key_case_insensitive(self):
        client = _make_client()
        result = client.redact_headers({"X-API-Key": "my-key", "X-Custom": "ok"})
        assert result["X-API-Key"] == "<redacted>"
        assert result["X-Custom"] == "ok"

    def test_redacts_cookie(self):
        client = _make_client()
        result = client.redact_headers({"Cookie": "session=abc123"})
        assert result["Cookie"] == "<redacted>"

    def test_empty_headers(self):
        client = _make_client()
        assert client.redact_headers({}) == {}

    def test_does_not_mutate_original(self):
        client = _make_client()
        original = {"Authorization": "secret"}
        client.redact_headers(original)
        assert original["Authorization"] == "secret"


# ---------------------------------------------------------------------------
# BobClient.run_agent — happy path
# ---------------------------------------------------------------------------

class TestRunAgentSuccess:
    @pytest.mark.asyncio
    async def test_returns_parsed_json_object(self):
        client = _make_client()
        stdout = _make_bob_stdout(last_message='{"answer": 42}')
        with _patch_invoke(client, stdout):
            result = await client.run_agent("test prompt", {})
        assert result == {"answer": 42}

    @pytest.mark.asyncio
    async def test_returns_parsed_json_array(self):
        client = _make_client()
        stdout = _make_bob_stdout(last_message='[{"fix": "a"}, {"fix": "b"}]')
        with _patch_invoke(client, stdout):
            result = await client.run_agent("test prompt", {})
        assert result == [{"fix": "a"}, {"fix": "b"}]

    @pytest.mark.asyncio
    async def test_extracts_json_from_fenced_last_message(self):
        """last_message wraps answer in a markdown json code fence — must still extract."""
        fenced = '```json\n{"file": "tests/test_repro.py", "code": "def test(): pass"}\n```'
        client = _make_client()
        stdout = _make_bob_stdout(last_message=fenced)
        with _patch_invoke(client, stdout):
            result = await client.run_agent("test prompt", {})
        assert result == {"file": "tests/test_repro.py", "code": "def test(): pass"}

    @pytest.mark.asyncio
    async def test_accumulates_cost(self):
        client = _make_client()
        stdout = _make_bob_stdout(session_costs=0.10, last_message='{"x": 1}')
        with _patch_invoke(client, stdout):
            await client.run_agent("prompt 1", {})
        assert client.get_bobcoins_used() == pytest.approx(0.10)

    @pytest.mark.asyncio
    async def test_accumulates_cost_across_calls(self):
        client = _make_client()
        stdout1 = _make_bob_stdout(session_costs=0.10, last_message='{"x": 1}')
        stdout2 = _make_bob_stdout(session_costs=0.25, last_message='{"x": 2}')

        with _patch_invoke(client, stdout1):
            await client.run_agent("prompt 1", {})
        assert client.get_bobcoins_used() == pytest.approx(0.10)

        with _patch_invoke(client, stdout2):
            await client.run_agent("prompt 2", {})
        assert client.get_bobcoins_used() == pytest.approx(0.35)

    @pytest.mark.asyncio
    async def test_max_cost_arg_decreases_with_usage(self):
        """_invoke is called with remaining = max_bobcoins - bobcoins_used."""
        client = _make_client(max_bobcoins=8.0)
        captured: list[float] = []

        async def fake_invoke(prompt, mode, max_cost):
            captured.append(max_cost)
            return _make_bob_stdout(session_costs=2.0, last_message='{"x": 1}').decode(), 0

        with patch.object(client, "_invoke", side_effect=fake_invoke):
            await client.run_agent("p1", {})
            await client.run_agent("p2", {})

        assert captured[0] == pytest.approx(8.0)
        assert captured[1] == pytest.approx(6.0)

    @pytest.mark.asyncio
    async def test_context_headers_redacted_in_prompt(self):
        """Headers named in REDACTED_HEADER_NAMES must not appear as plain values."""
        client = _make_client()
        captured_prompts: list[str] = []

        async def fake_invoke(prompt, mode, max_cost):
            captured_prompts.append(prompt)
            return _make_bob_stdout(last_message='{"ok": true}').decode(), 0

        context = {"headers": {"Authorization": "Bearer supersecret", "Content-Type": "application/json"}}
        with patch.object(client, "_invoke", side_effect=fake_invoke):
            await client.run_agent("test", context)

        assert "supersecret" not in captured_prompts[0]
        assert "<redacted>" in captured_prompts[0]


# ---------------------------------------------------------------------------
# BobClient.run_agent — error paths
# ---------------------------------------------------------------------------

class TestRunAgentErrors:
    @pytest.mark.asyncio
    async def test_budget_exhausted_before_call(self):
        """Raises BudgetExhausted without calling subprocess when budget is already spent."""
        client = _make_client(max_bobcoins=1.0)
        # Pre-exhaust the budget by simulating a previous call
        stdout = _make_bob_stdout(session_costs=1.0, last_message='{"x": 1}')
        with _patch_invoke(client, stdout):
            await client.run_agent("first", {})

        # Second call should raise immediately
        with pytest.raises(BudgetExhausted):
            await client.run_agent("second", {})

    @pytest.mark.asyncio
    async def test_budget_exhausted_on_zero_budget(self):
        """Raises BudgetExhausted when max_bobcoins is 0."""
        client = _make_client(max_bobcoins=0.0)
        with pytest.raises(BudgetExhausted):
            await client.run_agent("any", {})

    @pytest.mark.asyncio
    async def test_bob_shell_error_on_nonzero_exit(self):
        client = _make_client()
        stdout = _make_bob_stdout()  # content irrelevant — exit code wins
        with _patch_invoke(client, stdout, returncode=1):
            with pytest.raises(BobShellError, match="exited with code 1"):
                await client.run_agent("test", {})

    @pytest.mark.asyncio
    async def test_bob_shell_error_on_status_not_success(self):
        client = _make_client()
        stdout = _make_bob_stdout(status="error")
        with _patch_invoke(client, stdout):
            with pytest.raises(BobShellError, match="status 'error'"):
                await client.run_agent("test", {})

    @pytest.mark.asyncio
    async def test_retry_once_on_malformed_envelope_json(self):
        """If the last line is not JSON, retry once then raise SubagentError."""
        client = _make_client()
        call_count = 0

        async def fake_invoke(prompt, mode, max_cost):
            nonlocal call_count
            call_count += 1
            return "not json at all\n", 0

        with patch.object(client, "_invoke", side_effect=fake_invoke):
            with pytest.raises(SubagentError):
                await client.run_agent("test", {})

        assert call_count == 2  # tried twice

    @pytest.mark.asyncio
    async def test_retry_once_on_no_json_in_last_message(self):
        """If last_message contains no JSON, retry once then raise SubagentError."""
        client = _make_client()
        call_count = 0

        async def fake_invoke(prompt, mode, max_cost):
            nonlocal call_count
            call_count += 1
            return _make_bob_stdout(last_message="no JSON here, just prose").decode(), 0

        with patch.object(client, "_invoke", side_effect=fake_invoke):
            with pytest.raises(SubagentError):
                await client.run_agent("test", {})

        assert call_count == 2  # tried twice

    @pytest.mark.asyncio
    async def test_succeeds_on_second_attempt_after_bad_first(self):
        """If first attempt has malformed last_message, second attempt succeeds."""
        client = _make_client()
        call_count = 0

        async def fake_invoke(prompt, mode, max_cost):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_bob_stdout(last_message="just prose, no json").decode(), 0
            return _make_bob_stdout(last_message='{"recovered": true}').decode(), 0

        with patch.object(client, "_invoke", side_effect=fake_invoke):
            result = await client.run_agent("test", {})

        assert result == {"recovered": True}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_bob_shell_error_message_never_has_env_values(self):
        """Error messages must not include environment values (§14 requirement)."""
        client = _make_client()

        async def fake_invoke(prompt, mode, max_cost):
            return "", 2

        with patch.object(client, "_invoke", side_effect=fake_invoke):
            with pytest.raises(BobShellError) as exc_info:
                await client.run_agent("test", {})
        # The message should be generic — no env values interpolated
        msg = str(exc_info.value)
        assert "BOB_API_KEY" not in msg
        assert "APPDATA" not in msg


# ---------------------------------------------------------------------------
# BobClient._build_command
# ---------------------------------------------------------------------------

class TestBuildCommand:
    def test_contains_required_args(self):
        client = _make_client()
        with patch("culprit.infrastructure.bob_client._bob_argv", return_value=["bob"]):
            cmd = client._build_command("my prompt", "ask", 5.0)
        assert "run" in cmd
        assert "--format" in cmd
        assert "json" in cmd
        assert "--mode" in cmd
        assert "ask" in cmd
        assert "--max-cost" in cmd
        assert "5.0" in cmd
        assert "--max-turns" in cmd
        assert "8" in cmd
        assert "--accept-license" in cmd
        assert "--trust" in cmd
        assert "-w" in cmd
        assert str(client._workspace_root) in cmd
        assert "my prompt" in cmd

    def test_no_shell_true(self):
        """_build_command must return a list (never a string that needs shell=True)."""
        client = _make_client()
        with patch("culprit.infrastructure.bob_client._bob_argv", return_value=["bob"]):
            cmd = client._build_command("prompt", "ask", 3.0)
        assert isinstance(cmd, list)
        for item in cmd:
            assert isinstance(item, str)
