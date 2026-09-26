"""Tests for the Culprit HTTP API (culprit.server) — no Bob, no network."""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from culprit import server

TOKEN = "test-api-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

FIXED_REPORT = {
    "status": "FIXED",
    "root_cause": {"codebase": "shared", "file": "pricing.py", "line": 28,
                   "symbol": "compute_cart_total", "explanation": "discount applied pre-tax"},
    "fix": {"unified_diff": "-a\n+b\n"},
    "attempts": [{"num": 1, "test_result": {"passed": True, "output": "", "failed_tests": []}}],
    "commit_sha": "e8b856f2120510cc",
    "bobcoins_used": 0.2091,
    "elapsed_seconds": 62.8,
    "adjudicator_available": False,
}


def _fake_runner(report: dict | None, delay: float = 0.0, gate: threading.Event | None = None):
    def run(issue, report_path, on_line):
        on_line("   0.1s  🔁 Attempt 1/3")
        if gate is not None:
            gate.wait(5)
        time.sleep(delay)
        if report is not None:
            report_path.write_text(json.dumps(report), encoding="utf-8")
        return 0
    return run


def _wait_done(client, job_id, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        body = client.get(f"/debug/{job_id}", headers=AUTH).json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(server.API_TOKEN_ENV, TOKEN)
    server._jobs.clear()
    yield
    # never leave the lock held between tests
    if server._run_lock.locked():
        server._run_lock.release()


def test_health_needs_no_auth():
    client = TestClient(server.create_app(_fake_runner(FIXED_REPORT)))
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_debug_rejects_missing_or_wrong_token(headers):
    client = TestClient(server.create_app(_fake_runner(FIXED_REPORT)))
    assert client.post("/debug", json={"issue": "cart total wrong"}, headers=headers).status_code == 401
    assert client.get("/debug/abc", headers=headers).status_code == 401


def test_503_when_token_not_configured(monkeypatch):
    monkeypatch.delenv(server.API_TOKEN_ENV)
    client = TestClient(server.create_app(_fake_runner(FIXED_REPORT)))
    assert client.post("/debug", json={"issue": "cart total wrong"}, headers=AUTH).status_code == 503


def test_fixed_run_is_summarised():
    client = TestClient(server.create_app(_fake_runner(FIXED_REPORT)))
    started = client.post("/debug", json={"issue": "cart total wrong"}, headers=AUTH)
    assert started.status_code == 202
    body = _wait_done(client, started.json()["job_id"])
    assert body["status"] == "done"
    assert body["culprit_status"] == "FIXED"
    assert body["root_cause"].startswith("shared/pricing.py:28")
    assert body["tests_passed"] is True
    assert body["commit_sha"] == "e8b856f2120510cc"
    assert "FIXED" in body["summary"] and "e8b856f" in body["summary"]
    assert body["progress"] == ["0.1s  🔁 Attempt 1/3"]


def test_needs_human_reports_failure_reason():
    report = {**FIXED_REPORT, "status": "NEEDS_HUMAN", "commit_sha": None,
              "attempts": [{"num": 1, "failure_reason": "Confidence 0.40 below floor 0.7"}]}
    client = TestClient(server.create_app(_fake_runner(report)))
    body = _wait_done(client, client.post("/debug", json={"issue": "cart total wrong"}, headers=AUTH).json()["job_id"])
    assert body["culprit_status"] == "NEEDS_HUMAN"
    assert "Confidence 0.40" in body["summary"]


def test_missing_report_is_an_error():
    client = TestClient(server.create_app(_fake_runner(None)))
    body = _wait_done(client, client.post("/debug", json={"issue": "cart total wrong"}, headers=AUTH).json()["job_id"])
    assert body["status"] == "error"


def test_only_one_run_at_a_time():
    gate = threading.Event()
    client = TestClient(server.create_app(_fake_runner(FIXED_REPORT, gate=gate)))
    first = client.post("/debug", json={"issue": "cart total wrong"}, headers=AUTH)
    second = client.post("/debug", json={"issue": "another bug report"}, headers=AUTH)
    assert first.status_code == 202
    assert second.status_code == 409
    gate.set()
    _wait_done(client, first.json()["job_id"])
    third = client.post("/debug", json={"issue": "cart total wrong"}, headers=AUTH)
    assert third.status_code == 202
    _wait_done(client, third.json()["job_id"])


def test_caller_cannot_choose_folders_or_urls():
    """Only `issue` is accepted; extra fields are ignored, never forwarded."""
    seen = {}
    def runner(issue, report_path, on_line):
        seen["issue"] = issue
        report_path.write_text(json.dumps(FIXED_REPORT), encoding="utf-8")
        return 0
    client = TestClient(server.create_app(runner))
    resp = client.post("/debug", json={"issue": "cart total wrong", "folder": "C:/", "url": "http://evil"}, headers=AUTH)
    _wait_done(client, resp.json()["job_id"])
    assert seen == {"issue": "cart total wrong"}


def test_unknown_job_is_404():
    client = TestClient(server.create_app(_fake_runner(FIXED_REPORT)))
    assert client.get("/debug/nope", headers=AUTH).status_code == 404


def test_issue_length_validated():
    client = TestClient(server.create_app(_fake_runner(FIXED_REPORT)))
    assert client.post("/debug", json={"issue": "x"}, headers=AUTH).status_code == 422
