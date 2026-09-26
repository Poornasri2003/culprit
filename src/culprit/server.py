"""
Culprit HTTP API — lets IBM watsonx Orchestrate (or any client) trigger Culprit.

Presentation layer, like cli.py: no business logic. A debug run takes about
60 s, longer than Orchestrate's 40 s limit for a synchronous tool, so the API
is asynchronous:

    POST /debug            start a run on the demo project, returns a job id
    GET  /debug/{job_id}   poll: running / done / error, with the report summary
    GET  /health           liveness check (no auth)

Safety:
- Every /debug call requires "Authorization: Bearer <CULPRIT_API_TOKEN>".
- The API can only debug the allow-listed demo project (demo_workspace/
  sample_app and its live backend). Callers choose the issue text, never
  folders, URLs or commands.
- One run at a time; each run is a separate `python -m culprit debug`
  subprocess given an argv list (no shell).
"""

from __future__ import annotations

import hmac
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = REPO_ROOT / "demo_workspace" / "sample_app"
DEMO_FOLDERS = ("frontend", "backend", "shared")
DEMO_URL = os.environ.get("CULPRIT_DEMO_URL", "http://localhost:8000/cart/total")
DEMO_METHOD = "POST"
DEMO_BODY = '{"items":[{"price":20,"qty":2}],"discount_code":"SAVE10"}'
RUN_TIMEOUT_SECONDS = 420
API_TOKEN_ENV = "CULPRIT_API_TOKEN"

load_dotenv(REPO_ROOT / ".env")


# ── API models ────────────────────────────────────────────────────────────────


class DebugRequest(BaseModel):
    """What a caller may choose: only the bug description."""

    issue: str = Field(min_length=5, max_length=500, description="The bug, in one sentence.")


class DebugStarted(BaseModel):
    job_id: str
    status: Literal["running"]
    message: str


class DebugResult(BaseModel):
    job_id: str
    status: Literal["running", "done", "error"]
    elapsed_seconds: float
    progress: list[str]
    culprit_status: Optional[str] = None  # FIXED / PARTIAL / NEEDS_HUMAN
    root_cause: Optional[str] = None
    fix_diff: Optional[str] = None
    tests_passed: Optional[bool] = None
    commit_sha: Optional[str] = None
    bobcoins_used: Optional[float] = None
    adjudicator_available: Optional[bool] = None
    summary: str


# ── Job store ─────────────────────────────────────────────────────────────────


@dataclass
class _Job:
    job_id: str
    issue: str
    started: float = field(default_factory=time.monotonic)
    finished: Optional[float] = None
    state: Literal["running", "done", "error"] = "running"
    progress: deque[str] = field(default_factory=lambda: deque(maxlen=15))
    report: Optional[dict] = None
    error: Optional[str] = None


_jobs: dict[str, _Job] = {}
_run_lock = threading.Lock()


def _run_culprit(issue: str, report_path: Path, on_line) -> int:
    """Reset the demo, run `culprit debug` as a subprocess, stream its output lines."""
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "prepare_demo.py")],
        cwd=REPO_ROOT, check=True, capture_output=True, timeout=120,
    )
    argv = [
        sys.executable, "-m", "culprit", "debug",
        "--url", DEMO_URL, "--method", DEMO_METHOD, "--body", DEMO_BODY, "--auth-bearer",
        *[arg for name in DEMO_FOLDERS for arg in ("--folder", str(DEMO_ROOT / name))],
        "--issue", issue,
        "--json-report", str(report_path),
    ]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "COLUMNS": "100"}
    proc = subprocess.Popen(
        argv, cwd=REPO_ROOT, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace",
    )
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip())
        if time.monotonic() > deadline:
            proc.kill()
            break
    return proc.wait()


def _worker(job: _Job, runner) -> None:
    report_path = Path(tempfile.gettempdir()) / f"culprit_report_{job.job_id}.json"
    try:
        def on_line(line: str) -> None:
            # Keep only the live step lines ("  12.3s  🧪 ..."), not the final panel.
            if line.strip() and line.lstrip()[:1].isdigit() and "s " in line[:12]:
                job.progress.append(line.strip())

        runner(job.issue, report_path, on_line)
        if report_path.exists():
            job.report = json.loads(report_path.read_text(encoding="utf-8"))
            job.state = "done"
        else:
            job.state = "error"
            job.error = "Culprit finished without a report (see server log)."
    except Exception as exc:  # noqa: BLE001 — report any failure to the caller
        job.state = "error"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.finished = time.monotonic()
        report_path.unlink(missing_ok=True)
        _run_lock.release()


def _summarise(job: _Job) -> DebugResult:
    end = job.finished or time.monotonic()
    result = DebugResult(
        job_id=job.job_id,
        status=job.state,
        elapsed_seconds=round(end - job.started, 1),
        progress=list(job.progress),
        summary="",
    )
    if job.state == "running":
        result.summary = "Culprit is still working. Check again in about 20 seconds."
        return result
    if job.state == "error" or not job.report:
        result.summary = f"Culprit could not complete the run: {job.error}"
        return result

    r = job.report
    rc = r.get("root_cause") or {}
    fix = r.get("fix") or {}
    attempts = r.get("attempts") or []
    last_test = (attempts[-1].get("test_result") if attempts else None) or {}
    result.culprit_status = r.get("status")
    if rc:
        result.root_cause = f"{rc.get('codebase')}/{rc.get('file')}:{rc.get('line')} ({rc.get('symbol')}): {rc.get('explanation', '')[:400]}"
    result.fix_diff = (fix.get("unified_diff") or "")[:1500] or None
    result.tests_passed = last_test.get("passed") if last_test else None
    result.commit_sha = r.get("commit_sha")
    result.bobcoins_used = round(float(r.get("bobcoins_used", 0.0)), 3)
    result.adjudicator_available = r.get("adjudicator_available")
    if result.culprit_status == "FIXED":
        result.summary = (
            f"FIXED in {r.get('elapsed_seconds', 0):.0f}s for {result.bobcoins_used} Bobcoins. "
            f"Root cause: {rc.get('codebase')}/{rc.get('file')} line {rc.get('line')}. "
            f"All tests pass; fix and regression test committed ({(result.commit_sha or '')[:7]})."
        )
    else:
        reason = next((a.get("failure_reason") for a in reversed(attempts) if a.get("failure_reason")), None)
        result.summary = f"{result.culprit_status}: no fix was committed. {reason or ''}".strip()
    return result


# ── App ───────────────────────────────────────────────────────────────────────


def create_app(runner=_run_culprit) -> FastAPI:
    """Build the FastAPI app; `runner` is injectable for tests."""
    app = FastAPI(
        title="Culprit API",
        version="1.0.0",
        description="Trigger Culprit (IBM Bob-powered debugger) on the demo project.",
    )

    def require_token(authorization: str = Header(default="")) -> None:
        expected = os.environ.get(API_TOKEN_ENV, "")
        if not expected:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{API_TOKEN_ENV} is not configured")
        supplied = authorization[len("Bearer "):] if authorization.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing bearer token")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/debug", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_token)])
    def start_debug(req: DebugRequest) -> DebugStarted:
        if not _run_lock.acquire(blocking=False):
            running = next((j.job_id for j in _jobs.values() if j.state == "running"), None)
            raise HTTPException(status.HTTP_409_CONFLICT, f"A Culprit run is already in progress (job {running}).")
        job = _Job(job_id=uuid.uuid4().hex[:12], issue=req.issue)
        _jobs[job.job_id] = job
        threading.Thread(target=_worker, args=(job, runner), daemon=True).start()
        return DebugStarted(
            job_id=job.job_id,
            status="running",
            message="Culprit started. A run takes about 60 seconds; poll get_culprit_debug_result with this job_id.",
        )

    @app.get("/debug/{job_id}", dependencies=[Depends(require_token)])
    def get_result(job_id: str) -> DebugResult:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown job_id")
        return _summarise(job)

    return app


app = create_app()
