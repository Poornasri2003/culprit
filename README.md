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

## Inputs and outputs

This section shows every entry point with what goes in and what comes out. The outputs are real, from our runs.

### 1. The demo app with the bug (`sample_app/backend`)

**Input:**
```bash
curl -X POST http://localhost:8000/cart/total \
  -H "Authorization: Bearer culprit-demo-token" -H "Content-Type: application/json" \
  -d '{"items":[{"price":20,"qty":2}],"discount_code":"SAVE10"}'
```

**Output:**

| When | Response | Status |
|---|---|---|
| Before the fix | `{"total": 32.4}` | ❌ Wrong. The rule is (40 × 1.08) − 10 = 33.20 |
| After the fix and a server restart | `{"total": 33.2}` | ✅ Correct |
| With a missing or wrong token | `401 {"error": "Unauthorized"}` | |

### 2. `culprit debug` (CLI)

**Input:**

| Flag | Meaning | Example |
|---|---|---|
| `--issue` (required) | The bug, in one sentence | `"cart total wrong when discount code applied"` |
| `--folder` (1–6, repeatable) | A code folder, one per codebase | `demo_workspace/sample_app/shared` |
| `--url` | Live endpoint to probe (optional) | `http://localhost:8000/cart/total` |
| `--method`, `--body` | How to call the URL | `POST`, `'{"items":[...],"discount_code":"SAVE10"}'` |
| `--auth-bearer` / `--no-auth` | Auth for the probe (the token comes from `BEARER_TOKEN` in `.env`) | |
| `--dry-run` | Analyse only: report the fix, change nothing | |
| `--json-report PATH` | Also write the final report as JSON | `report.json` |

**Output (terminal, abbreviated):** in the real output, the fix diff appears in a framed, syntax-highlighted box.
```
   0.0s  🔁 Attempt 1/3
   0.0s    🌐 Probing live POST http://localhost:8000/cart/total…
   0.3s       ↳ HTTP 200: {"total":32.4}
   0.3s    🔍 Running Reproducer + CauseTracer in parallel…
  37.7s    🧪 Writing and running repro test…
  38.6s    ✏️  Running FixAuthor…
  47.5s    🛡  Running Guard…
  61.6s    🔧 Applying fix…
  61.6s    🧪 Running full test suite…
  62.9s    ✅ Tests passed — committing…
──────────────── Culprit Report ────────────────
  Status:       FIXED
  Elapsed:      63.3s
  Bobcoins:     0.2089
  Root cause:   pricing.py:28  [shared]  confidence=99%
  Fix diff:     -    discounted_subtotal = subtotal - discount
                -    total = discounted_subtotal * (1 + tax_rate)
                +    total = subtotal * (1 + tax_rate) - discount
  Tests:        PASSED
  Commit SHA:   2ba122aacc5ff13964c665d03fbf7c83563d2695
```

**Other outputs:**
- **Exit code:** `0` when FIXED, `1` otherwise (NEEDS_HUMAN or PARTIAL).
- **Side effects when FIXED:**
  - A git commit in the target repo, containing the fixed source file and `tests/test_culprit_regression.py`.
  - The temporary repro test is deleted.

**Possible statuses:**

| Status | Meaning |
|---|---|
| `FIXED` | The fix is applied, every test passes, and it's committed |
| `NEEDS_HUMAN` | The bug couldn't be reproduced (the repro test passes, E9), confidence was below 0.70 (E5), or all 3 attempts failed |
| `PARTIAL` | `--dry-run` was used, or the Bobcoin budget ran out (E4) |

### 3. JSON report (`--json-report`)

```json
{
  "status": "FIXED",
  "bug": { "description": "cart total wrong when discount code applied", "runtime_evidence": [], "static_evidence": ["..."] },
  "root_cause": {
    "codebase": "shared", "file": "pricing.py", "line": 28, "symbol": "compute_cart_total",
    "explanation": "The discount is subtracted before tax is applied ...", "confidence": 0.99
  },
  "fix": {
    "target_codebase": "shared",
    "edits": [ { "file": "pricing.py", "old_text": "...", "new_text": "..." } ],
    "unified_diff": "--- a/pricing.py\n+++ b/pricing.py\n@@ ...",
    "applied_files": ["pricing.py"]
  },
  "regression_test": { "codebase": "shared", "file": "tests/test_culprit_regression.py", "test_name": "...", "code": "..." },
  "attempts": [ { "num": 1, "subagent_outputs": {}, "test_result": { "passed": true, "output": "...", "failed_tests": [] }, "failure_reason": null } ],
  "elapsed_seconds": 63.3,
  "bobcoins_used": 0.2089,
  "commit_sha": "2ba122aacc5ff13964c665d03fbf7c83563d2695"
}
```
Tokens never appear in the report, because `AuthContext.token` is excluded from serialisation.

### 4. `culprit serve` HTTP API (used by the Orchestrate tools)

Every `/debug` call needs `Authorization: Bearer <CULPRIT_API_TOKEN>`.

| Endpoint | Input | Output |
|---|---|---|
| `GET /health` | none | `{"status": "ok"}` |
| `POST /debug` | `{"issue": "Customers say the cart total is wrong when they use the SAVE10 discount code"}` (5–500 chars; no other field is accepted) | `202 {"job_id": "77852e466795", "status": "running", "message": "Culprit started. A run takes about 60 seconds; ..."}` |
| `GET /debug/{job_id}` | the `job_id` | While running: `{"status": "running", "progress": [...], "summary": "Culprit is still working..."}`. When done: see below |

When done, `GET /debug/{job_id}` returns this (real response through the public tunnel):
```json
{
  "job_id": "77852e466795",
  "status": "done",
  "elapsed_seconds": 74.1,
  "progress": ["0.3s  ↳ HTTP 200: {\"total\":32.4}", "...", "72.4s  ✅ Tests passed — committing…"],
  "culprit_status": "FIXED",
  "root_cause": "shared/pricing.py:28 (compute_cart_total): The docstring ... states the correct business rule ...",
  "fix_diff": "--- a/pricing.py\n+++ b/pricing.py\n...",
  "tests_passed": true,
  "commit_sha": "3e6c3f1d53ae961ae465af2e9a8ebaa8507339a3",
  "bobcoins_used": 0.181,
  "summary": "FIXED in 73s for 0.181 Bobcoins. Root cause: shared/pricing.py line 28. All tests pass; fix and regression test committed (3e6c3f1)."
}
```

| Error | When |
|---|---|
| `401` | Missing or wrong token |
| `503` | `CULPRIT_API_TOKEN` is not set on the server |
| `409` | A run is already in progress |
| `404` | Unknown `job_id` |
| `422` | `issue` is too short or too long |

### 5. watsonx Orchestrate agent and tools

| Tool (in `orchestrate/culprit_openapi.yaml`) | Input | Output |
|---|---|---|
| `start_culprit_debug` | `issue` (string) | `job_id`, `status`, `message`, the same as `POST /debug` |
| `get_culprit_debug_result` | `job_id` (string) | The result object from section 4 |

The agent `culprit_oncall` (in `orchestrate/culprit_agent.yaml`) works like this:
- **Input:** a chat message describing the bug.
- **What it does:** calls `start_culprit_debug` and replies with the job id. When asked for the status, it calls `get_culprit_debug_result`.
- **Output:** it reports the Culprit status, the root cause file and line, whether the tests passed, the commit SHA, the time taken and the Bobcoins used.

### 6. IBM Bob subagents (inside Culprit, through Bob Shell)

**How Culprit calls Bob:** `bob run --format json --mode ask --max-cost <remaining> --max-turns 8 -w <project root> "<prompt>"`

**What Bob Shell returns:** one JSON line.
```json
{"type":"result","status":"success","stats":{"session_costs":0.0368},"last_message":"<answer, often inside a json code fence>"}
```

| Subagent | Input (in its prompt) | Output (JSON that Culprit validates) |
|---|---|---|
| **Reproducer** | Issue text, redacted live evidence, the codebase list, and the prior failure (if any) | `{"file": "tests/test_culprit_repro.py", "code": "<pytest that FAILS on current code>"}` |
| **CauseTracer** | Issue text, redacted live evidence, the codebase list, and the prior failure (if any) | `{"codebase": "shared", "file": "pricing.py", "line": 28, "symbol": "compute_cart_total", "explanation": "...", "confidence": 0.99}` |
| **FixAuthor** | The root cause, the codebase list, and the prior failure (if any) | `[{"target_codebase": "shared", "edits": [{"file": "pricing.py", "old_text": "<exact, unique>", "new_text": "..."}]}]` (up to 3, lowest risk first) |
| **Guard** | The root cause and the chosen fix | `{"codebase": "shared", "file": "tests/test_culprit_regression.py", "test_name": "...", "code": "<permanent pytest>"}` |

### 7. Scripts

| Script | Input | Output |
|---|---|---|
| `scripts/prepare_demo.py` | none | A fresh, buggy copy at `demo_workspace/sample_app/`, set up as its own git repo with one baseline commit |
| `scripts/run_backend.py` | `BEARER_TOKEN` from `.env` | The demo backend running on `http://localhost:8000` |
| `scripts/orchestrate_deploy.py` | `--tunnel-url`, optional `--llm` / `--list-models`, and `WO_INSTANCE_URL` + `WO_API_KEY` from `.env` | A bearer connection `culprit_api`, the two tools, and the agent `culprit_oncall`, all deployed in your Orchestrate instance (secrets are printed as `***`) |

### 8. Environment variables (`.env`)

| Variable | Needed for | Where it comes from |
|---|---|---|
| `BOB_API_KEY` | Culprit calling Bob Shell (**required**) | bob.ibm.com, then **API keys**, with Inference scope |
| `BEARER_TOKEN` | The demo backend and `--auth-bearer` | Any value; the demo uses `culprit-demo-token` |
| `CULPRIT_API_TOKEN` | `culprit serve` | Generated by `orchestrate_deploy.py`, or any random string |
| `WO_INSTANCE_URL`, `WO_API_KEY` | Deploying the Orchestrate agent (optional) | Orchestrate, under **Settings** and then **API details** |

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
