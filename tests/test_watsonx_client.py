"""
tests/test_watsonx_client.py — pytest tests for WatsonxClient.

Uses httpx mock transports (no network, no real watsonx.ai, no real IAM).
Covers:
  - APPROVE verdict (total_score >= threshold)
  - REJECT verdict (total_score < threshold)
  - Malformed model output on first call, good response on retry -> Adjudication
  - Malformed model output on both attempts -> WatsonxError
  - is_configured() returns False when required env vars are absent
  - API key never appears in a WatsonxError message
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from culprit.domain.exceptions import WatsonxError
from culprit.domain.models import (
    AdjudicationVerdict,
    FileEdit,
    Fix,
    RootCause,
)
from culprit.infrastructure.watsonx_client import WatsonxClient

# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

_ROOT_CAUSE = RootCause(
    codebase="backend",
    file="app.py",
    line=42,
    symbol="calculate_total",
    explanation="Discount applied before tax instead of after.",
    confidence=0.9,
)

_FIX = Fix(
    target_codebase="backend",
    edits=[FileEdit(file="app.py", old_text="pre_tax_discount()", new_text="post_tax_discount()")],
    unified_diff="--- a/app.py\n+++ b/app.py\n@@ -42 +42 @@\n-pre_tax_discount()\n+post_tax_discount()\n",
    applied_files=["app.py"],
)

_SOURCE_CONTEXT = "def calculate_total(price, discount):\n    return price - discount"

_GOOD_API_KEY = "super-secret-ibm-key"
_GOOD_PROJECT_ID = "proj-1234"


def _iam_response(token: str = "fake-iam-token", expires_in: int = 3600) -> dict[str, Any]:
    return {"access_token": token, "expires_in": expires_in, "token_type": "Bearer"}


def _chat_response(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}}
        ]
    }


def _model_json(
    correctness: float = 0.9,
    safety: float = 0.8,
    minimalism: float = 0.85,
    reasoning: str = "Looks good.",
) -> str:
    return json.dumps(
        {
            "correctness_score": correctness,
            "safety_score": safety,
            "minimalism_score": minimalism,
            "reasoning": reasoning,
        }
    )


class _SequentialTransport(httpx.AsyncBaseTransport):
    """Returns pre-programmed responses in order; repeats the last one."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self._index = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        # Attach the request so httpx internals are happy
        resp.request = request
        return resp


def _make_response(status: int, body: Any) -> httpx.Response:
    content = json.dumps(body).encode()
    return httpx.Response(status, content=content, headers={"content-type": "application/json"})


def _configure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IBM_CLOUD_API_KEY", _GOOD_API_KEY)
    monkeypatch.setenv("WATSONX_PROJECT_ID", _GOOD_PROJECT_ID)
    monkeypatch.setenv("WATSONX_MODEL_ID", "ibm/granite-3-8b-instruct")
    monkeypatch.setenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")


# ─────────────────────────────────────────────────────────────────────────────
# Helper: patch httpx.AsyncClient to use a sequential transport
# ─────────────────────────────────────────────────────────────────────────────

def _patch_async_client(responses: list[httpx.Response]):
    """
    Context manager that patches httpx.AsyncClient so every instance created
    inside watsonx_client uses our _SequentialTransport.
    """
    transport = _SequentialTransport(responses)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self_inner, *args, **kwargs):
        kwargs.pop("transport", None)
        original_init(self_inner, *args, transport=transport, **kwargs)

    return patch.object(httpx.AsyncClient, "__init__", patched_init)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: APPROVE verdict
# ─────────────────────────────────────────────────────────────────────────────


class TestAdjudicateApprove:
    @pytest.mark.asyncio
    async def test_approve_verdict_when_scores_above_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response(_model_json(0.9, 0.85, 0.8))),
        ]

        with _patch_async_client(responses):
            result = await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert result.verdict == AdjudicationVerdict.APPROVE
        assert result.total_score >= 0.65

    @pytest.mark.asyncio
    async def test_approve_populates_all_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        reasoning = "The fix correctly addresses the root cause."
        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response(_model_json(0.9, 0.9, 0.9, reasoning))),
        ]

        with _patch_async_client(responses):
            result = await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert result.correctness_score == pytest.approx(0.9)
        assert result.safety_score == pytest.approx(0.9)
        assert result.minimalism_score == pytest.approx(0.9)
        assert result.reasoning == reasoning
        assert result.total_score == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_total_score_is_arithmetic_mean(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response(_model_json(0.6, 0.8, 1.0))),
        ]

        with _patch_async_client(responses):
            result = await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        expected_total = (0.6 + 0.8 + 1.0) / 3
        assert result.total_score == pytest.approx(expected_total)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: REJECT verdict
# ─────────────────────────────────────────────────────────────────────────────


class TestAdjudicateReject:
    @pytest.mark.asyncio
    async def test_reject_verdict_when_scores_below_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        # total = (0.3 + 0.4 + 0.5) / 3 ≈ 0.4  < ADJUDICATION_THRESHOLD (0.65)
        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response(_model_json(0.3, 0.4, 0.5))),
        ]

        with _patch_async_client(responses):
            result = await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert result.verdict == AdjudicationVerdict.REJECT
        assert result.total_score < 0.65

    @pytest.mark.asyncio
    async def test_reject_at_exactly_below_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_score of 0.64 should produce REJECT."""
        _configure_env(monkeypatch)
        client = WatsonxClient()

        # mean(0.64, 0.64, 0.64) == 0.64
        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response(_model_json(0.64, 0.64, 0.64))),
        ]

        with _patch_async_client(responses):
            result = await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert result.verdict == AdjudicationVerdict.REJECT

    @pytest.mark.asyncio
    async def test_approve_at_exactly_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_score of exactly 0.65 should produce APPROVE."""
        _configure_env(monkeypatch)
        client = WatsonxClient()

        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response(_model_json(0.65, 0.65, 0.65))),
        ]

        with _patch_async_client(responses):
            result = await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert result.verdict == AdjudicationVerdict.APPROVE


# ─────────────────────────────────────────────────────────────────────────────
# Tests: malformed output → retry → success
# ─────────────────────────────────────────────────────────────────────────────


class TestMalformedOutputRetry:
    @pytest.mark.asyncio
    async def test_malformed_first_then_good_returns_adjudication(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First call returns garbage, second returns valid JSON — adjudication succeeds."""
        _configure_env(monkeypatch)
        client = WatsonxClient()

        # IAM token (acquired once; cached for the retry)
        # chat call 1: malformed model text
        # chat call 2 (retry): valid JSON
        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response("NOT JSON AT ALL")),
            _make_response(200, _chat_response(_model_json(0.8, 0.8, 0.8))),
        ]

        with _patch_async_client(responses):
            result = await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert result.verdict == AdjudicationVerdict.APPROVE

    @pytest.mark.asyncio
    async def test_malformed_then_good_sets_correct_scores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response("oops not json")),
            _make_response(200, _chat_response(_model_json(0.7, 0.75, 0.8, "OK on retry."))),
        ]

        with _patch_async_client(responses):
            result = await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert result.correctness_score == pytest.approx(0.7)
        assert result.reasoning == "OK on retry."


# ─────────────────────────────────────────────────────────────────────────────
# Tests: malformed output on both attempts → WatsonxError
# ─────────────────────────────────────────────────────────────────────────────


class TestMalformedTwiceRaisesWatsonxError:
    @pytest.mark.asyncio
    async def test_raises_watsonx_error_after_two_bad_outputs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response("garbage #1")),
            _make_response(200, _chat_response("garbage #2")),
        ]

        with _patch_async_client(responses):
            with pytest.raises(WatsonxError):
                await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

    @pytest.mark.asyncio
    async def test_watsonx_error_message_does_not_contain_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response("garbage")),
            _make_response(200, _chat_response("garbage")),
        ]

        with _patch_async_client(responses):
            with pytest.raises(WatsonxError) as exc_info:
                await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert _GOOD_API_KEY not in str(exc_info.value)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: is_configured()
# ─────────────────────────────────────────────────────────────────────────────


class TestIsConfigured:
    def test_returns_false_when_no_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("IBM_CLOUD_API_KEY", raising=False)
        monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)
        client = WatsonxClient()
        assert client.is_configured() is False

    def test_returns_false_when_only_api_key_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IBM_CLOUD_API_KEY", _GOOD_API_KEY)
        monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)
        client = WatsonxClient()
        assert client.is_configured() is False

    def test_returns_false_when_only_project_id_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("IBM_CLOUD_API_KEY", raising=False)
        monkeypatch.setenv("WATSONX_PROJECT_ID", _GOOD_PROJECT_ID)
        client = WatsonxClient()
        assert client.is_configured() is False

    def test_returns_true_when_both_vars_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IBM_CLOUD_API_KEY", _GOOD_API_KEY)
        monkeypatch.setenv("WATSONX_PROJECT_ID", _GOOD_PROJECT_ID)
        client = WatsonxClient()
        assert client.is_configured() is True


# ─────────────────────────────────────────────────────────────────────────────
# Tests: API key must never appear in any error message
# ─────────────────────────────────────────────────────────────────────────────


class TestApiKeyNeverLeaked:
    @pytest.mark.asyncio
    async def test_iam_401_error_does_not_contain_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        responses = [
            _make_response(401, {"error": "unauthorized"}),
        ]

        with _patch_async_client(responses):
            with pytest.raises(WatsonxError) as exc_info:
                await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert _GOOD_API_KEY not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_iam_500_error_does_not_contain_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        responses = [
            _make_response(500, {"error": "internal"}),
        ]

        with _patch_async_client(responses):
            with pytest.raises(WatsonxError) as exc_info:
                await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert _GOOD_API_KEY not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_chat_401_error_does_not_contain_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        responses = [
            _make_response(200, _iam_response("secret-bearer-token")),
            _make_response(401, {"error": "unauthorized"}),
        ]

        with _patch_async_client(responses):
            with pytest.raises(WatsonxError) as exc_info:
                await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert "secret-bearer-token" not in str(exc_info.value)
        assert _GOOD_API_KEY not in str(exc_info.value)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: score clamping
# ─────────────────────────────────────────────────────────────────────────────


class TestScoreClamping:
    @pytest.mark.asyncio
    async def test_scores_above_one_are_clamped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure_env(monkeypatch)
        client = WatsonxClient()

        raw_json = json.dumps(
            {
                "correctness_score": 1.5,
                "safety_score": 2.0,
                "minimalism_score": -0.1,
                "reasoning": "Out-of-range scores.",
            }
        )
        responses = [
            _make_response(200, _iam_response()),
            _make_response(200, _chat_response(raw_json)),
        ]

        with _patch_async_client(responses):
            result = await client.adjudicate(_ROOT_CAUSE, _FIX, _SOURCE_CONTEXT)

        assert result.correctness_score == pytest.approx(1.0)
        assert result.safety_score == pytest.approx(1.0)
        assert result.minimalism_score == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: redaction
# ─────────────────────────────────────────────────────────────────────────────


class TestRedaction:
    def test_authorization_header_redacted(self) -> None:
        client = WatsonxClient()
        ctx = "Authorization: Bearer mysecrettoken\nsome other line"
        redacted = client._redact(ctx)
        assert "mysecrettoken" not in redacted
        assert "<redacted>" in redacted

    def test_x_api_key_header_redacted(self) -> None:
        client = WatsonxClient()
        ctx = "X-API-Key: myapikey123\nsome other line"
        redacted = client._redact(ctx)
        assert "myapikey123" not in redacted
        assert "<redacted>" in redacted

    def test_cookie_header_redacted(self) -> None:
        client = WatsonxClient()
        ctx = "Cookie: session=abc123\nsome other line"
        redacted = client._redact(ctx)
        assert "abc123" not in redacted
        assert "<redacted>" in redacted

    def test_non_sensitive_content_not_redacted(self) -> None:
        client = WatsonxClient()
        ctx = "Content-Type: application/json\nsome other line"
        redacted = client._redact(ctx)
        assert "application/json" in redacted
