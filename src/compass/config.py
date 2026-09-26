"""Runtime configuration, loaded from environment variables (.env is honored)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # picks up ./.env if present; no-op in production containers


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Environment variable {name} is required. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class BobConfig:
    api_key: str
    base_url: str = "https://api.bob.ibm.com"
    model: str = "bob-shell-default"

    @classmethod
    def from_env(cls) -> "BobConfig":
        return cls(
            api_key=_required("BOB_API_KEY"),
            base_url=os.getenv("BOB_BASE_URL", "https://api.bob.ibm.com"),
            model=os.getenv("BOB_MODEL", "bob-shell-default"),
        )


@dataclass(frozen=True)
class SandboxLimits:
    cmd_timeout_seconds: int = 60
    cmd_output_max_bytes: int = 4096
    tokens_per_subagent: int = 50_000
    tokens_total_max: int = 250_000

    @classmethod
    def from_env(cls) -> "SandboxLimits":
        return cls(
            cmd_timeout_seconds=_int("COMPASS_CMD_TIMEOUT_SECONDS", 60),
            cmd_output_max_bytes=_int("COMPASS_CMD_OUTPUT_MAX_BYTES", 4096),
            tokens_per_subagent=_int("COMPASS_TOKENS_PER_SUBAGENT", 50_000),
            tokens_total_max=_int("COMPASS_TOKENS_TOTAL_MAX", 250_000),
        )


@dataclass(frozen=True)
class ServerConfig:
    """Bearer-token auth for `compass serve`. Auto-generates a token when the
    env var is missing so `compass serve` never fails to start on a stock
    machine — the generated token is printed to stdout on server boot.
    """
    api_token: str

    @classmethod
    def from_env(cls) -> "ServerConfig":
        import secrets
        token = os.getenv("COMPASS_API_TOKEN")
        if not token:
            token = "compass_" + secrets.token_urlsafe(24)
            print(f"[compass] generated COMPASS_API_TOKEN={token}")
        return cls(api_token=token)


@dataclass(frozen=True)
class OrchestrateConfig:
    instance_url: str | None
    api_key: str | None

    @classmethod
    def from_env(cls) -> "OrchestrateConfig":
        return cls(
            instance_url=os.getenv("WO_INSTANCE_URL"),
            api_key=os.getenv("WO_API_KEY"),
        )


@dataclass(frozen=True)
class Paths:
    workspaces: Path = field(default_factory=lambda: Path.cwd() / "workspaces")
    sessions: Path = field(default_factory=lambda: Path.cwd() / "bob_sessions")

    def ensure(self) -> None:
        self.workspaces.mkdir(parents=True, exist_ok=True)
        self.sessions.mkdir(parents=True, exist_ok=True)
