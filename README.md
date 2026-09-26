# Compass

**Point Compass at any Git repository and get an onboarding pack in minutes.**

Compass is an IBM Bob 2.0 agent that reads an unfamiliar codebase the way a senior engineer would — inventories the code, draws the architecture, verifies the install-run-test loop by actually running it, picks a reading tour, and drafts three ranked starter tasks. It then serves a `/ask` endpoint so new teammates can ask questions and get answers with `[file:line]` citations.

> Hackathon: [IBM Bob 2.0 on lablab.ai](https://lablab.ai/ai-hackathons/ibm-bob-2-hackathon).
> Track: *Smart developer onboarding assistant* (Appendix example #1).

Full design in [ARCHITECTURE.md](ARCHITECTURE.md). Schemas and subagent prompts in [SPEC.md](SPEC.md).

## Works with any Git source

Compass doesn't care where the repo lives. It works with **anything `git clone` can consume**, plus plain local paths:

- GitHub (`https://github.com/…`, `git@github.com:…`)
- GitLab, self-hosted GitLab, Gitea, Bitbucket, Azure DevOps
- Any SSH remote (`ssh://…`, `user@host:path.git`)
- A local checkout (`D:\work\my-service` or `./relative/path`)
- A `.git` bundle file

## What Compass produces

Given a `--repo`, Compass writes an `onboarding/` folder:

- `OVERVIEW.md` — what this project is and who it's for
- `ARCHITECTURE.md` — modules, boundaries, key call paths, Mermaid diagram
- `DEV_LOOP.md` — install / run / test commands **Compass actually ran** (no aspirational docs)
- `TOUR.md` — a 30-minute guided reading of 6–10 files
- `STARTER_TASKS.md` — three ranked tasks with file pointers, acceptance criteria, and hint ladders
- `notes/*.json` — machine-readable versions, used to ground the `/ask` endpoint

## Run it (5-minute quickstart)

```bash
git clone https://github.com/<you>/compass.git
cd compass
python -m venv .venv && . .venv/Scripts/activate     # PowerShell: .venv\Scripts\Activate.ps1
pip install -e .[dev]

cp .env.example .env
# open .env and fill in BOB_API_KEY (and, for Orchestrate, WO_INSTANCE_URL + tokens)

# Demo — no Bob call, no network — writes a real pack from a canned fixture
compass demo --output-dir ./onboarding_demo

# Real run — needs BOB_API_KEY set
compass run --repo https://github.com/pallets/flask --output-dir ./onboarding
compass run --repo D:\work\my-service --output-dir ./onboarding_local

# Serve the /ask endpoint against a pack you built
compass serve --pack ./onboarding --port 8080
curl -H "Authorization: Bearer $COMPASS_API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"question":"Where does routing happen?"}' \
     http://localhost:8080/ask
```

## Entry points

| Command | Input | Output |
|---|---|---|
| `compass demo [--output-dir ./onboarding_demo]` | Nothing. | Writes a full pack from a canned fixture; use this to see the artifacts without spending a Bob token. |
| `compass run --repo <url\|path> [--role backend] [--difficulty easy] [--output-dir ./onboarding]` | Any Git URL or local path. | Writes the six Markdown files and `notes/*.json` under `--output-dir`, plus a `report.json` with wall-clock and token spend. |
| `compass serve --pack <dir> [--port 8080]` | A previously produced pack. | FastAPI server exposing `POST /onboard`, `POST /ask`, `GET /healthz`. Bearer-token auth via `COMPASS_API_TOKEN`. |
| `compass ask --pack <dir> "<question>"` | A pack and a natural-language question. | Prints a grounded answer with `[file:line]` citations. |
| `python scripts/orchestrate_deploy.py` | Env with `WO_INSTANCE_URL` set. | Registers the Compass agent and OpenAPI tool with your watsonx Orchestrate instance. |

## How Compass uses IBM Bob

Compass runs **four Bob Shell subagents** in sequence, each with its own tool allowlist and Pydantic output schema:

1. **Scout** — inventories languages, package managers, and entry points *(read-only)*.
2. **Cartographer** — samples the most-referenced files, follows imports across module boundaries, and emits a verified architecture map *(read-only)*.
3. **DevLoopRunner** — Bob proposes install/build/test commands, Compass runs them in a sandboxed workspace, and only verified commands land in `DEV_LOOP.md` *(read + `run_command`)*.
4. **Guide** — writes the reading tour and drafts three starter tasks *(read-only + `git_log`)*.

The orchestrator drives the four subagents, validates their JSON output against the schemas in [SPEC.md](SPEC.md), retries once on schema failure, and assembles the `onboarding/` pack. Bob transcripts are saved to `bob_sessions/` for reproducibility.

## watsonx Orchestrate

`orchestrate/compass_agent.yaml` and `orchestrate/compass_openapi.yaml` define:

- A **workflow** ("New teammate joined") that calls `POST /onboard`, waits for the pack, and posts it to Slack/Teams.
- A **skill** ("Ask this repo") that exposes `POST /ask` as a natural-language tool.

Deploy both with `python scripts/orchestrate_deploy.py`.

## watsonx.ai (optional)

`/ask` retrieves the top-k relevant chunks from `notes/*.json` (BM25) and calls a watsonx.ai foundation model with a strict "answer only from the provided context, cite `file:line`, refuse if not found" prompt. This is the only place Compass uses a general-purpose LLM — Bob handles everything on the code itself.

## Safety notes

DevLoopRunner runs untrusted code from stranger repos in a **sandboxed workspace**: no network by default, 60-second per-command timeout, 4 KB output cap, and a deny-list on destructive commands (`sudo`, `rm -rf /`, `shutdown`, fork bombs, etc.). Bob token spend is capped per subagent (default 50 k tokens); Compass refuses runs above a configurable total unless `--yes-large` is set.

## Layout

See [ARCHITECTURE.md §8](ARCHITECTURE.md) for the full tree. In short: `src/compass/{domain,application,subagents,infrastructure}`, `orchestrate/*.yaml`, `scripts/`, `tests/`.

## License

MIT — required by lablab submission rules.
