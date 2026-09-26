"""
Tests for src/culprit/domain/models.py.

Covers:
  (a) AuthContext.token never appears in model_dump / model_dump_json
  (c) CulpritReport builds with root_cause=None
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from culprit.domain.models import (
    Attempt,
    AuthContext,
    AuthScheme,
    Bug,
    CulpritReport,
    ReportStatus,
)


# ── (a) AuthContext.token is excluded from serialisation ─────────────────────


def test_auth_context_token_excluded_from_model_dump():
    ctx = AuthContext(scheme=AuthScheme.BEARER, token="super-secret")
    dumped = ctx.model_dump()
    assert "token" not in dumped


def test_auth_context_token_excluded_from_model_dump_json():
    ctx = AuthContext(scheme=AuthScheme.BEARER, token="super-secret")
    data = json.loads(ctx.model_dump_json())
    assert "token" not in data


def test_auth_context_token_excluded_when_none():
    ctx = AuthContext(scheme=AuthScheme.NONE, token=None)
    dumped = ctx.model_dump()
    assert "token" not in dumped


# ── (c) CulpritReport builds with root_cause=None ───────────────────────────


def _minimal_bug() -> Bug:
    return Bug(description="something is broken", runtime_evidence=[], static_evidence=[])


def test_culprit_report_builds_with_root_cause_none():
    report = CulpritReport(
        status=ReportStatus.NEEDS_HUMAN,
        bug=_minimal_bug(),
        root_cause=None,
        attempts=[],
        elapsed_seconds=1.0,
        bobcoins_used=0.0,
    )
    assert report.root_cause is None
    assert report.status == ReportStatus.NEEDS_HUMAN


def test_culprit_report_root_cause_none_in_dump():
    report = CulpritReport(
        status=ReportStatus.PARTIAL,
        bug=_minimal_bug(),
        root_cause=None,
        attempts=[],
        elapsed_seconds=0.5,
        bobcoins_used=0.0,
    )
    dumped = report.model_dump()
    assert dumped["root_cause"] is None
