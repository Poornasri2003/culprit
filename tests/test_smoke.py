"""Smoke tests: schemas, sandbox denylist, source resolution, and the demo pack end-to-end."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from compass import __version__
from compass.application.demo import DEMO_PACK, write_demo_pack
from compass.domain.exceptions import SandboxViolation
from compass.domain.models import (
    AskAnswer,
    Command,
    DevLoop,
    Edge,
    EntryPoint,
    FileRef,
    OnboardingPack,
    RepoInventory,
)
from compass.infrastructure.command_runner import check_allowed
from compass.infrastructure.git_ops import copy_local, extract_zip
from compass.infrastructure.source import _looks_like_url, resolve_source


def test_version_is_a_string() -> None:
    assert isinstance(__version__, str) and __version__


def test_repo_inventory_round_trips() -> None:
    inv = RepoInventory(
        name="demo",
        description="a demo repo",
        languages=[],
        package_managers=[],
        entry_points=[
            EntryPoint(kind="cli", file=FileRef(path="src/demo/__main__.py"), hint="cli entry")
        ],
        top_dirs=["src"],
        tracked_file_count=1,
        size_estimate_tokens=100,
    )
    round_tripped = RepoInventory.model_validate_json(inv.model_dump_json())
    assert round_tripped == inv


def test_edge_requires_evidence() -> None:
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        Edge(from_module="a", to_module="b", kind="imports", evidence=[])


def test_devloop_accepts_failed_commands() -> None:
    d = DevLoop(
        prerequisites=["python 3.11"],
        commands=[
            Command(
                label="install", cmd="pip install -e .",
                exit_code=1, duration_seconds=2.0,
                tail_stdout="", tail_stderr="oops", verdict="failed",
            )
        ],
    )
    assert d.commands[0].verdict == "failed"


def test_ask_answer_default_ungrounded() -> None:
    a = AskAnswer(answer="I don't know from this repo", grounded=False)
    assert a.citations == []


@pytest.mark.parametrize("bad", ["sudo rm -rf /", "rm -rf /", "bash -c 'shutdown now'"])
def test_denylist_blocks_dangerous_commands(bad: str) -> None:
    with pytest.raises(SandboxViolation):
        check_allowed(bad)


def test_denylist_allows_ordinary_command() -> None:
    check_allowed("pytest -q")


def test_demo_pack_is_valid_and_round_trips() -> None:
    round_tripped = OnboardingPack.model_validate_json(DEMO_PACK.model_dump_json())
    assert round_tripped.inventory.name == "Flask (demo fixture)"
    assert len(round_tripped.guide.tour) >= 6
    assert len(round_tripped.guide.starter_tasks) == 3


def test_demo_writes_all_six_files(tmp_path: Path) -> None:
    write_demo_pack(tmp_path)
    for name in ("OVERVIEW.md", "ARCHITECTURE.md", "DEV_LOOP.md",
                 "TOUR.md", "STARTER_TASKS.md", "report.json"):
        assert (tmp_path / name).exists(), name
    for name in ("inventory", "architecture", "devloop", "guide"):
        assert (tmp_path / "notes" / f"{name}.json").exists(), name


# --- source resolution ----------------------------------------------------


@pytest.mark.parametrize("spec,expected", [
    ("https://github.com/pallets/flask", True),
    ("http://example.com/x.git", True),
    ("git@github.com:user/repo.git", True),
    ("ssh://git@host/repo.git", True),
    ("D:\\work\\my-service", False),
    ("./relative/path", False),
    ("my_repo.zip", False),
])
def test_looks_like_url(spec: str, expected: bool) -> None:
    assert _looks_like_url(spec) is expected


def test_resolve_source_copies_local_dir(tmp_path: Path) -> None:
    src = tmp_path / "src_repo"
    src.mkdir()
    (src / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    (src / ".git").mkdir()  # simulate a .git dir that should be ignored
    (src / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    ws = tmp_path / "workspace"
    ws.mkdir()
    checkout = resolve_source(str(src), ws)

    assert checkout.exists()
    assert (checkout / "hello.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert not (checkout / ".git").exists(), ".git should have been filtered out"


def test_resolve_source_extracts_zip_and_flattens_top_dir(tmp_path: Path) -> None:
    zip_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("sample-main/README.md", "# hello\n")
        zf.writestr("sample-main/src/app.py", "print('go')\n")

    ws = tmp_path / "workspace"
    ws.mkdir()
    checkout = resolve_source(str(zip_path), ws)

    # The top-level "sample-main" folder should have been flattened away.
    assert (checkout / "README.md").exists()
    assert (checkout / "src" / "app.py").exists()
    assert not (checkout / "sample-main").exists()


def test_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../escape.txt", "gotcha")

    ws = tmp_path / "workspace"
    ws.mkdir()
    from compass.infrastructure.git_ops import SourceError
    with pytest.raises(SourceError):
        extract_zip(zip_path, ws)


def test_copy_local_rejects_missing_dir(tmp_path: Path) -> None:
    from compass.infrastructure.git_ops import SourceError
    with pytest.raises(SourceError):
        copy_local(tmp_path / "does_not_exist", tmp_path / "ws")
