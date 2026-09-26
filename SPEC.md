# Compass — Schemas & subagent prompts

Companion to [ARCHITECTURE.md](ARCHITECTURE.md). This file locks down the JSON schemas each Bob subagent must produce and the system prompts that drive them. If the code and this doc disagree, this doc is wrong — fix it here first.

---

## 1. Shared types

```python
# src/compass/domain/models.py
from pydantic import BaseModel, Field
from typing import Literal

class FileRef(BaseModel):
    path: str                    # repo-relative POSIX path
    line_start: int | None = None
    line_end: int | None = None
```

## 2. Scout

**Job:** inventory the repo. Read-only. No architectural claims.

**Output schema — `RepoInventory`**

```python
class Language(BaseModel):
    name: str                    # "python", "typescript", ...
    file_count: int
    percent: float               # of tracked source files

class EntryPoint(BaseModel):
    kind: Literal["cli", "http_server", "worker", "library", "script", "container"]
    file: FileRef
    hint: str                    # one sentence

class RepoInventory(BaseModel):
    name: str
    description: str             # ≤ 300 chars, pulled from README/package metadata
    languages: list[Language]
    package_managers: list[str]  # "pip", "poetry", "npm", "pnpm", "cargo", ...
    entry_points: list[EntryPoint]
    top_dirs: list[str]          # top-level source directories, in traversal order
    tracked_file_count: int
    size_estimate_tokens: int    # for the budget gate in the orchestrator
```

**System prompt (excerpt).**

> You are Compass's Scout. Your job is inventory, not judgment. Never guess a language from a filename alone — read the file if you're unsure. Never invent files. Never speculate about architecture; that is the Cartographer's job. Emit exactly one JSON object matching `RepoInventory`. If a field is unknown, use `null` — do not fabricate.

**Tool allowlist:** `list_dir`, `read_file`, `search`.

## 3. Cartographer

**Job:** produce a **verified** architecture map. Every edge must be backed by evidence.

**Output schema — `ArchitectureMap`**

```python
class Module(BaseModel):
    name: str                        # human-readable, e.g. "checkout.api"
    root: str                        # repo-relative directory
    role: str                        # one sentence
    exemplar_files: list[FileRef]    # 1–3 files representative of this module

class Edge(BaseModel):
    from_module: str                 # Module.name
    to_module: str                   # Module.name
    kind: Literal["imports", "calls", "http", "queue", "db"]
    evidence: list[FileRef]          # concrete files/lines proving the edge

class SpinePath(BaseModel):
    name: str                        # e.g. "checkout → auth → db"
    steps: list[FileRef]             # ordered

class ArchitectureMap(BaseModel):
    modules: list[Module]
    edges: list[Edge]
    spine_paths: list[SpinePath]     # 3–5 end-to-end request/data flows
    mermaid: str                     # rendered Mermaid diagram source
```

**System prompt (excerpt).**

> You are Compass's Cartographer. Do not include an edge unless you can cite the file and line that proves it, using `search` or `find_references`. Prefer fewer, correct edges over many speculative ones. Every entry in `edges[].evidence` must come from a tool call in this session — you are not permitted to cite from memory. The Mermaid diagram must include only modules and edges you have already listed.

**Tool allowlist:** `list_dir`, `read_file`, `search`, `find_references`.

## 4. DevLoopRunner

**Job:** produce the actual commands that work. Bob proposes; the runner executes; only what passes gets written.

**Output schema — `DevLoop`**

```python
class Command(BaseModel):
    label: str                       # "install", "test", "lint", ...
    cmd: str
    cwd: str | None = None
    exit_code: int
    duration_seconds: float
    tail_stdout: str                 # last 40 lines, capped
    tail_stderr: str
    verdict: Literal["ok", "flaky", "failed", "skipped"]

class DevLoop(BaseModel):
    prerequisites: list[str]         # e.g. "python 3.11+", "docker"
    commands: list[Command]          # in the order a newcomer should run them
    notes: list[str]                 # human-readable caveats
```

**Loop protocol.**

1. Bob proposes one command at a time as a tool call to `run_command`.
2. The runner executes it (sandboxed, 60 s timeout, 4 KB output cap) and returns exit code + tails.
3. Bob decides whether to record it, retry with a variant, or give up on that step.
4. Bob emits a final `DevLoop` where each `Command` has `verdict != "skipped"` **only if** the runner actually executed it in this session.

**System prompt (excerpt).**

> You are Compass's DevLoopRunner. You may only claim a command works if you ran it in this session and it returned exit code 0. If a command fails, either propose a fix and retry once, or record it with `verdict: "failed"` and move on — do not lie. Never invent commands that were not executed.

**Tool allowlist:** `list_dir`, `read_file`, `run_command`.

## 5. Guide

**Job:** the reading tour and three starter tasks.

**Output schema — `Guide`**

```python
class TourStop(BaseModel):
    order: int
    file: FileRef
    why: str                         # 1–2 sentences: why this file matters
    notice: str                      # 1–2 sentences: what to pay attention to

class HintLadder(BaseModel):
    small: str                       # a nudge
    medium: str                      # a pointer
    large: str                       # nearly the answer

class StarterTask(BaseModel):
    title: str
    difficulty: Literal["easy", "medium"]
    files_to_touch: list[FileRef]
    acceptance_criteria: list[str]   # bullet points, testable
    hints: HintLadder

class Guide(BaseModel):
    tour: list[TourStop]             # 6–10 stops, ordered
    starter_tasks: list[StarterTask] # exactly 3
```

**System prompt (excerpt).**

> You are Compass's Guide. Pick tour stops by dependency depth, not by file size. A good tour teaches the shape of the system in 30 minutes. Draft starter tasks that a new teammate can finish in half a day — no research spikes, no infra changes, no "rewrite this module." Each task must have three hint levels: `small` (a nudge), `medium` (a pointer), `large` (nearly the answer).

**Tool allowlist:** `list_dir`, `read_file`, `search`, `git_log`.

## 6. Orchestrator report

The orchestrator wraps everything into a top-level record used by the CLI, the server, and `bob_sessions/report.json`.

```python
class SubagentReport(BaseModel):
    name: Literal["scout", "cartographer", "devloop_runner", "guide"]
    started_at: str                  # ISO 8601
    duration_seconds: float
    tokens_prompt: int
    tokens_completion: int
    retries: int
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
    total_wall_clock_seconds: float
    total_tokens: int
```

## 7. `/ask` protocol

```python
class AskRequest(BaseModel):
    question: str
    top_k: int = 6                   # chunks to retrieve

class AskCitation(BaseModel):
    file: str
    line_start: int
    line_end: int
    snippet: str

class AskAnswer(BaseModel):
    answer: str                      # or "I don't know from this repo"
    citations: list[AskCitation]     # empty allowed only on the refusal answer
    grounded: bool                   # false ⇒ refused
```

**Ground rule.** The `/ask` handler retrieves the top-k chunks from `notes/*.json`, hands them to the LLM (Bob or watsonx.ai) with a system prompt that forbids ungrounded answers, and rejects any response whose citations don't match the retrieved chunks. A unit test asserts that a nonsense question ("what is the meaning of life") returns `grounded: false`.
