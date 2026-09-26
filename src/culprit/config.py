"""
Culprit configuration constants.

All hard limits (§3) and watsonx.ai Adjudicator settings (§12) live here.
Model IDs and watsonx coordinates are read from environment variables;
this module provides only the defaults and the numeric thresholds.
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

# §12 — watsonx.ai Adjudicator
ADJUDICATION_THRESHOLD: float = 0.65    # total_score below this -> REJECT
WATSONX_TIMEOUT_SECONDS: int = 20
WATSONX_MAX_RETRIES: int = 2

# §12 — env-var names (defaults supplied; never the actual credential values)
WATSONX_MODEL_ID_ENV: str = "WATSONX_MODEL_ID"        # env var name; default value "ibm/granite-3-8b-instruct"
WATSONX_URL_ENV: str = "WATSONX_URL"                  # env var name; default value "https://us-south.ml.cloud.ibm.com"
WATSONX_PROJECT_ID_ENV: str = "WATSONX_PROJECT_ID"    # env var name; no default
IBM_CLOUD_API_KEY_ENV: str = "IBM_CLOUD_API_KEY"      # env var name; no default

# Redacted header names (§10)
REDACTED_HEADER_NAMES: frozenset[str] = frozenset({"authorization", "x-api-key", "cookie"})
