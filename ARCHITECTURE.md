# Compass — Smart Developer Onboarding Assistant

**Hackathon:** IBM Bob 2.0 (lablab.ai)
**Track:** Smart developer onboarding assistant (Appendix example #1)
**Stack:** IBM Bob Shell (required) · watsonx Orchestrate (workflow) · watsonx.ai (Q&A grounding, optional)

---

## 1. Elevator pitch

Compass points at any Git repository — GitHub, GitLab, Bitbucket, self-hosted, SSH, or a local folder — and produces an **onboarding pack**: an architecture map, a working local dev-loop, a guided tour, three starter tasks, and a `/ask` endpoint that answers questions about the code with file/line citations. It turns "first productive commit in weeks" into "first productive commit in hours."

## 2. Problem

Every developer joining a new team burns days to weeks on the same friction:

- The README is out of date or missing.
- The install and test commands in the docs don't actually run on a clean machine.
- The architecture lives in a senior engineer's head.
- No one has time to hand-hold a newcomer through a 200-file repo.
- The first "good first issue" the team labels is either trivial or a trap.

The cost is enormous and invisible: onboarding delays are absorbed by the newcomer, not measured.

## 3. What Compass does (input → output)

### Input

| Field | Required | Example |
|---|---|---|
| `repo` | yes | `https://github.com/acme/checkout-service`, `git@gitlab.com:acme/svc.git`, `ssh://git@self-hosted/…`, `D:\work\checkout`, `./relative/path` |
| `role` | no | `backend` \| `frontend` \| `sre` \| `full-stack` |
| `starter_difficulty` | no | `easy` (default) \| `medium` |
| `output_dir` | no | `./onboarding` (default) |

**Any Git source works.** Whatever `git clone` accepts, Compass accepts. Local paths are hard-linked into an isolated workspace so Compass never mutates your working copy.

### Output (an `onboarding/` folder)

| File | What's in it |
|---|---|
| `OVERVIEW.md` | One-page project summary: what it is, who uses it, the top-level shape. |
| `ARCHITECTURE.md` | Modules, boundaries, key call paths, and a Mermaid diagram Compass **verified** by tracing imports/calls in code. |
| `DEV_LOOP.md` | Install → run → test → lint, with the **exact commands Compass actually ran** and their outcomes. No aspirational commands. |
| `TOUR.md` | A 30-minute reading tour of 6–10 files, each with "why it matters" and "what to notice." |
| `STARTER_TASKS.md` | 3 ranked tasks with file pointers, acceptance criteria, and hint chains. |
| `notes/*.json` | Machine-readable versions of the above, for the Orchestrate `/ask` tool to ground its answers. |

Plus a running Orchestrate-hosted endpoint: `POST /ask { question }` → grounded answer with `[file:line]` citations.

## 4. Architecture

### 4.1 Component diagram

```
┌───────────────────────────────────────────────────────────────────────┐
│                          User surfaces                                │
│                                                                       │
│   CLI:  compass demo                                                  │
│         compass run --repo <url|path>                                 │
│   API:  POST /onboard    (kicks off a run)                            │
│         POST /ask        (grounded Q&A over an onboarded repo)        │
│   Orchestrate:  "New teammate joined" workflow calls /onboard         │
│                 and posts the pack to Slack/Teams                     │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│                         Orchestrator                                  │
│  Plans the run, calls subagents in order, aggregates their outputs    │
│  into a single OnboardingPack, writes files, and returns a report.    │
└─┬──────────────┬──────────────┬──────────────┬────────────────────────┘
  │              │              │              │
  ▼              ▼              ▼              ▼
┌──────────┐ ┌──────────────┐ ┌───────────┐ ┌────────────────┐
│  Scout   │ │ Cartographer │ │ DevLoop   │ │     Guide      │
│  Bob     │ │    Bob       │ │ Runner    │ │      Bob       │
│ subagent │ │  subagent    │ │ (tool +   │ │   subagent     │
│          │ │              │ │  Bob)     │ │                │
│ inventory│ │ modules,     │ │ install,  │ │ tour + starter │
│ languages│ │ boundaries,  │ │ build,    │ │ tasks, hints,  │
│ layout   │ │ call paths   │ │ test,     │ │ acceptance     │
│          │ │              │ │ lint      │ │ criteria       │
└────┬─────┘ └──────┬───────┘ └─────┬─────┘ └────────┬───────┘
     │              │               │                │
     └──────────────┴───────────────┴────────────────┘
                              │
                              ▼
                    ┌────────────────────┐
                    │ Infrastructure     │
                    │ ───────────────    │
                    │ git_ops            │
                    │ workspace (sandbox)│
                    │ command_runner     │
                    │ file_index         │
                    │ bob_shell_client   │
                    │ orchestrate_client │
                    └────────────────────┘
```

### 4.2 The four subagents (each is a Bob Shell session with a focused prompt and tool allowlist)

**Scout — repo inventory.**
Walks the tree, counts languages, detects package managers, spots entry points (`main`, `manage.py`, `index.ts`, `Dockerfile`, `Makefile`, `pyproject.toml`, `package.json`), and emits a `RepoInventory` JSON. Read-only.

**Cartographer — architecture map.**
Given the inventory, samples the most-imported/most-called files, follows references across module boundaries, and produces `ArchitectureMap` (modules, edges between them, and 3–5 "spine" call paths from HTTP-in to storage-out). Renders a Mermaid diagram. Read-only.

**DevLoopRunner — verify what actually works.**
Not a pure LLM subagent — it's Bob **proposing** commands and a runner **executing** them in a sandboxed workspace, then feeding the exit code and last N lines back. Compass only writes a command into `DEV_LOOP.md` after it has run cleanly. This is what separates Compass from a "docs summarizer."

**Guide — tour + starter tasks.**
Given inventory + architecture + dev-loop, picks 6–10 files for a reading tour (ordered by dependency depth, not file size), writes prose for each, and drafts 3 starter tasks by scanning `TODO`s, missing tests, thin error handling, and stale docstrings. Each task has: title, files to touch, acceptance criteria, and a hint ladder (three progressively larger nudges).

### 4.3 Data flow

```
repo URL/path ──► git_ops.clone() ──► workspace/ ──► Scout ─────► inventory.json
                                                     │
                                                     ▼
                                               Cartographer ────► architecture.json (+ .mmd)
                                                     │
                                                     ▼
                                               DevLoopRunner ───► devloop.json
                                                     │           (only verified commands)
                                                     ▼
                                                   Guide ────────► tour.json + tasks.json
                                                     │
                                                     ▼
                                            Orchestrator ────────► onboarding/*.md
                                                                  + notes/*.json
                                                                  + report.json
```

The `notes/*.json` files stay on disk. When `/ask` is called later, Compass loads them and hands them to Bob (or watsonx.ai) as grounded context, so answers cite real file paths and line numbers instead of hallucinating them.

## 5. IBM Bob usage (this is the required piece)

Compass uses **Bob Shell** as its reasoning engine. Every subagent:

1. Authenticates with `BOB_API_KEY` (scope: Inference).
2. Opens a Bob Shell session with a **subagent-specific system prompt** (Scout's prompt is different from Guide's — narrow scope = better outputs).
3. Iterates: Bob proposes a tool call → Compass executes it → Compass returns the observation → Bob continues until it emits a final JSON payload matching the subagent's Pydantic schema.
4. Compass validates the JSON against the schema. On mismatch, it retries once with the validation error appended.

Tool surface exposed to Bob (Compass implements each as a Python function):

| Tool | Who uses it | What it does |
|---|---|---|
| `list_dir(path)` | Scout, Cartographer, Guide | Directory listing, respects `.gitignore`. |
| `read_file(path, start, end)` | all | Bounded reads to keep token cost predictable. |
| `search(pattern, glob)` | Cartographer, Guide | Ripgrep-backed. |
| `run_command(cmd, timeout)` | DevLoopRunner only | Sandboxed shell, capped output. |
| `find_references(symbol)` | Cartographer | Cross-file symbol resolution. |
| `git_log(path)` | Guide | To identify hot files worth touring. |

The tool allowlist is enforced per subagent — Scout cannot run shell commands, DevLoopRunner cannot invent files, etc. This is a structural safety property, not a prompt request.

## 6. watsonx Orchestrate integration

Two things live in Orchestrate:

**A workflow — "New teammate joined."** Trigger: HR adds a person to a team. Actions:
1. Call Compass's `POST /onboard` with the team's repo.
2. Wait for the pack.
3. Post `OVERVIEW.md` and a link to the full pack in the person's Slack/Teams DM.
4. Create a calendar hold titled "Compass tour" on day 2, with `TOUR.md` in the description.

**A skill — "Ask this repo."** Exposes `POST /ask` as a natural-language tool. The new dev can say to their Orchestrate assistant: *"How does auth work in the checkout service?"* → Orchestrate calls `/ask` → Compass answers with citations.

Both are defined in `orchestrate/compass_agent.yaml` and `orchestrate/compass_openapi.yaml`, and deployed with `scripts/orchestrate_deploy.py`.

## 7. watsonx.ai integration (optional, judge-friendly)

For `/ask`, Compass retrieves the top-k relevant chunks from `notes/*.json` (BM25 is plenty at hackathon scale) and calls a watsonx.ai foundation model with a strict "answer only from the provided context, cite file:line, refuse if not found" prompt. This lets us claim watsonx.ai usage without pretending it's doing something Bob couldn't. Bob does the heavy lifting on the codebase; watsonx.ai handles the conversational layer.

## 8. Directory layout

```
Culprit/                              (working directory; project name: Compass)
├── ARCHITECTURE.md                   (this file)
├── README.md                         (elevator pitch + run-it steps for judges)
├── SPEC.md                           (schemas and prompts, one section per subagent)
├── .env.example                      (BOB_API_KEY, COMPASS_API_TOKEN, WO_INSTANCE_URL)
├── pyproject.toml
├── src/compass/
│   ├── __main__.py                   (python -m compass)
│   ├── cli.py                        (compass demo, run, serve, ask)
│   ├── config.py
│   ├── domain/                       (Pydantic models: RepoInventory,
│   │                                  ArchitectureMap, DevLoop, Tour, StarterTask,
│   │                                  OnboardingPack, AskAnswer)
│   ├── application/
│   │   ├── orchestrator.py           (drives the 4 subagents)
│   │   ├── report_builder.py         (JSON → Markdown)
│   │   ├── demo.py                   (canned pack for `compass demo`)
│   │   └── ask.py                    (grounded Q&A)
│   ├── subagents/
│   │   ├── base.py                   (Bob Shell session + retry + schema validation)
│   │   ├── scout.py
│   │   ├── cartographer.py
│   │   ├── devloop_runner.py
│   │   └── guide.py
│   ├── infrastructure/
│   │   ├── bob_client.py             (Bob Shell HTTP client + CannedBobClient)
│   │   ├── git_ops.py
│   │   ├── workspace.py              (sandboxed tmp dir per run)
│   │   ├── command_runner.py         (timeout + output cap + denylist)
│   │   ├── file_index.py             (ripgrep + gitignore)
│   │   └── orchestrate_client.py
│   └── server.py                     (FastAPI: /onboard, /ask, /healthz)
├── orchestrate/
│   ├── compass_agent.yaml
│   └── compass_openapi.yaml
├── scripts/
│   ├── orchestrate_deploy.py
│   └── prepare_demo.py               (clones a demo repo for the recording)
└── tests/
    ├── test_smoke.py
    ├── test_report_builder.py
    ├── test_command_runner.py
    └── test_orchestrator.py
```

## 9. Demo flow (3 minutes, what the judges see)

1. `compass demo --output-dir ./onboarding_demo` — instant, no Bob call, writes a full onboarding pack from the canned fixture so the judge can *see the artifacts* before we call Bob for real.
2. `compass run --repo https://github.com/pallets/flask` — a repo the judge knows but the presenter "hasn't seen." Terminal shows the 4 subagents running: Scout ✓ → Cartographer ✓ → DevLoopRunner (live commands scrolling) ✓ → Guide ✓.
3. Open `onboarding/` in VS Code. Walk through `OVERVIEW.md`, `ARCHITECTURE.md` (Mermaid diagram renders), `DEV_LOOP.md` (point at a real command that ran), `STARTER_TASKS.md`.
4. Switch to Slack with Orchestrate wired in: ask *"Where does routing happen?"* → grounded answer with a `flask/app.py:L###` citation.
5. Close by pointing at total wall-clock time (< 3 min) and total Bob token cost (from the run report).

## 10. What "done" looks like (submission checklist)

- [ ] `compass demo` writes a full pack from the canned fixture (already passes today).
- [ ] `compass run` produces the full pack on 3 seed sources (Flask on GitHub, a small Rust CLI on GitLab, a local Python folder).
- [ ] `compass serve` exposes `/onboard` and `/ask`; tokenized with `COMPASS_API_TOKEN`.
- [ ] Orchestrate agent deployed to the trial instance; skill answers via `/ask`.
- [ ] README with 5-minute quickstart, screenshots, and a Loom link.
- [ ] MIT license (lablab requirement).
- [ ] `bob_sessions/` folder with saved transcripts of the four subagents on the demo repo, for reproducibility.

## 11. Risks and how we handle them

| Risk | Mitigation |
|---|---|
| DevLoopRunner runs untrusted code from a stranger's repo. | Sandboxed workspace, no network by default, 60s per-command timeout, output capped at 4 KB, deny-list on destructive commands (`rm -rf /`, `sudo`, `shutdown`, fork bombs, `curl … \| sh`, etc.). |
| Bob token cost balloons on huge repos. | Hard budget per subagent (default 50k tokens), Scout produces a size estimate first, orchestrator refuses runs above a configurable cap unless `--yes-large`. |
| Cartographer hallucinates architecture. | Every claim in the diagram must be backed by a `find_references` or `search` result recorded in `architecture.json`; the report builder drops unbacked claims. |
| `/ask` invents citations. | Grounded on `notes/*.json` only; if no matching chunk, the answer is "I don't know from this repo" — measured and enforced by a unit test. |

## 12. Roadmap after the hackathon

- Multi-repo mode (monorepo + microservices onboarding at once).
- "Onboarding diff": rerun on a repo the newcomer has been in for two weeks; show what changed and what they should re-read.
- IDE extension: open `TOUR.md` as a walkthrough inside VS Code with jump-to-file.
- Team memory: store `notes/*.json` in a vector store so `/ask` gets better over time as the team asks more questions.
