"""
HttpClient — authenticated HTTP probe adapter (§5, §9).

Makes real HTTP calls to live endpoints. Enforces HTTP_TIMEOUT_SECONDS.
Captures the full response as RuntimeEvidence whatever the status code.
Raises UnreachableError on timeout/DNS failure (E8). It does NOT decide
whether the bug is observable — a wrong value returned with HTTP 200 is a
real bug; E9 is decided by the Reproducer's failing test (SPEC §7).
"""

from __future__ import annotations

import time

import httpx

from culprit.config import HTTP_TIMEOUT_SECONDS, REDACTED_HEADER_NAMES
from culprit.domain.exceptions import UnreachableError
from culprit.domain.models import AuthContext, AuthScheme, Endpoint, RuntimeEvidence


def _apply_auth(request_headers: dict[str, str], auth_context: AuthContext) -> dict[str, str]:
    """Return a copy of *request_headers* with the appropriate auth header added."""
    headers = dict(request_headers)
    if auth_context.scheme == AuthScheme.BEARER and auth_context.token:
        headers["Authorization"] = f"Bearer {auth_context.token}"
    elif auth_context.scheme == AuthScheme.NONE:
        pass  # no header required
    # BASIC / OAUTH2 / APIKEY are stretch goals; nothing to inject here for MVP
    return headers


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with sensitive values replaced by '<redacted>'."""
    return {
        k: ("<redacted>" if k.lower() in REDACTED_HEADER_NAMES else v)
        for k, v in headers.items()
    }


class HttpClient:
    """
    Adapter for probing live endpoints (§5, §9).

    Pattern: Adapter — wraps httpx and maps HTTP outcomes to domain types.
    """

    def __init__(self, auth_context: AuthContext) -> None:
        """Build the httpx client; do not open connections yet."""
        self._auth_context = auth_context
        self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS)

    async def probe(self, endpoint: Endpoint) -> RuntimeEvidence:
        """
        Make an authenticated HTTP call and return RuntimeEvidence (any status).

        Raises UnreachableError on timeout/DNS failure (E8).
        """
        headers = _apply_auth(endpoint.headers, self._auth_context)
        if endpoint.body is not None and not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json"
        t0 = time.monotonic()
        try:
            response = await self._client.request(
                method=endpoint.method,
                url=endpoint.url,
                headers=headers,
                content=endpoint.body,
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.TransportError) as exc:
            raise UnreachableError(
                f"Could not reach {endpoint.url}: {exc}"
            ) from exc

        latency_ms = (time.monotonic() - t0) * 1000.0

        # Redact sensitive headers in the evidence (§10)
        redacted_headers = _redact_headers(dict(response.request.headers))

        evidence_endpoint = endpoint.model_copy(
            update={
                "headers": redacted_headers,
                "actual_status": response.status_code,
            }
        )

        return RuntimeEvidence(
            endpoint=evidence_endpoint,
            request_body=endpoint.body,
            response_body=response.text,
            response_status=response.status_code,
            latency_ms=latency_ms,
            stack_trace_hint=None,
        )

    async def close(self) -> None:
        """Release the underlying httpx client connection pool."""
        await self._client.aclose()
