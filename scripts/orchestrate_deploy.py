"""Deploy the Culprit tools and agent to IBM watsonx Orchestrate.

Usage:
    python scripts/orchestrate_deploy.py --tunnel-url https://<name>.trycloudflare.com
    python scripts/orchestrate_deploy.py --list-models          # pick an --llm
    python scripts/orchestrate_deploy.py --tunnel-url ... --llm <model from --list-models>

Needs in .env (never printed):
    WO_INSTANCE_URL    watsonx Orchestrate > Settings > API details > service instance URL
    WO_API_KEY         same page > Generate API key
    CULPRIT_API_TOKEN  bearer token for `culprit serve` (generated here if missing)

Uses the ADK installed in .venv-orchestrate/ (kept apart from Culprit's own
dependencies). Steps: connect the ADK to your instance, create a bearer
connection holding CULPRIT_API_TOKEN, import the two OpenAPI tools pointing
at the tunnel URL, then import and deploy the culprit_oncall agent.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
ORCH_DIR = REPO_ROOT / "orchestrate"
BUILD_DIR = ORCH_DIR / "build"
ADK = REPO_ROOT / ".venv-orchestrate" / ("Scripts/orchestrate.exe" if os.name == "nt" else "bin/orchestrate")
ENV_NAME = "culprit-trial"
APP_ID = "culprit_api"
AGENT_NAME = "culprit_oncall"


def _secrets() -> list[str]:
    return [v for v in (os.environ.get("WO_API_KEY"), os.environ.get("CULPRIT_API_TOKEN")) if v]


def adk(*args: str, allow_fail: bool = False) -> str:
    """Run an ADK command; print its output with any secret values masked."""
    shown = " ".join("***" if a in _secrets() else a for a in args)
    print(f"$ orchestrate {shown}")
    proc = subprocess.run(
        [str(ADK), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "NO_COLOR": "1"},
    )
    out = proc.stdout + proc.stderr
    for value in _secrets():
        out = out.replace(value, "***")
    out = "\n".join(line for line in out.splitlines() if "[DEBUG]" not in line).strip()
    if out:
        print("  " + out.replace("\n", "\n  "))
    if proc.returncode != 0 and not allow_fail:
        sys.exit(f"orchestrate {args[0]} {args[1] if len(args) > 1 else ''} failed (exit {proc.returncode})")
    return out


def ensure_api_token() -> None:
    """Create CULPRIT_API_TOKEN in .env once, without ever printing it."""
    if os.environ.get("CULPRIT_API_TOKEN"):
        return
    token = secrets.token_urlsafe(32)
    raw = ENV_FILE.read_bytes().decode("utf-8") if ENV_FILE.exists() else ""
    nl = "\r\n" if "\r\n" in raw else "\n"
    raw = raw.rstrip("\r\n") + nl + nl + "# Bearer token for `culprit serve` (watsonx Orchestrate connection)" + nl
    ENV_FILE.write_bytes((raw + f"CULPRIT_API_TOKEN={token}{nl}").encode("utf-8"))
    os.environ["CULPRIT_API_TOKEN"] = token
    print("Generated CULPRIT_API_TOKEN in .env")


def connect() -> None:
    url, key = os.environ.get("WO_INSTANCE_URL"), os.environ.get("WO_API_KEY")
    if not url or not key:
        sys.exit("Set WO_INSTANCE_URL and WO_API_KEY in .env first (see the docstring).")
    adk("env", "add", "-n", ENV_NAME, "-u", url, allow_fail=True)  # fine if it already exists
    adk("env", "activate", ENV_NAME, "--api-key", key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tunnel-url", help="Public HTTPS URL that forwards to `culprit serve`.")
    parser.add_argument("--llm", help="Model for the agent, e.g. from --list-models.")
    parser.add_argument("--list-models", action="store_true", help="List models available to the agent and exit.")
    args = parser.parse_args()

    if not ADK.exists():
        sys.exit("ADK not found. Run: python -m venv .venv-orchestrate && "
                 ".venv-orchestrate/Scripts/pip install ibm-watsonx-orchestrate")
    load_dotenv(ENV_FILE)
    connect()

    if args.list_models:
        adk("models", "list")
        return
    if not args.tunnel_url or not re.match(r"^https://[\w.-]+/?$", args.tunnel_url):
        sys.exit("--tunnel-url must look like https://<name>.trycloudflare.com")
    tunnel = args.tunnel_url.rstrip("/")
    ensure_api_token()

    # 1. Bearer connection that stores CULPRIT_API_TOKEN inside Orchestrate.
    adk("connections", "add", "-a", APP_ID, allow_fail=True)
    for env in ("draft", "live"):
        adk("connections", "configure", "-a", APP_ID, "--env", env, "-t", "team", "-k", "bearer",
            "--server-url", tunnel, allow_fail=(env == "live"))
        adk("connections", "set-credentials", "-a", APP_ID, "--env", env,
            "--token", os.environ["CULPRIT_API_TOKEN"], allow_fail=(env == "live"))

    # 2. OpenAPI tools pointing at the tunnel.
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    spec = (ORCH_DIR / "culprit_openapi.yaml").read_text(encoding="utf-8")
    spec = spec.replace("https://REPLACE_WITH_TUNNEL_URL", tunnel)
    (BUILD_DIR / "culprit_openapi.yaml").write_text(spec, encoding="utf-8")
    adk("tools", "import", "-k", "openapi", "-f", str(BUILD_DIR / "culprit_openapi.yaml"), "--app-id", APP_ID)

    # 3. The agent.
    agent = (ORCH_DIR / "culprit_agent.yaml").read_text(encoding="utf-8")
    if args.llm:
        agent = re.sub(r"(?m)^llm: .*$", f"llm: {args.llm}", agent)
    (BUILD_DIR / "culprit_agent.yaml").write_text(agent, encoding="utf-8")
    adk("agents", "import", "-f", str(BUILD_DIR / "culprit_agent.yaml"))
    adk("agents", "deploy", "--name", AGENT_NAME, allow_fail=True)

    print(f"\nDone. Open watsonx Orchestrate > Chat, pick the '{AGENT_NAME}' agent and say:\n"
          '  "Customers say the cart total is wrong when they use the SAVE10 discount code."')


if __name__ == "__main__":
    main()
