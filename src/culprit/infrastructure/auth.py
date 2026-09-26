"""
AuthBuilder — constructs an AuthContext from environment variables (§2, §9).

Reads credentials exclusively from environment variables (.env loaded by
python-dotenv). Never accepts credentials from CLI arguments.
Raises AuthError if required env vars are absent or if token
acquisition fails (E7).
"""

from __future__ import annotations

import os

from culprit.domain.exceptions import AuthError
from culprit.domain.models import AuthContext, AuthScheme


class AuthBuilder:
    """
    Constructs a validated AuthContext from the environment (§2, §9).

    Pattern: Builder — assembles an immutable AuthContext step by step
    depending on the chosen auth scheme.
    """

    def build(self, scheme: AuthScheme) -> AuthContext:
        """
        Read env vars for scheme, acquire token if needed, return AuthContext.

        Raises AuthError if required vars are missing or token call fails (E7).
        """
        dispatch = {
            AuthScheme.BEARER: self._build_bearer,
            AuthScheme.NONE: self._build_none,
            AuthScheme.BASIC: self._build_basic,
            AuthScheme.OAUTH2: self._build_oauth2,
            AuthScheme.APIKEY: self._build_apikey,
        }
        builder = dispatch[scheme]
        return builder()

    # ------------------------------------------------------------------
    # MVP schemes
    # ------------------------------------------------------------------

    def _build_bearer(self) -> AuthContext:
        """Read BEARER_TOKEN from env; return AuthContext(scheme=BEARER)."""
        token = os.environ.get("BEARER_TOKEN")
        if not token:
            raise AuthError(
                "BEARER_TOKEN environment variable is not set (E7)"
            )
        # token is intentionally NOT logged (§10)
        return AuthContext(scheme=AuthScheme.BEARER, token=token)

    def _build_none(self) -> AuthContext:
        """Return AuthContext(scheme=NONE) for public endpoints."""
        return AuthContext(scheme=AuthScheme.NONE)

    # ------------------------------------------------------------------
    # Stretch-goal schemes — raise NotImplementedError
    # ------------------------------------------------------------------

    def _build_basic(self) -> AuthContext:
        """Read BASIC_USER, BASIC_PASS from env; return AuthContext(scheme=BASIC)."""
        raise NotImplementedError("stretch goal")

    def _build_oauth2(self) -> AuthContext:
        """Read OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_TOKEN_URL; acquire token."""
        raise NotImplementedError("stretch goal")

    def _build_apikey(self) -> AuthContext:
        """Read API_KEY, API_KEY_HEADER from env; return AuthContext(scheme=APIKEY)."""
        raise NotImplementedError("stretch goal")
