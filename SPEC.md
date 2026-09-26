# Culprit — Product Specification v1.0

## 1. Purpose
Culprit is a Python CLI tool built on IBM Bob 2.0 that debugs live
backend services and multi-codebase projects. Given running endpoints
+ source code, it authenticates, reproduces the failure at runtime,
traces the root cause across codebases, generates a verified fix, and
writes a regression test.

Culprit combines RUNTIME evidence (real HTTP calls, real responses,
real errors) with STATIC evidence (source code across N codebases) —
this is the differentiator vs static-only AI debuggers.

## 2. Command-line interface

    culprit debug [inputs...] --issue "<description>"

    Input flags (at least one code source + optionally a live URL):
      --url URL              Live endpoint to probe (repeatable)
      --method METHOD        HTTP method for the probe (default GET)
      --body TEXT            Request body for the probe (e.g. JSON)
      --folder PATH          Local code folder (repeatable, 1-6)
      --git URL              Remote repo to clone (repeatable, 1-4)

    Auth flags (choose one when --url is used):
      --auth-bearer          Reads BEARER_TOKEN from .env
      --auth-basic           Reads BASIC_USER, BASIC_PASS from .env
      --auth-oauth2          Reads OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET,
                             OAUTH_TOKEN_URL from .env
      --auth-apikey          Reads API_KEY, API_KEY_HEADER from .env
      --no-auth              Explicit for public endpoints

    Behavior flags:
      --issue TEXT           Bug description (required)
      --trace PATH           Optional: path to a stack trace / error log
      --dry-run              Analyze only; no fix applied, no commit

## 3. Hard limits (deterministic — NOT decided by AI)
Defined in src/culprit/config.py:

    MAX_ATTEMPTS = 3
    MAX_WALLCLOCK_SECONDS = 240
    MAX_FILES_PER_SUBAGENT = 25
    MAX_CODEBASES = 6
    MAX_URLS = 3
    MAX_BOBCOIN_PER_RUN = 8
    CONFIDENCE_FLOOR = 0.70
    HTTP_TIMEOUT_SECONDS = 15
    AUTH_TOKEN_TTL_SECONDS = 300

## 4. Domain model (Pydantic — schema-enforced)

    class Endpoint:
        url: str
        method: str
        headers: dict
        expected_status: int
        actual_status: Optional[int]

    class AuthContext:
        scheme: BEARER | BASIC | OAUTH2 | APIKEY | NONE
        token: Optional[str]  # never logged, never in report
        expires_at: Optional[datetime]

    class RuntimeEvidence:
        endpoint: Endpoint
        request_body: Optional[str]
        response_body: str
        response_status: int
        latency_ms: float
        stack_trace_hint: Optional[str]

    class SourceCodebase:
        name: str
        root_path: Path
        language: str
        entry_points: list[str]

    class Bug:
        description: str
        runtime_evidence: list[RuntimeEvidence]
        static_evidence: list[SourceCodebase]

    class RootCause:
        codebase: str
        file: str
        line: int
        symbol: str
        explanation: str
        confidence: float  # 0.0 - 1.0

    class Fix:
        target_codebase: str
        unified_diff: str
        applied_files: list[str]

    class RegressionTest:
        codebase: str
        file: str
        test_name: str
        code: str

    class Attempt:
        num: int
        subagent_outputs: dict
        test_result: Optional[TestResult]
        failure_reason: Optional[str]

    class CulpritReport:
        status: FIXED | PARTIAL | NEEDS_HUMAN
        bug: Bug
        root_cause: Optional[RootCause]   # None if run aborted before tracing
        fix: Optional[Fix]
        regression_test: Optional[RegressionTest]
        attempts: list[Attempt]
        elapsed_seconds: float
        bobcoins_used: float
        adjudications: list[Adjudication]   # see §12
        adjudicator_available: bool         # False if watsonx.ai was unreachable

## 5. Subagents (Strategy pattern; each has a specific output type)

    Reproducer(workspace, bug) -> FailingTest
      * If --url provided: the orchestrator has already probed it; the
        captured RuntimeEvidence is given to Reproducer as context
      * ALWAYS writes a pytest that exercises the code path IN-PROCESS
        (e.g. Flask test_client), never the live URL — a running server
        keeps old code loaded, so a live-URL test cannot see a fix
      * The bug is "observable" only if this test FAILS on current code

    CauseTracer(workspace, bug, runtime_evidence) -> RootCause
      * Walks source code across ALL codebases
      * Follows imports/API contracts between codebases
      * Correlates runtime response with source lines
      * Assigns confidence 0.0-1.0

    FixAuthor(workspace, root_cause) -> list[Fix]
      * Proposes up to 3 candidate patches
      * Ranked by risk (quick patch < proper fix < refactor)

    Guard(workspace, root_cause, fix) -> RegressionTest
      * Writes a pytest that would have caught this bug
      * Never touches the fix code itself

## 6. Self-healing orchestrator loop

    workspace = load(folders, git_urls)
    auth = build_auth_context(auth_flag)  # from env vars only

    for attempt in range(MAX_ATTEMPTS):
        runtime_evidence = probe_url(url, auth) if url else None
        parallel_run(Reproducer, CauseTracer)   # both use runtime_evidence
        if failing_test passes on current code:
            return NEEDS_HUMAN (BugNotObservable, E9)
        if confidence < CONFIDENCE_FLOOR:
            return NEEDS_HUMAN
        candidates = FixAuthor(root_cause)       # up to 3, lowest risk first
        fix = first candidate whose adjudication == APPROVE   # §12; fail-open
        if no candidate approved:
            record failure  # reasoning feeds next attempt as context
            continue        # no fix is EVER applied without approval
        regression_test = Guard(root_cause, fix)
        apply(fix + regression_test)
        result = test_runner.run(all_codebases)
        if result.passed:
            git.commit(fix + regression_test)
            return FIXED
        revert(fix)
        record failure  # feeds next attempt as context

    return NEEDS_HUMAN

## 7. Edge cases (explicit — every one has a defined behavior)

    E1  No tests in any codebase -> skip Guard, status = PARTIAL
    E2  Bob returns malformed JSON -> pydantic error -> one retry
        inside bob_client -> then SubagentError
    E3  Fix breaks other tests -> revert, next attempt gets failure log
    E4  Bobcoin budget exhausted -> BudgetExhausted -> status = PARTIAL
    E5  Confidence < 0.70 -> DO NOT apply fix -> NEEDS_HUMAN
    E6  --dry-run -> subagents run, no apply, no commit
    E7  Auth token acquisition fails -> AuthError -> abort, no partial
    E8  URL unreachable (timeout/DNS) -> UnreachableError -> abort
    E9  Reproducer's failing test PASSES on current code (bug not
        reproducible) -> BugNotObservable -> NEEDS_HUMAN with a message
        asking for a better --issue text. NOTE: an HTTP 200 alone is NOT
        E9 — a wrong value returned with 200 is a real bug.
    E10 Bug spans codebases we don't have -> NEEDS_HUMAN with message
        naming the missing codebase

## 8. Where AI adds value (defensible in interview)

    - Semantic mapping between runtime response and source lines
      across N codebases (no static analyzer can do this)
    - Natural language --issue -> concrete failing pytest
    - Root cause reasoning that ties HTTP response payload
      to specific lines in specific files across services

## 9. Where deterministic code decides (NOT AI)

    - Loop control (retry count, budget)
    - HTTP client (requests, timeouts, retries)
    - Auth flow (token acquisition, refresh, TTL)
    - Test execution and result parsing
    - Git operations (commit, diff, revert)
    - Pydantic schema validation
    - Adjudication total score and APPROVE/REJECT verdict (threshold compare)

## 10. Secrets policy (hard rule)

    - All credentials live in .env, referenced by env var name
    - .env is git-ignored AND bob-ignored (verified in Step 3)
    - Tokens NEVER appear in report objects, log lines, or Bob
      chat context
    - bob_client.py redacts headers containing 'Authorization',
      'X-API-Key', 'Cookie' before sending prompts to Bob

## 11. Demo scenario (for video)

    Multi-service e-commerce sample:
      sample_app/frontend  (Flask, calls backend)
      sample_app/backend   (Flask, /cart/total endpoint)
      sample_app/shared    (pricing library)

    Seeded bug: pricing library applies discount pre-tax,
    frontend expects post-tax discount.

    Demo run:
      culprit debug \
        --url http://localhost:8000/cart/total \
        --auth-bearer \
        --folder ./sample_app/frontend \
        --folder ./sample_app/backend \
        --folder ./sample_app/shared \
        --issue "cart total wrong when discount code applied"

## 12. watsonx.ai Adjudicator (second opinion)

    Model:    read from WATSONX_MODEL_ID (a Granite model listed in the
              hackathon "watsonx Hackathon Sandbox" project, Dallas region).
              Never llama-3-405b-instruct or mistral-* (out of scope per
              hackathon guide).
    Transport: plain REST via httpx (IAM token endpoint + watsonx.ai
              text/chat endpoint). No ibm-watsonx-ai SDK: keeps deps light
              and avoids Python-version compatibility risk.
    Endpoint: WATSONX_URL (default https://us-south.ml.cloud.ibm.com)
    Project:  WATSONX_PROJECT_ID
    Auth:     IBM_CLOUD_API_KEY from .env -> IAM token -> watsonx bearer
              (secrets policy of §10 applies: never logged, never in the
              report, never in Bob chat context)

    Role: independent critic. Reads (root_cause, the fix selected for this
    attempt, relevant source context) and scores it on three axes:
      - correctness  (does the fix actually address the root cause?)
      - safety       (does the fix introduce new risks?)
      - minimalism   (is it the smallest change that works?)

    class Adjudication:
        correctness_score: float   # 0.0 - 1.0   (from the model)
        safety_score: float        # 0.0 - 1.0   (from the model)
        minimalism_score: float    # 0.0 - 1.0   (from the model)
        total_score: float         # mean of the 3 axes (computed by code)
        verdict: APPROVE | REJECT  # total_score >= threshold (computed by code)
        reasoning: str             # from the model

    The model only produces the three axis scores and the reasoning.
    total_score and verdict are computed deterministically by code.
    Model output is schema-enforced via the Adjudication pydantic model.

    Hard constants (defined in src/culprit/config.py):

        ADJUDICATION_THRESHOLD = 0.65    # total_score below this -> REJECT
        WATSONX_TIMEOUT_SECONDS = 20
        WATSONX_MAX_RETRIES = 2

    Behavior:
      * REJECT -> the fix is NOT applied; the reasoning is recorded as the
        attempt's failure_reason and fed to the next attempt (§6).
      * watsonx failure, timeout, or missing credentials -> log a warning
        and proceed WITHOUT adjudication; report.adjudicator_available =
        false. Never block on infrastructure failure.
      * Malformed model output -> pydantic error -> one retry -> then
        treated as a watsonx failure (previous bullet).
      * Source context sent to watsonx passes through the same redaction
        as bob_client (§10).
      * --dry-run -> adjudication still runs and is reported; nothing is
        applied and nothing is committed (E6).

    New edge case:
      E11  Every attempt REJECTed -> NEEDS_HUMAN, with the last
           adjudication reasoning included in the report.

## 13. Where each model adds value (defensible answer)

    Bob subagents: repository-aware, multi-file semantic reasoning; Agent
      mode for tool use (git, tests, filesystem).

    watsonx.ai Granite: an independent second opinion on proposed fixes,
      reducing single-model blind spots; produces structured axis scores.

    Deterministic code: budget, HTTP, git, schema validation, loops,
      adjudication total and verdict.

## 14. Bob runtime integration (how Culprit calls Bob)

    Culprit's subagents call IBM Bob through Bob Shell in non-interactive
    mode, as a subprocess (bob_client.py):

        bob run --format json --mode <ask|agent> --workspace <path>
                --max-cost <remaining budget> --accept-license "<prompt>"

    * Auth: Bob Shell's own login (SSO) or BOB_API_KEY in the environment.
      Culprit never reads, logs or passes BOB_API_KEY itself.
    * Budget: --max-cost is set per call to
      MAX_BOBCOIN_PER_RUN - bobcoins_used so far (hard cap enforced by Bob
      AND by Culprit). Cost per call is read from the JSON output if present.
    * The target service's AuthContext is NEVER passed to bob_client.
    * Prerequisite: Node.js >= 24 and Bob Shell installed.

    Bob IDE is used to BUILD Culprit (task sessions exported to
    bob_sessions/ for judging); Bob Shell is used by Culprit at RUNTIME.

## 15. Hackathon MVP scope (deadline-driven)

    MUST (demo path):
      --folder (1-6), --url (1), --auth-bearer, --no-auth, --issue,
      --dry-run; 4 subagents via Bob Shell; watsonx.ai adjudicator
      (fail-open); apply -> pytest -> commit; Rich terminal report.

    STRETCH (only if time remains; keep stubs raising NotImplementedError):
      --git, --auth-basic, --auth-oauth2, --auth-apikey, --trace.
