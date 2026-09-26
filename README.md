# Culprit

**An AI debugger that reproduces a bug on a live service, traces it across codebases, and commits a tested fix. It is powered by IBM Bob.**

You give Culprit a running endpoint, the code folders behind it, and one sentence describing the bug. Culprit then does the following:

1. It **probes the live endpoint** to capture real runtime evidence, meaning the actual response.
2. It **reproduces** the bug as a failing pytest and **traces** the root cause across all the codebases.
3. It **writes up to three candidate fixes**, ranked from lowest to highest risk, and takes the lowest-risk one.
4. It **applies the fix, runs the full test suite**, and **commits** the fix together with a permanent regression test. If the tests fail, it reverts the fix and tries again.

```
$ python -m culprit debug --url http://localhost:8000/cart/total --method POST \
    --body '{"items":[{"price":20,"qty":2}],"discount_code":"SAVE10"}' --auth-bearer \
    --folder demo_workspace/sample_app/frontend --folder demo_workspace/sample_app/backend \
    --folder demo_workspace/sample_app/shared --issue "cart total wrong when discount code applied"

   0.1s    🌐 Probing live POST http://localhost:8000/cart/total…
   0.4s       ↳ HTTP 200: {"total":32.4}
   0.4s    🔍 Running Reproducer + CauseTracer in parallel…
  25.1s    🧪 Writing and running repro test…
  28.4s    ✏️  Running FixAuthor…
  43.1s    🛡  Running Guard…
  58.3s    🔧 Applying fix…
  58.3s    🧪 Running full test suite…
  62.1s    ✅ Tests passed — committing…

  Status:     FIXED        Elapsed: 62.8s        Bobcoins: 0.21
  Root cause: shared/pricing.py:28  confidence=98%
  Fix:        -    discounted_subtotal = subtotal - discount
              -    total = discounted_subtotal * (1 + tax_rate)
              +    total = subtotal * (1 + tax_rate) - discount
```

## Why it matters

Most AI debuggers only read code. Many real bugs, though, are only visible at runtime: the service returns `200 OK` with a wrong value, and the cause sits in a library that another service imports. Culprit combines **runtime evidence**, from a real authenticated HTTP call, with **static evidence**, from every codebase involved. It also trusts only one result: a fix that makes a test pass which previously failed.

## How it works

```
 ENTRY POINTS
 ┌──────────────────────────────────────────┐      ┌──────────────────────────────┐
 │ watsonx Orchestrate chat                 │      │ Terminal                     │
 │  AGENT: culprit_oncall                   │      │  $ culprit debug --url ...   │
 │   TOOL start_culprit_debug(issue)        │      │    (Click + Rich live view)  │
 │   TOOL get_culprit_debug_result(job_id)  │      └──────────────┬───────────────┘
 └──────────────────┬───────────────────────┘                     │
                    │ HTTPS + bearer token (Cloudflare tunnel)    │
                    ▼                                             │
 ┌──────────────────────────────────────────┐                     │
 │ culprit serve  (FastAPI, async jobs)     │                     │
 └──────────────────┬───────────────────────┘                     │
                    ▼                                             ▼
 CULPRIT ORCHESTRATOR (plain Python: loop of max 3 attempts, 240 s, Bobcoin cap)
 ┌────────────────────────────────────────────────────────────────────────────┐
 │ 1. TOOL HttpClient.probe ──► live evidence: HTTP 200 {"total": 32.4}       │
 │                                                                            │
 │ 2. SUBAGENT Reproducer  ‖  SUBAGENT CauseTracer        (in parallel)       │
 │      failing pytest          root cause + confidence                       │
 │                                                                            │
 │ 3. TOOL TestRunner: the repro test must FAIL on the current code           │
 │                                                                            │
 │ 4. SUBAGENT FixAuthor ──► up to 3 fixes (exact search/replace edits)       │
 │                                                                            │
 │ 5. SUBAGENT Guard ──► permanent regression test                            │
 │                                                                            │
 │ 6. TOOL GitOps.apply ─► TOOL TestRunner (all tests) ─► TOOL GitOps.commit  │
 │                                  └── tests fail? GitOps.revert, retry      │
 └────────────────────────────────────────────────────────────────────────────┘
          │ each SUBAGENT = one headless IBM Bob run
          ▼
 IBM BOB SHELL:  bob run --format json --mode ask --max-cost <budget> -w <project>
   Bob's own read-only TOOLS: list files, read files, search code
   (ask mode: Bob can read the code but never write it; Culprit applies the edits)
```

### Agent, subagent, tool: what each word means here

| Term | In Culprit | Who runs it |
|---|---|---|
| **Agent** | `culprit_oncall`, a chat assistant in watsonx Orchestrate. It understands the user's bug report and decides which tool to call | watsonx Orchestrate (an LLM) |
| **Tool** (Orchestrate) | `start_culprit_debug` and `get_culprit_debug_result`, the two actions the agent can take. They're defined in `orchestrate/culprit_openapi.yaml` and call `culprit serve` | Culprit's HTTP API |
| **Orchestrator** | The boss inside Culprit. Plain Python that runs the steps in order, enforces limits, and decides to commit or revert. It contains no AI | Python |
| **Subagent** | One specialist job given to Bob: **Reproducer**, **CauseTracer**, **FixAuthor** or **Guard**. Each gets its own prompt and returns strict JSON | IBM Bob, through Bob Shell |
| **Tool** (Culprit) | Plain-code actions the orchestrator uses: `HttpClient` (probe the live URL), `TestRunner` (pytest), `GitOps` (apply, revert, commit) | Python |
| **Tool** (Bob) | Bob's built-in abilities during a subagent run: list, read and search files. They're read-only in ask mode | IBM Bob |

| Part | What decides | Why |
|---|---|---|
| Reproducer, CauseTracer, FixAuthor, Guard | **IBM Bob**, called headless through Bob Shell (`bob run --format json --mode ask`) | Reading several repositories, mapping an HTTP response to source lines, writing tests and edits |
| Keep or revert a fix | **The test suite**: the repro test must fail before the fix, and every test must pass after it | An objective judge that needs no second model |
| Loop, budget, HTTP, auth, applying edits, tests, git | **Plain Python**, with no AI involved | These steps must be predictable and auditable |

Culprit's safety rules:
- Bob runs in read-only **ask** mode, and Culprit applies the edits itself. Each edit is an exact search-and-replace, and an edit whose text doesn't match the file exactly once is rejected.
- A repro test counts only if it **fails on an assertion**. A test that crashes on import proves nothing.
- A fix is committed only if **all tests pass**. Otherwise the files are restored from backup.
- A hard **Bobcoin cap** applies per run, enforced by both Culprit and Bob (`--max-cost`).
- Secrets live only in `.env`. Tokens are excluded from every report, and `Authorization`, `X-API-Key` and `Cookie` values are redacted before any text reaches Bob or watsonx.

## Quick start (Windows or macOS/Linux)

Prerequisites: Python 3.11 or later, Node.js 22.15 or later, [Bob Shell](https://bob.ibm.com/docs/shell/getting-started/install-and-setup), and a Bob API key with **Inference** scope.

```bash
pip install -e .
cp .env.example .env        # then set BOB_API_KEY (and optionally the watsonx values)

python scripts/prepare_demo.py   # fresh, broken copy of the demo app in demo_workspace/ (its own git repo)
python scripts/run_backend.py    # terminal 1: demo backend on http://localhost:8000
```

In terminal 2, run the `culprit debug …` command shown at the top of this README.

- Add `--dry-run` to analyse and review the fix without applying or committing anything.
- Leave out `--url` to debug from code alone.
- Run `python scripts/prepare_demo.py` again to reset the demo.

## Trigger it from chat with watsonx Orchestrate

An on-call engineer shouldn't need a terminal. Culprit ships a **watsonx Orchestrate** agent, `culprit_oncall`, with two tools that run Culprit from chat:

> 👤 *"Customers say the cart total is wrong when they use the SAVE10 discount code."*
> 🤖 calls `start_culprit_debug`, then `get_culprit_debug_result`, and reports the result.

```
Orchestrate chat ─► agent culprit_oncall ─► tools start_culprit_debug / get_culprit_debug_result
                                                  │ (OpenAPI tools + bearer connection)
                                                  ▼
                            Cloudflare tunnel ─► culprit serve (FastAPI) ─► culprit debug ─► Bob Shell
```

- The API is **asynchronous**, because a run (about 60 s) exceeds Orchestrate's 40 s limit for synchronous tools. One call starts the run, and a second fetches the result.
- It is **bearer-token protected**. Callers choose **only the issue text**: the project, the URL and the folders are fixed on the server side.
- The Orchestrate developer kit (ADK) lives in its own `.venv-orchestrate/`, apart from Culprit's own dependencies.

**What we verified, and what we didn't:**

| Part | Status |
|---|---|
| `culprit serve` and both tool endpoints, called over a public Cloudflare tunnel | ✅ Verified end to end. `start_culprit_debug` returned a job id, and `get_culprit_debug_result` returned FIXED in 73 s for 0.18 Bobcoins with all tests passing |
| Agent and tool definitions (`orchestrate/`), connection setup and deploy script | ✅ Written against the ADK 2.17 docs. The YAML is validated, and all 12 API tests pass |
| Deploying the agent into a live Orchestrate instance and chatting with it | ⚠️ **Not run by us**. It needs an Orchestrate API key, which we couldn't generate before the deadline. Follow the steps below with your own instance |

### Run it with your own watsonx Orchestrate instance

Prerequisites: the Quick start above works, plus [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and a watsonx Orchestrate instance. The free 30-day trial works.

1. Get your instance values. In Orchestrate, click the **profile icon**, then **Settings** and **API details**. Copy the **service instance URL** and **Generate API key**. On AWS/MCSP instances, the button opens the IBM SaaS Console: go to **Subscriptions**, then **View instances**, open your instance, and generate the key there.
2. Put them in `.env`:
   ```
   WO_INSTANCE_URL=https://api.<region>.watson-orchestrate.ibm.com/instances/<id>
   WO_API_KEY=<your Orchestrate API key>
   ```
   Note that a Bob API key does **not** work here: Orchestrate returns `Error getting MCSP_V2 Token`.
3. Run:
   ```bash
   python -m venv .venv-orchestrate && .venv-orchestrate/Scripts/pip install ibm-watsonx-orchestrate
   python scripts/prepare_demo.py && python scripts/run_backend.py      # terminal 1: demo backend
   python -m culprit serve --port 8080                                   # terminal 2: Culprit API
   cloudflared tunnel --url http://localhost:8080                        # terminal 3: copy the https URL
   python scripts/orchestrate_deploy.py --list-models                    # optional: choose a model
   python scripts/orchestrate_deploy.py --tunnel-url https://<name>.trycloudflare.com [--llm <model>]
   ```
   The deploy script connects the ADK to your instance and generates `CULPRIT_API_TOKEN` in `.env` if it is missing. It stores that token in a bearer connection, imports both tools pointing at your tunnel, then imports and deploys the agent.
4. In Orchestrate, open **Chat**, pick **culprit_oncall**, and report the bug. Ask for the status after about a minute.

## The demo scenario

`sample_app/` is a small multi-service shop:

| Folder | Contents |
|---|---|
| `shared/` | The pricing library |
| `backend/` | A Flask API with bearer auth |
| `frontend/` | A Flask UI |
| `tests/` | The existing test suite |

The documented business rule says a fixed discount code (for example `SAVE10`) comes off the **post-tax** total. The code subtracts it **before** tax, so the store undercharges on every discounted order: $32.40 instead of $33.20. The existing tests never cover discounts, so they all pass.

## Project layout

```
src/culprit/
  cli.py                      Click CLI with the Rich live progress and report
  server.py                   HTTP API for watsonx Orchestrate (culprit serve)
  application/                orchestrator.py (the loop), report_builder.py
  domain/                     Pydantic models and exceptions
  subagents/                  Reproducer, CauseTracer, FixAuthor, Guard (Strategy) and their factory
  infrastructure/             bob_client, http_client, auth, workspace, test_runner, git_ops
tests/                        127 unit tests, with no network, no Bob and no cost
sample_app/                   the demo app with its seeded bug
scripts/                      prepare_demo.py, run_backend.py, orchestrate_deploy.py
orchestrate/                  Orchestrate agent and OpenAPI tool definitions
SPEC.md, ARCHITECTURE.md      the specification and design Culprit was built from
bob_sessions/                 exported IBM Bob task sessions and screenshots (hackathon requirement)
```

Run the unit tests with `python -m pytest tests`.

## Built with IBM Bob

Culprit was designed and built in the **Bob IDE**, one task per layer:

1. The architecture, in Plan mode
2. The sample app
3. The domain models
4. The infrastructure
5. A watsonx.ai reviewer client (later removed, since no IBM Cloud account was available)
6. Bob Shell and the subagents
7. The orchestrator and CLI

Each task was reviewed, and every fix was verified against real Bob runs. The exported sessions and cost screenshots are in [`bob_sessions/`](bob_sessions/). At runtime, Culprit itself calls **Bob Shell** for all four subagents, and a full debug run costs about **0.2 Bobcoins**.

## Limits and next steps

- Only the MVP from SPEC §15 is implemented. `--git` and the basic, OAuth2 and API-key auth modes are stubs, and only bearer auth and no-auth work.
- Culprit currently runs Python codebases tested with pytest.
- Next steps: cloning from `--git`, JS/TS test runners, and a GitHub pull request instead of a local commit.

## Security

See [SECURITY.MD](SECURITY.MD). Never commit `.env`. `.gitignore` and `.bobignore` keep credentials out of git and out of Bob's context.

## License

[MIT](LICENSE)
