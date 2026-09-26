"""Typed exceptions Compass raises. Callers should never need `except Exception`."""


class CompassError(Exception):
    """Base class for all Compass errors."""


class ConfigError(CompassError):
    """Missing or malformed configuration (usually a bad or missing env var)."""


class BobShellError(CompassError):
    """Bob Shell subprocess failed or returned an unusable result."""


class BobBudgetExhausted(CompassError):
    """Compass hit its Bobcoin budget for this run."""


class SchemaError(CompassError):
    """A subagent produced JSON that did not match its Pydantic schema, twice."""


class BudgetExceeded(CompassError):
    """Compass refused to proceed because it would cross a token or size cap."""


class SandboxViolation(CompassError):
    """DevLoopRunner attempted a denied command."""
