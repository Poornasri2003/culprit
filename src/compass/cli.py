"""Command-line interface: `compass demo | run | serve | ask`."""
from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from compass import __version__

console = Console()


@click.group(help="Compass — generate onboarding packs for unfamiliar Git repos.")
@click.version_option(__version__, prog_name="compass")
def main() -> None:
    """Entry point for the `compass` console script."""


@main.command("demo")
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=Path("./onboarding_demo"),
    help="Where to write the canned onboarding pack (no Bob call, no network).",
)
def demo(output_dir: Path) -> None:
    """Write a full onboarding pack from a canned fixture. Great for showing the artifacts."""
    from compass.application.demo import write_demo_pack

    pack = write_demo_pack(output_dir)
    console.print(f"[green]compass demo[/green] wrote {pack.repo} to [bold]{output_dir}[/bold]")


@main.command("run")
@click.option("--repo", required=True,
              help="Anything git clone accepts, or a local path.")
@click.option("--role", type=click.Choice(["backend", "frontend", "sre", "full-stack"]),
              default=None, help="Optional role hint to tailor the tour.")
@click.option("--difficulty", type=click.Choice(["easy", "medium"]), default="easy",
              help="Difficulty of starter tasks.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=Path("./onboarding"),
              help="Where to write the onboarding pack.")
@click.option("--yes-large", is_flag=True, help="Allow runs above the total-token cap.")
def run(repo: str, role: str | None, difficulty: str, output_dir: Path, yes_large: bool) -> None:
    """Onboard a repo and write the pack to --output-dir."""
    from compass.application.orchestrator import Orchestrator

    Orchestrator().run(
        repo=repo,
        role=role,
        difficulty=difficulty,
        output_dir=output_dir,
        allow_large=yes_large,
    )


@main.command("serve")
@click.option("--pack", type=click.Path(exists=True, file_okay=False, path_type=Path),
              required=True, help="Directory of a previously produced pack.")
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8080, type=int)
def serve(pack: Path, host: str, port: int) -> None:
    """Serve /onboard and /ask against an existing pack."""
    import uvicorn
    from compass.server import build_app

    app = build_app(pack_dir=pack)
    uvicorn.run(app, host=host, port=port)


@main.command("ask")
@click.option("--pack", type=click.Path(exists=True, file_okay=False, path_type=Path),
              required=True, help="Directory of a previously produced pack.")
@click.argument("question")
def ask(pack: Path, question: str) -> None:
    """One-shot: ask a question about the given pack."""
    from compass.application.ask import answer_question

    reply = answer_question(pack_dir=pack, question=question)
    console.print(reply)
