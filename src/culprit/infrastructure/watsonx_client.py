"""
WatsonxClient — watsonx.ai Adjudicator adapter (§12).

Reads model ID and coordinates from environment variables (via config.py).
Sends (root_cause, fix, source_context) to the Granite model and parses
the three axis scores. Computes total_score and verdict deterministically.
Applies the same header redaction as bob_client (§10).
Never logs or exposes credentials.

Transport: plain REST via httpx.AsyncClient (NO ibm-watsonx-ai SDK — keeps
deps light and avoids Python-version compatibility risk, SPEC §12).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from typing import Any

import httpx

from culprit.config import (
    ADJUDICATION_THRESHOLD,
    IBM_CLOUD_API_KEY_ENV,
    REDACTED_HEADER_NAMES,
    WATSONX_MAX_RETRIES,
    WATSONX_MODEL_ID_ENV,
    WATSONX_PROJECT_ID_ENV,
    WATSONX_TIMEOUT_SECONDS,
    WATSONX_URL_ENV,
)
from culprit.domain.exceptions import WatsonxError
from culprit.domain.models import (
    Adjudication,
    AdjudicationVerdict,
    Fix,
    RootCause,
)

# ── Defaults (§12) ────────────────────────────────────────────────────────────

_DEFAULT_MODEL_ID: str = "ibm/granite-3-8b-instruct"
_DEFAULT_WATSONX_URL: str = "https://us-south.ml.cloud.ibm.com"

# IAM token endpoint (§12 — plain REST)
_IAM_TOKEN_URL: str = "https://iam.cloud.ibm.com/identity/token"

# watsonx.ai Chat API version
_CHAT_API_VERSION: str = "2024-10-08"

# Buffer: refresh the token this many seconds *before* it expires.
_TOKEN_EXPIRY_BUFFER_SECONDS: int = 60

# ── Internal sentinel exception ──────────────────────────────────────────────

class _MalformedResponseError(Exception):
    """Raised when the chat endpoint returns HTTP 200 but an unexpected body shape."""


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT: str = (
    "You are a strict code-review adjudicator. "
    "Evaluate the proposed fix on three axes: "
    "correctness (does it actually fix the root cause?), "
    "safety (does it introduce new risks?), "
    "and minimalism (is it the smallest change that works?). "
    "Reply with ONLY a JSON object — no markdown, no prose outside the JSON:\n"
    '{"correctness_score": <0.0-1.0>, '
    '"safety_score": <0.0-1.0>, '
    '"minimalism_score": <0.0-1.0>, '
    '"reasoning": "<one short paragraph>"}'
)


class WatsonxClient:
    """
    Adapter for the watsonx.ai Adjudicator (§12).

    Pattern: Adapter — hides IAM token acquisition, HTTP transport, and
    retry logic behind adjudicate().
    """

    def __init__(self) -> None:
        """
        Read WATSONX_URL, WATSONX_PROJECT_ID, WATSONX_MODEL_ID, IBM_CLOUD_API_KEY
        from environment. Never raises: missing vars just make is_configured()
        False so the orchestrator can fail open (§12). No network call here.
        """
        self._api_key: str | None = os.environ.get(IBM_CLOUD_API_KEY_ENV)
        self._project_id: str | None = os.environ.get(WATSONX_PROJECT_ID_ENV)
        self._model_id: str = os.environ.get(WATSONX_MODEL_ID_ENV, _DEFAULT_MODEL_ID)
        self._watsonx_url: str = os.environ.get(WATSONX_URL_ENV, _DEFAULT_WATSONX_URL).rstrip("/")

        # Cached IAM token state — populated lazily by _get_iam_token()
        self._iam_token: str | None = None
        self._token_expires_at: float = 0.0  # Unix timestamp

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """Return True only if all required watsonx env vars are present."""
        return bool(self._api_key and self._project_id)

    async def adjudicate(
        self,
        root_cause: RootCause,
        fix: Fix,
        source_context: str,
    ) -> Adjudication:
        """
        Call the Granite model; compute total_score and verdict; return Adjudication.

        source_context has already been redacted by the caller.
        Retries once on malformed model output, then raises WatsonxError (§12).
        Raises WatsonxError on timeout or HTTP/auth error — caller proceeds fail-open.
        """
        user_message = self._build_user_message(root_cause, fix, source_context)

        last_exc: Exception | None = None
        for attempt in range(WATSONX_MAX_RETRIES):
            try:
                raw_text = await self._call_chat_api(user_message)
            except _MalformedResponseError as exc:
                # Malformed/unexpected response body from a 200 OK chat endpoint —
                # treat the same as malformed model output and retry.
                last_exc = exc
                continue
            except WatsonxError:
                raise  # HTTP / auth / timeout errors propagate immediately

            try:
                return self._parse_model_output(raw_text)
            except WatsonxError as exc:
                last_exc = exc
                # Retry on malformed output (but not on HTTP errors)
                continue

        raise WatsonxError(
            f"Model returned malformed output after {WATSONX_MAX_RETRIES} attempt(s)"
        ) from last_exc

    # ──────────────────────────────────────────────────────────────────────────
    # Score / verdict helpers (§12 — deterministic, not AI)
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_total_score(
        self,
        correctness: float,
        safety: float,
        minimalism: float,
    ) -> float:
        """Return the arithmetic mean of the three axis scores (§12)."""
        return (correctness + safety + minimalism) / 3.0

    def _compute_verdict(self, total_score: float) -> AdjudicationVerdict:
        """Return APPROVE if total_score >= ADJUDICATION_THRESHOLD, else REJECT (§12)."""
        if total_score >= ADJUDICATION_THRESHOLD:
            return AdjudicationVerdict.APPROVE
        return AdjudicationVerdict.REJECT

    # ──────────────────────────────────────────────────────────────────────────
    # Redaction (§10)
    # ──────────────────────────────────────────────────────────────────────────

    def _redact(self, source_context: str) -> str:
        """Strip credential-bearing header values per REDACTED_HEADER_NAMES (§10)."""
        result = source_context
        for header_name in REDACTED_HEADER_NAMES:
            # Match header name (case-insensitive) followed by ': <value>'
            pattern = re.compile(
                r"(?i)(" + re.escape(header_name) + r"\s*:\s*)([^\r\n]+)",
            )
            result = pattern.sub(r"\1<redacted>", result)
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _build_user_message(
        self,
        root_cause: RootCause,
        fix: Fix,
        source_context: str,
    ) -> str:
        """Compose the user-turn content for the chat request."""
        redacted_ctx = self._redact(source_context)
        return (
            f"Root cause:\n{root_cause.explanation}\n\n"
            f"Proposed fix:\n{self._redact(self._describe_fix(fix))}\n\n"
            f"Source context:\n{redacted_ctx}"
        )

    @staticmethod
    def _describe_fix(fix: Fix) -> str:
        """Render the fix for review; adjudication runs BEFORE apply, so the
        diff is generated here from fix.edits rather than read from
        fix.unified_diff (which git_ops only fills after applying)."""
        if fix.unified_diff:
            return fix.unified_diff
        if not fix.edits:
            return "(no edits proposed)"
        parts = []
        for edit in fix.edits:
            diff = difflib.unified_diff(
                edit.old_text.splitlines(keepends=True),
                edit.new_text.splitlines(keepends=True),
                fromfile=f"a/{edit.file}",
                tofile=f"b/{edit.file}",
            )
            parts.append("".join(diff))
        return "\n".join(parts)

    async def _get_iam_token(self) -> str:
        """
        Return a valid IAM bearer token, refreshing it when near expiry.

        Never includes the API key in exception messages.
        Raises WatsonxError on failure.
        """
        now = time.time()
        if self._iam_token and now < self._token_expires_at - _TOKEN_EXPIRY_BUFFER_SECONDS:
            return self._iam_token

        try:
            async with httpx.AsyncClient(timeout=WATSONX_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    _IAM_TOKEN_URL,
                    data={
                        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                        "apikey": self._api_key,  # not logged
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.TimeoutException as exc:
            raise WatsonxError("IAM token request timed out") from exc
        except httpx.TransportError as exc:
            raise WatsonxError("IAM token request failed (transport error)") from exc

        if response.status_code != 200:
            # Do NOT include response body — may echo back the key
            raise WatsonxError(
                f"IAM token endpoint returned HTTP {response.status_code}"
            )

        try:
            data: dict[str, Any] = response.json()
            token: str = data["access_token"]
            expires_in: int = int(data.get("expires_in", 3600))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise WatsonxError("Could not parse IAM token response") from exc

        self._iam_token = token
        self._token_expires_at = now + expires_in
        return token

    async def _call_chat_api(self, user_message: str) -> str:
        """
        Call the watsonx.ai text/chat endpoint and return the model's raw reply text.

        Raises WatsonxError on timeout, HTTP error, or auth failure.
        Never includes the token in exception messages.
        """
        token = await self._get_iam_token()

        url = (
            f"{self._watsonx_url}/ml/v1/text/chat"
            f"?version={_CHAT_API_VERSION}"
        )
        payload: dict[str, Any] = {
            "model_id": self._model_id,
            "project_id": self._project_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "parameters": {
                "max_tokens": 400,
                "temperature": 0,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=WATSONX_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",  # not logged
                        "Content-Type": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise WatsonxError("watsonx.ai chat request timed out") from exc
        except httpx.TransportError as exc:
            raise WatsonxError("watsonx.ai chat request failed (transport error)") from exc

        if response.status_code == 401:
            raise WatsonxError("watsonx.ai returned 401 Unauthorized")
        if response.status_code != 200:
            raise WatsonxError(
                f"watsonx.ai chat endpoint returned HTTP {response.status_code}"
            )

        try:
            body: dict[str, Any] = response.json()
            raw_text: str = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            # Raise as _MalformedResponseError so adjudicate() can retry, not abort.
            raise _MalformedResponseError(
                "Could not extract text from watsonx.ai response"
            ) from exc

        return raw_text

    def _parse_model_output(self, raw_text: str) -> Adjudication:
        """
        Extract the first {...} JSON block from *raw_text* and build an Adjudication.

        Raises WatsonxError if the JSON is missing or missing required keys.
        """
        match = re.search(r"\{[^{}]*\}", raw_text, re.DOTALL)
        if not match:
            raise WatsonxError(
                f"No JSON object found in model output: {raw_text[:200]!r}"
            )

        try:
            data: dict[str, Any] = json.loads(match.group())
        except json.JSONDecodeError as exc:
            raise WatsonxError(
                f"JSON decode error in model output: {exc}"
            ) from exc

        try:
            correctness = float(data["correctness_score"])
            safety = float(data["safety_score"])
            minimalism = float(data["minimalism_score"])
            reasoning = str(data["reasoning"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WatsonxError(
                f"Missing or invalid field in model output: {exc}"
            ) from exc

        # Clamp scores to [0, 1]
        correctness = max(0.0, min(1.0, correctness))
        safety = max(0.0, min(1.0, safety))
        minimalism = max(0.0, min(1.0, minimalism))

        total = self._compute_total_score(correctness, safety, minimalism)
        verdict = self._compute_verdict(total)

        return Adjudication(
            correctness_score=correctness,
            safety_score=safety,
            minimalism_score=minimalism,
            total_score=total,
            verdict=verdict,
            reasoning=reasoning,
        )
