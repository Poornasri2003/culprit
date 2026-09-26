"""
Culprit configuration constants.

All hard limits (§3) live here.
No credentials are stored here.
"""

# §3 — hard limits
MAX_ATTEMPTS: int = 3
MAX_WALLCLOCK_SECONDS: int = 240
MAX_FILES_PER_SUBAGENT: int = 25
MAX_CODEBASES: int = 6
MAX_URLS: int = 3
MAX_BOBCOIN_PER_RUN: float = 8
CONFIDENCE_FLOOR: float = 0.70
HTTP_TIMEOUT_SECONDS: int = 15
AUTH_TOKEN_TTL_SECONDS: int = 300

# Redacted header names (§10)
REDACTED_HEADER_NAMES: frozenset[str] = frozenset({"authorization", "x-api-key", "cookie"})
