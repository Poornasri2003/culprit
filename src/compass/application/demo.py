"""Canned OnboardingPack for `compass demo`.

This deliberately does *not* call Bob. It exists so a judge can see the pack
Compass produces before we spend a single API token, and so smoke tests can
exercise the whole report-builder pipeline against a known-good fixture.
"""
from __future__ import annotations

from pathlib import Path

from compass.application.report_builder import write_pack
from compass.domain.models import (
    ArchitectureMap,
    Command,
    DevLoop,
    Edge,
    EntryPoint,
    FileRef,
    Guide,
    HintLadder,
    Language,
    Module,
    OnboardingPack,
    RepoInventory,
    SpinePath,
    StarterTask,
    SubagentReport,
    TourStop,
)

_DEMO_INVENTORY = RepoInventory(
    name="Flask (demo fixture)",
    description=(
        "A lightweight WSGI web application framework, used here as a Compass "
        "demo fixture. No Bob call was made to produce this pack."
    ),
    languages=[
        Language(name="python", file_count=180, percent=95.0),
        Language(name="html", file_count=9, percent=5.0),
    ],
    package_managers=["pip"],
    entry_points=[
        EntryPoint(kind="library", file=FileRef(path="src/flask/__init__.py"),
                   hint="Public API entry — `from flask import Flask`."),
        EntryPoint(kind="cli", file=FileRef(path="src/flask/cli.py"),
                   hint="The `flask` command."),
    ],
    top_dirs=["src", "tests", "docs"],
    tracked_file_count=250,
    size_estimate_tokens=45_000,
)

_DEMO_ARCHITECTURE = ArchitectureMap(
    modules=[
        Module(name="flask.app", root="src/flask",
               role="The Flask application class and dispatch loop.",
               exemplar_files=[FileRef(path="src/flask/app.py")]),
        Module(name="flask.routing", root="src/flask",
               role="URL rules, endpoint resolution, and view registration.",
               exemplar_files=[FileRef(path="src/flask/blueprints.py")]),
        Module(name="flask.wrappers", root="src/flask",
               role="Request and Response wrappers atop Werkzeug.",
               exemplar_files=[FileRef(path="src/flask/wrappers.py")]),
        Module(name="flask.cli", root="src/flask",
               role="The `flask` command; loads the app and runs subcommands.",
               exemplar_files=[FileRef(path="src/flask/cli.py")]),
    ],
    edges=[
        Edge(from_module="flask.cli", to_module="flask.app", kind="imports",
             evidence=[FileRef(path="src/flask/cli.py", line_start=1, line_end=40)]),
        Edge(from_module="flask.app", to_module="flask.routing", kind="calls",
             evidence=[FileRef(path="src/flask/app.py", line_start=200, line_end=260)]),
        Edge(from_module="flask.app", to_module="flask.wrappers", kind="calls",
             evidence=[FileRef(path="src/flask/app.py", line_start=900, line_end=940)]),
    ],
    spine_paths=[
        SpinePath(name="HTTP request → view → response",
                  steps=[FileRef(path="src/flask/app.py"),
                         FileRef(path="src/flask/wrappers.py"),
                         FileRef(path="src/flask/blueprints.py")]),
        SpinePath(name="CLI invocation → app factory",
                  steps=[FileRef(path="src/flask/cli.py"),
                         FileRef(path="src/flask/app.py")]),
    ],
    mermaid=(
        "graph TD\n"
        "  cli[flask.cli] --> app[flask.app]\n"
        "  app --> routing[flask.routing]\n"
        "  app --> wrappers[flask.wrappers]\n"
    ),
)

_DEMO_DEVLOOP = DevLoop(
    prerequisites=["Python 3.11+", "pip"],
    commands=[
        Command(label="install", cmd="pip install -e .[dev]", exit_code=0,
                duration_seconds=18.4, tail_stdout="Successfully installed flask-3.0.0",
                tail_stderr="", verdict="ok"),
        Command(label="test", cmd="pytest -q", exit_code=0, duration_seconds=6.2,
                tail_stdout="453 passed in 6.2s", tail_stderr="", verdict="ok"),
        Command(label="lint", cmd="ruff check .", exit_code=0, duration_seconds=0.4,
                tail_stdout="All checks passed!", tail_stderr="", verdict="ok"),
    ],
    notes=["Everything green on the demo fixture."],
)

_DEMO_GUIDE = Guide(
    tour=[
        TourStop(order=1, file=FileRef(path="src/flask/__init__.py"),
                 why="The public API — every import a newcomer will type comes through here.",
                 notice="Notice how little code re-exports; the class doing work is in app.py."),
        TourStop(order=2, file=FileRef(path="src/flask/app.py"),
                 why="The Flask class and the WSGI __call__ entry point.",
                 notice="Follow `wsgi_app` down to `full_dispatch_request`; that is the whole story."),
        TourStop(order=3, file=FileRef(path="src/flask/wrappers.py"),
                 why="Request/Response objects that view functions see.",
                 notice="These are thin wrappers on Werkzeug; useful boundary to understand."),
        TourStop(order=4, file=FileRef(path="src/flask/blueprints.py"),
                 why="How large apps split into modules with their own URL rules.",
                 notice="Blueprints defer registration; look at `register` on the app."),
        TourStop(order=5, file=FileRef(path="src/flask/cli.py"),
                 why="The `flask run` command; where an app factory is discovered.",
                 notice="`ScriptInfo` and `AppGroup` — small classes, load-bearing."),
        TourStop(order=6, file=FileRef(path="tests/test_basic.py"),
                 why="The clearest examples of the framework in action.",
                 notice="Every test starts by making a small app; a good pattern to copy."),
    ],
    starter_tasks=[
        StarterTask(
            title="Add a --json flag to `flask routes`",
            difficulty="easy",
            files_to_touch=[FileRef(path="src/flask/cli.py")],
            acceptance_criteria=[
                "`flask routes --json` prints valid JSON to stdout.",
                "The existing `flask routes` output is unchanged.",
                "A test in `tests/test_cli.py` covers the JSON path.",
            ],
            hints=HintLadder(
                small="Find the existing `routes` command in cli.py.",
                medium="Add an `--json/--no-json` option; branch on it before printing.",
                large="Use `click.option('--json', 'as_json', is_flag=True)` and dump "
                      "`[dict(rule=...) for rule in app.url_map.iter_rules()]` with json.dumps.",
            ),
        ),
        StarterTask(
            title="Document the app factory pattern in the tutorial",
            difficulty="easy",
            files_to_touch=[FileRef(path="docs/patterns/appfactories.rst")],
            acceptance_criteria=[
                "One new subsection explaining why teams split config from creation.",
                "A working code example a reader can paste and run.",
                "`make -C docs html` builds cleanly with no warnings.",
            ],
            hints=HintLadder(
                small="Search the docs for `create_app`; it is under-explained.",
                medium="Model the new section after `docs/patterns/celery.rst`.",
                large="Add a code block with `create_app(config=None)` and mention "
                      "the `FLASK_APP=myapp:create_app` env var.",
            ),
        ),
        StarterTask(
            title="Emit a helpful error when `flask run` finds no app",
            difficulty="medium",
            files_to_touch=[FileRef(path="src/flask/cli.py")],
            acceptance_criteria=[
                "Running `flask run` in an empty directory prints a two-line hint pointing at FLASK_APP.",
                "Exit code is 2 (usage error), not 1.",
                "New test asserts both the exit code and the hint substring.",
            ],
            hints=HintLadder(
                small="Find where `ScriptInfo.load_app` raises today.",
                medium="Wrap the raise in a friendlier `click.UsageError` at the CLI boundary.",
                large="Catch `NoAppException`, print the hint via `click.echo(..., err=True)`, "
                      "and `sys.exit(2)`.",
            ),
        ),
    ],
)

DEMO_PACK = OnboardingPack(
    repo="demo:pallets/flask",
    role="backend",
    inventory=_DEMO_INVENTORY,
    architecture=_DEMO_ARCHITECTURE,
    devloop=_DEMO_DEVLOOP,
    guide=_DEMO_GUIDE,
    subagents=[
        SubagentReport(name="scout", started_at="2026-09-27T00:00:00+00:00",
                       duration_seconds=0.0, tokens_prompt=0, tokens_completion=0,
                       retries=0, ok=True),
        SubagentReport(name="cartographer", started_at="2026-09-27T00:00:00+00:00",
                       duration_seconds=0.0, tokens_prompt=0, tokens_completion=0,
                       retries=0, ok=True),
        SubagentReport(name="devloop_runner", started_at="2026-09-27T00:00:00+00:00",
                       duration_seconds=0.0, tokens_prompt=0, tokens_completion=0,
                       retries=0, ok=True),
        SubagentReport(name="guide", started_at="2026-09-27T00:00:00+00:00",
                       duration_seconds=0.0, tokens_prompt=0, tokens_completion=0,
                       retries=0, ok=True),
    ],
    total_wall_clock_seconds=0.0,
    total_tokens=0,
)


def write_demo_pack(output_dir: Path) -> OnboardingPack:
    """Write the canned pack to `output_dir` and return it."""
    write_pack(DEMO_PACK, output_dir)
    return DEMO_PACK
