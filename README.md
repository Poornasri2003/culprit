# Culprit

**An AI debugger that reproduces a bug on a live service, traces it across codebases, and commits a tested fix. It is powered by IBM Bob.**

You give Culprit a running endpoint, the code folders behind it, and one sentence describing the bug. Culprit then does the following:

1. It **probes the live endpoint** to capture real runtime evidence, meaning the actual response.
2. It **reproduces** the bug as a failing pytest and **traces** the root cause across all the codebases.
3. It **writes candidate fixes**, which a second model (watsonx.ai Granite) can review before anything is applied.
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
┌─────────────────────────────────────────────────────────────────┐
│  culprit debug  --url --method --body --auth-bearer --folder… │  CLI (Click + Rich)
├─────────────────────────────────────────────────────────────────┤
│  Orchestrator: self-healing loop, max 3 attempts, 240 s budget  │
│                                                                 │
│   probe URL ─► Reproducer ‖ CauseTracer ─► FixAuthor            │  IBM Bob (Bob Shell)
│                    │                          │                 │
│         repro test must FAIL        watsonx.ai Granite reviews  │  optional second opinion
│         on current code (E9)        each candidate (fail-open)  │
│                                               │                 │
│               Guard writes regression test ◄──┘                 │
│                    │                                            │
│          apply fix ─► run all tests ─► commit   or   revert     │  deterministic code
└─────────────────────────────────────────────────────────────────┘
```

| Part | What decides | Why |
|---|---|---|
| Reproducer, CauseTracer, FixAuthor, Guard | **IBM Bob**, called headless through Bob Shell (`bob run --format json --mode ask`) | Reading several repositories, mapping an HTTP response to source lines, writing tests and edits |
| Adjudicator | **watsonx.ai Granite** scores correctness, safety and minimalism; **code** computes the total and the APPROVE or REJECT verdict | An independent second opinion on Bob's fix |
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

### Optional: the watsonx.ai adjudicator

Set `IBM_CLOUD_API_KEY`, `WATSONX_PROJECT_ID`, `WATSONX_URL` and `WATSONX_MODEL_ID` in `.env`. Without these values, Culprit prints "watsonx not configured" and continues, so the adjudicator never blocks a run.

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
  application/                orchestrator.py (the loop), report_builder.py
  domain/                     Pydantic models and exceptions
  subagents/                  Reproducer, CauseTracer, FixAuthor, Guard (Strategy) and their factory
  infrastructure/             bob_client, watsonx_client, http_client, auth, workspace, test_runner, git_ops
tests/                        150 unit tests, with no network, no Bob and no cost
sample_app/                   the demo app with its seeded bug
scripts/                      prepare_demo.py, run_backend.py
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
5. The watsonx client
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
