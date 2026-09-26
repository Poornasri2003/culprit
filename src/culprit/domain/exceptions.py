"""
Culprit domain exceptions.

Each exception corresponds to an explicit edge case in §7.
Catching code must not swallow these silently — they propagate to the
orchestrator which maps them to a CulpritReport status.
"""


class CulpritError(Exception):
    """Base class for all Culprit domain errors."""


class SubagentError(CulpritError):
    """Bob returned malformed JSON after the allowed retry (E2)."""


class BudgetExhausted(CulpritError):
    """Bobcoin budget MAX_BOBCOIN_PER_RUN exceeded (E4)."""


class AuthError(CulpritError):
    """Auth token acquisition or refresh failed (E7)."""


class UnreachableError(CulpritError):
    """Target URL timed out or DNS-resolved to nothing (E8)."""


class BugNotObservable(CulpritError):
    """URL returned 200; the bug could not be reproduced (E9)."""


class MissingCodebaseError(CulpritError):
    """Bug spans a codebase not provided to the run (E10)."""


class ConfigurationError(CulpritError):
    """Required configuration (env var, CLI flag combination) is missing or invalid."""


class WatsonxError(CulpritError):
    """watsonx.ai call failed (timeout, auth, malformed output); caller fails open (§12)."""


class BobShellError(CulpritError):
    """Bob Shell subprocess failed to start or exited non-zero (§14)."""
