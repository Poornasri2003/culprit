"""Pydantic schemas for every subagent output.

Any deviation from SPEC.md must be reflected here, and every Bob subagent
validates its final JSON payload against these types before the orchestrator
accepts it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, NonNegativeFloat, NonNegativeInt


# --- Shared ---------------------------------------------------------------


class FileRef(BaseModel):
    path: str = Field(..., description="Repo-relative POSIX path.")
    line_start: int | None = None
    line_end: int | None = None


# --- Scout ----------------------------------------------------------------


class Language(BaseModel):
    name: str
    file_count: NonNegativeInt
    percent: float = Field(..., ge=0.0, le=100.0)


class EntryPoint(BaseModel):
    kind: Literal["cli", "http_server", "worker", "library", "script", "container"]
    file: FileRef
    hint: str = Field(..., max_length=280)


class RepoInventory(BaseModel):
    name: str
    description: str = Field(..., max_length=300)
    languages: list[Language]
    package_managers: list[str]
    entry_points: list[EntryPoint]
    top_dirs: list[str]
    tracked_file_count: NonNegativeInt
    size_estimate_tokens: NonNegativeInt
    # Optional content classifier — Scout fills this in so downstream code
    # can adapt when the "repo" is actually docs, media, or mixed content.
    content_kind: Literal["code", "docs", "media", "mixed", "unknown"] = "unknown"


# --- Cartographer ---------------------------------------------------------


class Module(BaseModel):
    name: str
    root: str
    role: str = Field(..., max_length=280)
    exemplar_files: list[FileRef] = Field(..., min_length=1, max_length=3)


class Edge(BaseModel):
    from_module: str
    to_module: str
    kind: Literal["imports", "calls", "http", "queue", "db"]
    evidence: list[FileRef] = Field(..., min_length=1)


class SpinePath(BaseModel):
    name: str
    steps: list[FileRef] = Field(..., min_length=2)


class ArchitectureMap(BaseModel):
    modules: list[Module]
    edges: list[Edge]
    spine_paths: list[SpinePath] = Field(..., min_length=1, max_length=5)
    mermaid: str


# --- DevLoopRunner --------------------------------------------------------


class Command(BaseModel):
    label: str
    cmd: str
    cwd: str | None = None
    exit_code: int
    duration_seconds: NonNegativeFloat
    tail_stdout: str
    tail_stderr: str
    verdict: Literal["ok", "flaky", "failed", "skipped"]


class DevLoop(BaseModel):
    prerequisites: list[str]
    commands: list[Command]
    notes: list[str] = Field(default_factory=list)


# --- Guide ----------------------------------------------------------------


class TourStop(BaseModel):
    order: int = Field(..., ge=1)
    file: FileRef
    why: str = Field(..., max_length=400)
    notice: str = Field(..., max_length=400)


class HintLadder(BaseModel):
    small: str
    medium: str
    large: str


class StarterTask(BaseModel):
    title: str
    difficulty: Literal["easy", "medium"]
    files_to_touch: list[FileRef] = Field(..., min_length=1)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    hints: HintLadder


class Guide(BaseModel):
    tour: list[TourStop] = Field(..., min_length=6, max_length=10)
    starter_tasks: list[StarterTask] = Field(..., min_length=3, max_length=3)


# --- Orchestrator wrap-up -------------------------------------------------


class SubagentReport(BaseModel):
    name: Literal["scout", "cartographer", "devloop_runner", "guide"]
    started_at: str  # ISO 8601
    duration_seconds: NonNegativeFloat
    tokens_prompt: NonNegativeInt
    tokens_completion: NonNegativeInt
    bobcoins: NonNegativeFloat = 0.0
    retries: NonNegativeInt
    ok: bool
    error: str | None = None


class OnboardingPack(BaseModel):
    repo: str
    role: str | None = None
    inventory: RepoInventory
    architecture: ArchitectureMap
    devloop: DevLoop
    guide: Guide
    subagents: list[SubagentReport]
    total_wall_clock_seconds: NonNegativeFloat
    total_tokens: NonNegativeInt
    total_bobcoins: NonNegativeFloat = 0.0


# --- /ask protocol --------------------------------------------------------


class AskRequest(BaseModel):
    question: str
    top_k: int = Field(6, ge=1, le=20)


class AskCitation(BaseModel):
    file: str
    line_start: NonNegativeInt
    line_end: NonNegativeInt
    snippet: str


class AskAnswer(BaseModel):
    answer: str
    citations: list[AskCitation] = Field(default_factory=list)
    grounded: bool
