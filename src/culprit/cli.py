"""
Culprit CLI entry point (§2).

Defines the `culprit debug` Click command. Parses and validates CLI flags,
loads .env, instantiates infrastructure objects, delegates entirely to
Orchestrator, and renders the CulpritReport to the terminal via Rich.

This file contains NO business logic. It is a pure presentation layer.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import anyio
import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich import box

from culprit.config import MAX_BOBCOIN_PER_RUN, MAX_CODEBASES, MAX_URLS
from culprit.domain.exceptions import AuthError, UnreachableError
from culprit.domain.models import AuthScheme, Bug, CulpritReport, Endpoint, ReportStatus, SourceCodebase
from culprit.infrastructure.auth import AuthBuilder
from culprit.infrastructure.bob_client import BobClient
from culprit.infrastructure.test_runner import project_root
from culprit.infrastructure.git_ops import GitOps
from culprit.infrastructure.http_client import HttpClient
from culprit.infrastructure.test_runner import TestRunner
from culprit.infrastructure.watsonx_client import WatsonxClient
from culprit.infrastructure.workspace import Workspace
from culprit.subagents.factory import SubagentFactory
from culprit.application.orchestrator import Orchestrator
from culprit.application.report_builder import ReportBuilder

console = Console()


@click.group()
def cli() -> None:
    """Culprit — AI-powered backend debugger."""


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True, help="Interface to bind.")
@click.option("--port", default=8080, show_default=True, type=int, help="Port to listen on.")
def serve_command(host: str, port: int) -> None:
    """Start the Culprit HTTP API (used by the watsonx Orchestrate agent)."""
    import uvicorn

    uvicorn.run("culprit.server:app", host=host, port=port, log_level="info")


@cli.command("debug")
@click.argument("inputs", nargs=-1)
@click.option("--issue", required=True, help="Bug description.")
@click.option("--url", multiple=True, help="Live endpoint URL (repeatable, max 3).")
@click.option("--method", default="GET", show_default=True, help="HTTP method used to probe --url.")
@click.option("--body", default=None, help="Request body (e.g. JSON) sent when probing --url.")
@click.option(
    "--folder",
    multiple=True,
    type=click.Path(exists=True),
    help="Local code folder (repeatable, 1-6).",
)
@click.option("--git", "git_urls", multiple=True, help="Remote repo to clone (repeatable, 1-4).")
@click.option(
    "--trace",
    type=click.Path(exists=True),
    default=None,
    help="Path to stack trace or error log.",
)
@click.option(
    "--auth-bearer",
    "auth_scheme",
    flag_value="BEARER",
    help="Read BEARER_TOKEN from .env.",
)
@click.option(
    "--auth-basic",
    "auth_scheme",
    flag_value="BASIC",
    help="Read BASIC_USER, BASIC_PASS from .env.",
)
@click.option(
    "--auth-oauth2",
    "auth_scheme",
    flag_value="OAUTH2",
    help="Read OAUTH_* vars from .env.",
)
@click.option(
    "--auth-apikey",
    "auth_scheme",
    flag_value="APIKEY",
    help="Read API_KEY, API_KEY_HEADER from .env.",
)
@click.option(
    "--no-auth",
    "auth_scheme",
    flag_value="NONE",
    help="Explicit for public endpoints.",
)
@click.option(
    "--json-report",
    type=click.Path(dir_okay=False),
    default=None,
    help="Also write the final report as JSON to this file (used by `culprit serve`).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Analyze only; no fix applied, no commit.",
)
def debug_command(
    inputs: tuple[str, ...],
    issue: str,
    url: tuple[str, ...],
    method: str,
    body: Optional[str],
    folder: tuple[str, ...],
    git_urls: tuple[str, ...],
    trace: Optional[str],
    auth_scheme: Optional[str],
    json_report: Optional[str],
    dry_run: bool,
) -> None:
    """
    Debug a live backend service and attempt an automated fix.

    Validates flag counts against MAX_URLS and MAX_CODEBASES, builds
    infrastructure objects, and delegates to Orchestrator.run().
    Renders the CulpritReport with Rich on completion.
    """
    # Load .env silently (never print its contents)
    load_dotenv(override=False)

    # ── Validate counts ────────────────────────────────────────────────────
    if len(url) > MAX_URLS:
        raise click.UsageError(f"Too many --url flags (max {MAX_URLS})")
    if len(folder) > MAX_CODEBASES:
        raise click.UsageError(f"Too many --folder flags (max {MAX_CODEBASES})")
    if not folder and not git_urls:
        raise click.UsageError("At least one --folder or --git source is required")

    # ── Build workspace ────────────────────────────────────────────────────
    import tempfile
    work_dir = Path(tempfile.mkdtemp(prefix="culprit_"))
    workspace = Workspace(work_dir=work_dir)
    for idx, f in enumerate(folder):
        name = Path(f).name or f"codebase_{idx}"
        workspace.load_folder(Path(f).resolve(), name)

    codebases = workspace.get_codebases()

    # ── Build auth context ─────────────────────────────────────────────────
    scheme = AuthScheme(auth_scheme) if auth_scheme else AuthScheme.NONE
    http_client: HttpClient | None = None
    if url:
        try:
            auth_context = AuthBuilder().build(scheme)
        except (AuthError, NotImplementedError) as exc:
            console.print(f"[red]Auth error:[/red] {exc}")
            sys.exit(1)
        endpoint: Endpoint | None = Endpoint(
            url=url[0],
            method=method.upper(),
            headers={},
            expected_status=200,
            body=body,
        )
        http_client = HttpClient(auth_context)
    else:
        auth_context = None
        endpoint = None

    # ── Build domain Bug ──────────────────────────────────────────────────
    static_evidence = list(codebases)
    bug = Bug(
        description=issue,
        runtime_evidence=[],
        static_evidence=static_evidence,
    )

    # ── Wire infrastructure ────────────────────────────────────────────────
    # Bob must run inside the folder that contains every codebase so it can
    # read (and cross-reference) all of them; work_dir is only for clones.
    bob_client = BobClient(
        workspace_root=project_root(codebases),
        max_bobcoins=MAX_BOBCOIN_PER_RUN,
    )
    factory = SubagentFactory(workspace, bob_client)
    watsonx_client = WatsonxClient()
    test_runner = TestRunner()
    git_ops = GitOps()
    report_builder = ReportBuilder(bug)

    # ── Progress callback using Rich ──────────────────────────────────────
    run_start = time.monotonic()

    def progress(msg: str) -> None:
        elapsed = time.monotonic() - run_start
        console.print(f"[dim]{elapsed:6.1f}s[/dim]  {msg}")

    orchestrator = Orchestrator(
        workspace=workspace,
        factory=factory,
        watsonx_client=watsonx_client,
        test_runner=test_runner,
        git_ops=git_ops,
        http_client=http_client,
        report_builder=report_builder,
        dry_run=dry_run,
        progress_cb=progress,
        endpoint=endpoint,
    )

    # ── Run ───────────────────────────────────────────────────────────────
    try:
        report: CulpritReport = anyio.from_thread.run_sync(
            lambda: anyio.run(orchestrator.run, bug)  # type: ignore[arg-type]
        )
    except Exception:
        report = anyio.run(orchestrator.run, bug)

    # ── Render final report ───────────────────────────────────────────────
    _render_report(report, run_start)
    if json_report:
        Path(json_report).write_text(report.model_dump_json(indent=2), encoding="utf-8")

    exit_code = 0 if report.status == ReportStatus.FIXED else 1
    sys.exit(exit_code)


def _render_report(report: CulpritReport, run_start: float) -> None:
    """Render the final CulpritReport to the terminal using Rich."""
    elapsed = time.monotonic() - run_start

    status_color = {
        ReportStatus.FIXED: "green",
        ReportStatus.PARTIAL: "yellow",
        ReportStatus.NEEDS_HUMAN: "red",
    }.get(report.status, "white")

    console.print()
    console.rule("[bold]Culprit Report[/bold]")

    # Status
    console.print(f"  Status:       [{status_color}]{report.status.value}[/{status_color}]")
    console.print(f"  Elapsed:      {elapsed:.1f}s")
    console.print(f"  Bobcoins:     {report.bobcoins_used:.4f}")

    # Root cause
    if report.root_cause:
        rc = report.root_cause
        console.print(
            f"  Root cause:   {rc.file}:{rc.line}  [{rc.codebase}]  "
            f"confidence={rc.confidence:.0%}"
        )
        console.print(f"                {rc.explanation}")

    # Fix diff
    if report.fix and report.fix.unified_diff:
        console.print()
        console.print(
            Panel(
                Syntax(report.fix.unified_diff, "diff", theme="monokai", line_numbers=False),
                title="Fix diff",
                border_style="green",
            )
        )

    # Adjudication table
    if report.adjudications:
        table = Table(title="Adjudication scores", box=box.SIMPLE)
        table.add_column("#", style="dim")
        table.add_column("Correctness")
        table.add_column("Safety")
        table.add_column("Minimalism")
        table.add_column("Total")
        table.add_column("Verdict")
        for i, adj in enumerate(report.adjudications, 1):
            verdict_style = "green" if adj.verdict.value == "APPROVE" else "red"
            table.add_row(
                str(i),
                f"{adj.correctness_score:.2f}",
                f"{adj.safety_score:.2f}",
                f"{adj.minimalism_score:.2f}",
                f"{adj.total_score:.2f}",
                f"[{verdict_style}]{adj.verdict.value}[/{verdict_style}]",
            )
        console.print(table)
    elif not report.adjudicator_available:
        console.print("  Adjudicator:  [dim]watsonx not configured[/dim]")

    # Test result
    if report.attempts:
        last = report.attempts[-1]
        if last.test_result:
            tr = last.test_result
            tr_color = "green" if tr.passed else "red"
            console.print(f"  Tests:        [{tr_color}]{'PASSED' if tr.passed else 'FAILED'}[/{tr_color}]")

    # Commit SHA
    commit_sha = report.commit_sha
    if commit_sha:
        console.print(f"  Commit SHA:   {commit_sha}")

    console.print()
