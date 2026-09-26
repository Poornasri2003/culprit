# Culprit — Architecture Design v1.0

> Derived exclusively from SPEC.md v1.0. Every decision maps to a specific
> section of the spec; section references are inline.

---

## 1. Layer Overview

```
┌──────────────────────────────────────────────────┐
│  Presentation   src/culprit/cli.py               │
│  (Click, thin wrapper — §2)                      │
├──────────────────────────────────────────────────┤
│  Application    src/culprit/application/         │
│    orchestrator.py   report_builder.py           │
│  (Use-case logic — §6)                           │
├──────────────────────────────────────────────────┤
│  Domain         src/culprit/domain/              │
│    models.py    exceptions.py                    │
│  (Pydantic models, domain exceptions — §4, §12)  │
├──────────────────────────────────────────────────┤
│  Subagents      src/culprit/subagents/           │
│    base.py  factory.py  reproducer.py            │
│    cause_tracer.py  fix_author.py  guard.py      │
│  (Strategy pattern, Bob-backed — §5)             │
├──────────────────────────────────────────────────┤
│  Infrastructure src/culprit/infrastructure/      │
│    workspace.py  bob_client.py  watsonx_client.py│
│    http_client.py  auth.py  test_runner.py       │
│    git_ops.py                                    │
│  (I/O, external services — §9, §10, §12)         │
├──────────────────────────────────────────────────┤
│  Config         src/culprit/config.py            │
│  (All hard constants — §3, §12)                  │
└──────────────────────────────────────────────────┘
```

**Design patterns used per layer:**

| Layer          | Pattern(s)                              |
|----------------|-----------------------------------------|
| Presentation   | Facade (CLI hides all complexity)       |
| Application    | Orchestrator, Builder                   |
| Domain         | Value Object (Pydantic models), enum    |
| Subagents      | Strategy (interchangeable agents)       |
| Infrastructure | Adapter (wraps external libs/services)  |
| Config         | Singleton module-level constants        |

---

## 2. `src/culprit/config.py`

> All hard limits from §3 and all watsonx constants from §12.
> Nothing is hardcoded elsewhere.

```python
"""
Culprit configuration constants.

All hard limits (§3) and watsonx.ai Adjudicator settings (§12) live here.
Model IDs and watsonx coordinates are read from environment variables;
this module provides only the defaults and the numeric thresholds.
No credentials are stored here.
"""

# §3 — hard limits
MAX_ATTEMPTS: int
MAX_WALLCLOCK_SECONDS: int
MAX_FILES_PER_SUBAGENT: int
MAX_CODEBASES: int
MAX_URLS: int
MAX_BOBCOIN_PER_RUN: float
CONFIDENCE_FLOOR: float
HTTP_TIMEOUT_SECONDS: int
AUTH_TOKEN_TTL_SECONDS: int

# §12 — watsonx.ai Adjudicator
ADJUDICATION_THRESHOLD: float    # total_score below this -> REJECT
WATSONX_TIMEOUT_SECONDS: int
WATSONX_MAX_RETRIES: int

# §12 — env-var names (defaults supplied; never the actual credential values)
WATSONX_MODEL_ID_ENV: str        # env var name; default value "ibm/granite-3-8b-instruct"
WATSONX_URL_ENV: str             # env var name; default value "https://us-south.ml.cloud.ibm.com"
WATSONX_PROJECT_ID_ENV: str      # env var name; no default
IBM_CLOUD_API_KEY_ENV: str       # env var name; no default

# Redacted header names (§10)
REDACTED_HEADER_NAMES: frozenset[str]  # {"authorization", "x-api-key", "cookie"}
```

**Pattern:** Singleton module-level constants — imported directly, never
instantiated. All env-var names (not values) are also here so the rest of
the codebase has a single authoritative list.

---

## 3. `src/culprit/domain/models.py`

> All Pydantic models from §4 and §12. No business logic; pure schema.

```python
"""
Culprit domain models (Pydantic v2).

Defines the canonical data shapes for all Culprit concepts:
Endpoint, AuthContext, RuntimeEvidence, SourceCodebase, Bug,
RootCause, Fix, RegressionTest, Attempt, CulpritReport (§4),
and Adjudication (§12).

These models are the only permitted way to pass structured data
between layers. Credentials (tokens) are present as Optional fields
but are excluded from serialisation via model_config.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

# ── Enums ────────────────────────────────────────────────────────────────────

class AuthScheme(str, Enum):
    """Authentication scheme supported by the CLI (§2)."""
    BEARER = "BEARER"
    BASIC  = "BASIC"
    OAUTH2 = "OAUTH2"
    APIKEY = "APIKEY"
    NONE   = "NONE"

class ReportStatus(str, Enum):
    """Final status of a Culprit debug run (§4)."""
    FIXED        = "FIXED"
    PARTIAL      = "PARTIAL"
    NEEDS_HUMAN  = "NEEDS_HUMAN"

class AdjudicationVerdict(str, Enum):
    """Deterministic verdict produced by code from total_score (§12)."""
    APPROVE = "APPROVE"
    REJECT  = "REJECT"

# ── §4 Models ────────────────────────────────────────────────────────────────

class Endpoint(BaseModel):
    """A live HTTP endpoint to probe (§4)."""

    url: str
    method: str
    headers: dict[str, str]
    expected_status: int
    actual_status: Optional[int] = None

class AuthContext(BaseModel):
    """
    Holds the resolved authentication state for a run (§4).

    token is excluded from all serialisation to enforce §10.
    """

    scheme: AuthScheme
    token: Optional[str] = Field(default=None, exclude=True)
    expires_at: Optional[datetime] = None

class RuntimeEvidence(BaseModel):
    """Captured HTTP interaction evidence from a live endpoint probe (§4)."""

    endpoint: Endpoint
    request_body: Optional[str] = None
    response_body: str
    response_status: int
    latency_ms: float
    stack_trace_hint: Optional[str] = None

class SourceCodebase(BaseModel):
    """A single local codebase made available to subagents (§4)."""

    name: str
    root_path: Path
    language: str
    entry_points: list[str]

class Bug(BaseModel):
    """The problem statement assembled from CLI inputs (§4)."""

    description: str
    runtime_evidence: list[RuntimeEvidence]
    static_evidence: list[SourceCodebase]

class RootCause(BaseModel):
    """Root cause identified by CauseTracer, with a confidence score (§4)."""

    codebase: str
    file: str
    line: int
    symbol: str
    explanation: str
    confidence: float  # 0.0 – 1.0

class Fix(BaseModel):
    """A single candidate patch produced by FixAuthor (§4)."""

    target_codebase: str
    unified_diff: str
    applied_files: list[str]

class RegressionTest(BaseModel):
    """A pytest that would have caught this bug, written by Guard (§4)."""

    codebase: str
    file: str
    test_name: str
    code: str

class TestResult(BaseModel):
    """Outcome of running pytest across all codebases (§6, §9)."""

    passed: bool
    output: str
    failed_tests: list[str]

class Attempt(BaseModel):
    """Record of one self-healing loop iteration (§4)."""

    num: int
    subagent_outputs: dict[str, object]
    test_result: Optional[TestResult] = None
    failure_reason: Optional[str] = None

# ── §12 Model ────────────────────────────────────────────────────────────────

class Adjudication(BaseModel):
    """
    watsonx.ai Adjudicator result for one fix candidate (§12).

    correctness_score, safety_score, minimalism_score, and reasoning
    come from the model. total_score and verdict are computed by code.
    """

    correctness_score: float   # 0.0 – 1.0, from model
    safety_score: float        # 0.0 – 1.0, from model
    minimalism_score: float    # 0.0 – 1.0, from model
    total_score: float         # mean of three axes, computed by code
    verdict: AdjudicationVerdict   # threshold compare, computed by code
    reasoning: str             # from model

# ── §4 Top-level report ──────────────────────────────────────────────────────

class CulpritReport(BaseModel):
    """
    Final output of a Culprit debug run (§4, §12).

    adjudications collects every Adjudication produced across all attempts.
    adjudicator_available is False when watsonx.ai was unreachable (§12).
    """

    status: ReportStatus
    bug: Bug
    root_cause: RootCause
    fix: Optional[Fix] = None
    regression_test: Optional[RegressionTest] = None
    attempts: list[Attempt]
    elapsed_seconds: float
    bobcoins_used: float
    adjudications: list[Adjudication]
    adjudicator_available: bool
```

**Pattern:** Value Object — models carry data only; all computation lives in
the application or infrastructure layers.

---

## 4. `src/culprit/domain/exceptions.py`

> One exception class per §7 edge case that requires a named abort path.

```python
"""
Culprit domain exceptions.

Each exception corresponds to an explicit edge case in §7.
Catching code must not swallow these silently — they propagate to the
orchestrator which maps them to a CulpritReport status.
"""

class CulpritError(Exception):
    """Base class for all Culprit domain errors."""

class SubagentError(CulpritError):
    """Bob returned malformed JSON after the allowed retry (E2)."""

class BudgetExhausted(CulpritError):
    """Bobcoin budget MAX_BOBCOIN_PER_RUN exceeded (E4)."""

class AuthError(CulpritError):
    """Auth token acquisition or refresh failed (E7)."""

class UnreachableError(CulpritError):
    """Target URL timed out or DNS-resolved to nothing (E8)."""

class BugNotObservable(CulpritError):
    """URL returned 200; the bug could not be reproduced (E9)."""

class MissingCodebaseError(CulpritError):
    """Bug spans a codebase not provided to the run (E10)."""
```

**Pattern:** Exception hierarchy rooted at `CulpritError` — enables a single
catch-all in the orchestrator while still allowing precise handling.

---

## 5. `src/culprit/subagents/base.py`

> Abstract base class defining the Strategy interface all subagents implement.

```python
"""
Abstract Subagent base class (Strategy pattern, §5).

Each concrete subagent implements `run()` with its own input/output
types but shares the lifecycle contract defined here: budget checking,
workspace reference, and the Bob client adapter.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from culprit.infrastructure.workspace import Workspace
from culprit.infrastructure.bob_client import BobClient

OutputT = TypeVar("OutputT")

class Subagent(ABC):
    """
    Strategy base for all Culprit subagents (§5).

    Concrete subagents inject a Workspace and BobClient at construction;
    run() is the single entry point invoked by the orchestrator.
    """

    def __init__(self, workspace: Workspace, bob_client: BobClient) -> None:
        """Store workspace and Bob client; do not open files or call APIs."""

    @abstractmethod
    async def run(self, **kwargs: Any) -> Any:
        """Execute the subagent task and return its typed output."""

    def _check_budget(self, bobcoins_used: float) -> None:
        """Raise BudgetExhausted if bobcoins_used >= MAX_BOBCOIN_PER_RUN."""
```

**Pattern:** Strategy — the orchestrator holds a list of `Subagent` references
and calls `run()` polymorphically; concrete strategies are swappable.

---

## 6. `src/culprit/subagents/factory.py`

> Constructs the four concrete subagent instances (Factory pattern).

```python
"""
SubagentFactory — instantiates the four concrete subagents (§5).

Centralises dependency injection so the orchestrator never constructs
subagents directly. The factory is the only place that names concrete
subagent classes.
"""

from __future__ import annotations

from culprit.infrastructure.workspace import Workspace
from culprit.infrastructure.bob_client import BobClient
from culprit.subagents.reproducer import Reproducer
from culprit.subagents.cause_tracer import CauseTracer
from culprit.subagents.fix_author import FixAuthor
from culprit.subagents.guard import Guard

class SubagentFactory:
    """Creates fully-wired subagent instances for the orchestrator (§5)."""

    def __init__(self, workspace: Workspace, bob_client: BobClient) -> None:
        """Store shared dependencies; no subagents are created yet."""

    def make_reproducer(self) -> Reproducer:
        """Return a new Reproducer bound to the stored workspace and client."""

    def make_cause_tracer(self) -> CauseTracer:
        """Return a new CauseTracer bound to the stored workspace and client."""

    def make_fix_author(self) -> FixAuthor:
        """Return a new FixAuthor bound to the stored workspace and client."""

    def make_guard(self) -> Guard:
        """Return a new Guard bound to the stored workspace and client."""
```

**Pattern:** Factory Method — decouples instantiation from usage.

---

## 7. `src/culprit/subagents/reproducer.py`

```python
"""
Reproducer subagent — produces a FailingTest (§5).

If a live URL is provided the Reproducer makes an authenticated HTTP call
via http_client, captures RuntimeEvidence, and writes a pytest that hits
the endpoint. Without a URL it writes a pytest that invokes the code path
directly from source.
"""

from __future__ import annotations
from dataclasses import dataclass

from culprit.subagents.base import Subagent
from culprit.domain.models import Bug, RuntimeEvidence, AuthContext

@dataclass
class FailingTest:
    """A pytest file that demonstrates the bug (output of Reproducer)."""
    codebase: str
    file: str
    code: str
    runtime_evidence: RuntimeEvidence | None

class Reproducer(Subagent):
    """
    Subagent that creates a reproducible failing test (§5).

    Implements the Strategy interface defined in Subagent.
    """

    async def run(  # type: ignore[override]
        self,
        *,
        bug: Bug,
        auth_context: AuthContext | None,
    ) -> FailingTest:
        """Produce a FailingTest; capture RuntimeEvidence when a URL is present."""
```

---

## 8. `src/culprit/subagents/cause_tracer.py`

```python
"""
CauseTracer subagent — identifies the root cause across codebases (§5).

Walks source code in ALL provided SourceCodebases, follows
imports/API contracts between them, correlates runtime response with
source lines, and assigns a confidence score 0.0–1.0.
"""

from __future__ import annotations

from culprit.subagents.base import Subagent
from culprit.domain.models import Bug, RuntimeEvidence, RootCause

class CauseTracer(Subagent):
    """
    Subagent that traces the root cause across N codebases (§5).

    Implements the Strategy interface defined in Subagent.
    """

    async def run(  # type: ignore[override]
        self,
        *,
        bug: Bug,
        runtime_evidence: RuntimeEvidence | None,
    ) -> RootCause:
        """Return a RootCause with confidence 0.0–1.0; raises SubagentError on bad output."""
```

---

## 9. `src/culprit/subagents/fix_author.py`

```python
"""
FixAuthor subagent — proposes up to 3 ranked candidate patches (§5).

Candidates are ranked by risk: quick patch < proper fix < refactor.
The orchestrator selects the first candidate that passes adjudication.
"""

from __future__ import annotations

from culprit.subagents.base import Subagent
from culprit.domain.models import RootCause, Fix

class FixAuthor(Subagent):
    """
    Subagent that authors ranked fix candidates (§5).

    Implements the Strategy interface defined in Subagent.
    """

    async def run(  # type: ignore[override]
        self,
        *,
        root_cause: RootCause,
    ) -> list[Fix]:
        """Return up to 3 Fix candidates ranked by risk (lowest first)."""
```

---

## 10. `src/culprit/subagents/guard.py`

```python
"""
Guard subagent — writes a regression test that would have caught the bug (§5).

Guard never modifies the fix code itself. If no tests exist in any
codebase the orchestrator skips Guard and records status = PARTIAL (E1).
"""

from __future__ import annotations

from culprit.subagents.base import Subagent
from culprit.domain.models import RootCause, Fix, RegressionTest

class Guard(Subagent):
    """
    Subagent that writes a regression test for the confirmed fix (§5).

    Implements the Strategy interface defined in Subagent.
    """

    async def run(  # type: ignore[override]
        self,
        *,
        root_cause: RootCause,
        fix: Fix,
    ) -> RegressionTest:
        """Return a RegressionTest that would have caught the bug; never edits fix code."""
```

---

## 11. `src/culprit/infrastructure/workspace.py`

```python
"""
Workspace — loads and exposes all source codebases for a run (§6, §9).

Handles local --folder paths and --git remote repo clones. Enforces
MAX_CODEBASES (§3). Never touches credentials.
"""

from __future__ import annotations
from pathlib import Path

from culprit.domain.models import SourceCodebase

class Workspace:
    """
    Aggregates all SourceCodebases available to a run (§6).

    Pattern: Adapter — hides filesystem and git I/O behind a stable interface.
    """

    def __init__(self, work_dir: Path) -> None:
        """Initialise with a temporary working directory; no I/O yet."""

    def load_folder(self, path: Path, name: str) -> SourceCodebase:
        """Register a local folder as a SourceCodebase; raises if MAX_CODEBASES exceeded."""

    def load_git(self, url: str, name: str) -> SourceCodebase:
        """Clone a remote repo and register it; raises if MAX_CODEBASES exceeded."""

    def get_codebases(self) -> list[SourceCodebase]:
        """Return all registered SourceCodebase objects."""

    def resolve_file(self, codebase_name: str, relative_path: str) -> Path:
        """Return the absolute Path for a file inside a named codebase."""

    def cleanup(self) -> None:
        """Remove all cloned repos and temp artefacts created during this run."""
```

**Pattern:** Adapter — shields the application layer from filesystem/git
details.

---

## 12. `src/culprit/infrastructure/bob_client.py`

> Wraps the IBM Bob 2.0 agent API. Implements header redaction per §10.

```python
"""
BobClient — IBM Bob 2.0 adapter with header redaction (§10).

Sends structured prompts to Bob subagents and parses the JSON responses.
Headers matching REDACTED_HEADER_NAMES are stripped before any content
reaches Bob chat context (§10). Retries once on malformed JSON (E2).
"""

from __future__ import annotations
from typing import Any

from culprit.domain.models import AuthContext

class BobClient:
    """
    Adapter around the IBM Bob 2.0 agent API (§5, §10).

    Pattern: Adapter — presents a stable async interface; hides Bob
    session lifecycle and retry logic.
    """

    def __init__(self, auth_context: AuthContext) -> None:
        """Initialise with the run's AuthContext; do not open a session yet."""

    async def run_agent(
        self,
        prompt: str,
        context: dict[str, Any],
        *,
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        """
        Send a prompt to a Bob agent and return the parsed JSON response.

        Retries once on malformed JSON before raising SubagentError (E2).
        Tracks bobcoin usage and raises BudgetExhausted when limit is hit (E4).
        """

    def redact_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Return a copy of headers with REDACTED_HEADER_NAMES values replaced by '<redacted>'."""

    def get_bobcoins_used(self) -> float:
        """Return cumulative bobcoin spend for the current run."""
```

**Pattern:** Adapter — decouples subagents from the Bob SDK.

---

## 13. `src/culprit/infrastructure/watsonx_client.py`

> Adjudicator adapter per §12. Computes `total_score` and `verdict` deterministically.

```python
"""
WatsonxClient — watsonx.ai Adjudicator adapter (§12).

Reads model ID and coordinates from environment variables (via config.py).
Sends (root_cause, fix, source_context) to the Granite model and parses
the three axis scores. Computes total_score and verdict deterministically.
Applies the same header redaction as bob_client (§10).
Never logs or exposes credentials.
"""

from __future__ import annotations

from culprit.domain.models import RootCause, Fix, Adjudication

class WatsonxClient:
    """
    Adapter for the watsonx.ai Adjudicator (§12).

    Pattern: Adapter — hides IAM token acquisition, HTTP transport, and
    retry logic behind adjudicate().
    """

    def __init__(self) -> None:
        """
        Read WATSONX_URL, WATSONX_PROJECT_ID, WATSONX_MODEL_ID, IBM_CLOUD_API_KEY
        from environment; raise ConfigurationError if required vars are absent.
        No network call here.
        """

    async def adjudicate(
        self,
        root_cause: RootCause,
        fix: Fix,
        source_context: str,
    ) -> Adjudication:
        """
        Call the Granite model; compute total_score and verdict; return Adjudication.

        source_context has already been redacted by the caller.
        Retries once on malformed model output, then raises WatsonxError (§12).
        Raises WatsonxError on timeout or auth failure — caller proceeds fail-open.
        """

    def _compute_total_score(
        self,
        correctness: float,
        safety: float,
        minimalism: float,
    ) -> float:
        """Return the arithmetic mean of the three axis scores (§12)."""

    def _compute_verdict(self, total_score: float) -> "AdjudicationVerdict":
        """Return APPROVE if total_score >= ADJUDICATION_THRESHOLD, else REJECT (§12)."""

    def _redact(self, source_context: str) -> str:
        """Strip credential-bearing header patterns per REDACTED_HEADER_NAMES (§10)."""
```

**Pattern:** Adapter — isolates all watsonx.ai SDK calls.

> **Key constraint (§6, §12):** `adjudicate()` is called BEFORE `fix` is
> applied. A REJECT verdict means the fix is never written to disk; its
> `reasoning` is stored as `Attempt.failure_reason` and fed to the next
> attempt as context.

---

## 14. `src/culprit/infrastructure/http_client.py`

```python
"""
HttpClient — authenticated HTTP probe adapter (§5, §9).

Makes real HTTP calls to live endpoints. Enforces HTTP_TIMEOUT_SECONDS.
Captures the full response as RuntimeEvidence. Raises UnreachableError
on timeout/DNS failure (E8) and BugNotObservable when the endpoint
returns 200 (E9).
"""

from __future__ import annotations

from culprit.domain.models import AuthContext, RuntimeEvidence, Endpoint

class HttpClient:
    """
    Adapter for probing live endpoints (§5, §9).

    Pattern: Adapter — wraps httpx and maps HTTP outcomes to domain types.
    """

    def __init__(self, auth_context: AuthContext) -> None:
        """Build the httpx client; do not open connections yet."""

    async def probe(self, endpoint: Endpoint) -> RuntimeEvidence:
        """
        Make an authenticated HTTP call and return RuntimeEvidence.

        Raises UnreachableError on timeout/DNS failure (E8).
        Raises BugNotObservable when the response is HTTP 200 (E9).
        """

    async def close(self) -> None:
        """Release the underlying httpx client connection pool."""
```

---

## 15. `src/culprit/infrastructure/auth.py`

```python
"""
AuthBuilder — constructs an AuthContext from environment variables (§2, §9).

Reads credentials exclusively from environment variables (.env loaded by
python-dotenv). Never accepts credentials from CLI arguments.
Raises AuthError if required env vars are absent or if token
acquisition fails (E7).
"""

from __future__ import annotations

from culprit.domain.models import AuthContext, AuthScheme

class AuthBuilder:
    """
    Constructs a validated AuthContext from the environment (§2, §9).

    Pattern: Builder — assembles an immutable AuthContext step by step
    depending on the chosen auth scheme.
    """

    def build(self, scheme: AuthScheme) -> AuthContext:
        """
        Read env vars for scheme, acquire token if needed, return AuthContext.

        Raises AuthError if required vars are missing or token call fails (E7).
        """

    def _build_bearer(self) -> AuthContext:
        """Read BEARER_TOKEN from env; return AuthContext(scheme=BEARER)."""

    def _build_basic(self) -> AuthContext:
        """Read BASIC_USER, BASIC_PASS from env; return AuthContext(scheme=BASIC)."""

    def _build_oauth2(self) -> AuthContext:
        """Read OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_TOKEN_URL; acquire token."""

    def _build_apikey(self) -> AuthContext:
        """Read API_KEY, API_KEY_HEADER from env; return AuthContext(scheme=APIKEY)."""

    def _build_none(self) -> AuthContext:
        """Return AuthContext(scheme=NONE) for public endpoints."""
```

**Pattern:** Builder — the `build()` dispatch keeps each scheme's logic
isolated and testable.

---

## 16. `src/culprit/infrastructure/test_runner.py`

```python
"""
TestRunner — executes pytest across all codebases and parses results (§6, §9).

All test execution is deterministic subprocess work; no AI involvement.
Raises no domain exceptions — failures are represented as TestResult.passed=False.
"""

from __future__ import annotations
from pathlib import Path

from culprit.domain.models import TestResult, SourceCodebase

class TestRunner:
    """
    Runs pytest across all codebases and returns a TestResult (§6, §9).

    Pattern: Adapter — wraps subprocess pytest invocation.
    """

    def run(self, codebases: list[SourceCodebase]) -> TestResult:
        """
        Invoke pytest in each codebase root; aggregate pass/fail; return TestResult.

        Never raises — test failures are expressed through TestResult.passed.
        """

    def has_tests(self, codebase: SourceCodebase) -> bool:
        """Return True if the codebase contains at least one pytest-discoverable test file."""
```

---

## 17. `src/culprit/infrastructure/git_ops.py`

```python
"""
GitOps — deterministic git operations (commit, revert, diff) per §6, §9.

Uses GitPython. All operations are per-codebase. Never commits credentials
or tokens. Never invokes AI.
"""

from __future__ import annotations
from pathlib import Path

from culprit.domain.models import Fix, RegressionTest, SourceCodebase

class GitOps:
    """
    Adapter for git operations during the self-healing loop (§6, §9).

    Pattern: Adapter — wraps GitPython behind a domain-typed interface.
    """

    def apply_fix(self, codebase: SourceCodebase, fix: Fix) -> None:
        """Apply a unified diff to the codebase working tree (does not commit)."""

    def revert_fix(self, codebase: SourceCodebase, fix: Fix) -> None:
        """Undo all changes introduced by the given fix."""

    def commit(
        self,
        codebase: SourceCodebase,
        fix: Fix,
        regression_test: RegressionTest | None,
    ) -> str:
        """Stage fix + regression test files and create a commit; return the commit SHA."""

    def current_diff(self, codebase: SourceCodebase) -> str:
        """Return the current working-tree unified diff for the codebase."""
```

---

## 18. `src/culprit/application/orchestrator.py`

> Implements the self-healing loop from §6. Calls adjudication BEFORE applying
> any fix.

```python
"""
Orchestrator — the self-healing debug loop (§6).

Controls the attempt loop up to MAX_ATTEMPTS, runs the four subagents
(concurrently via anyio), calls the watsonx.ai Adjudicator before every
fix application, applies or reverts fixes, runs tests, and accumulates
Attempt records.

All hard limits (MAX_ATTEMPTS, CONFIDENCE_FLOOR, etc.) are enforced here
deterministically — never delegated to AI (§9).
"""

from __future__ import annotations
import time
from pathlib import Path

from culprit.domain.models import (
    Bug, AuthContext, CulpritReport, Attempt, Adjudication,
    RootCause, Fix, ReportStatus,
)
from culprit.subagents.factory import SubagentFactory
from culprit.infrastructure.workspace import Workspace
from culprit.infrastructure.watsonx_client import WatsonxClient
from culprit.infrastructure.test_runner import TestRunner
from culprit.infrastructure.git_ops import GitOps
from culprit.infrastructure.http_client import HttpClient

class Orchestrator:
    """
    Controls the full Culprit self-healing loop (§6).

    Pattern: Orchestrator — coordinates subagents, infrastructure, and
    domain rules without embedding any business logic inside adapters.
    """

    def __init__(
        self,
        workspace: Workspace,
        factory: SubagentFactory,
        watsonx_client: WatsonxClient,
        test_runner: TestRunner,
        git_ops: GitOps,
        http_client: HttpClient | None,
        dry_run: bool,
    ) -> None:
        """Wire all dependencies; no I/O at construction time."""

    async def run(self, bug: Bug, auth_context: AuthContext) -> CulpritReport:
        """
        Execute up to MAX_ATTEMPTS of the self-healing loop; return the final CulpritReport.

        Adjudication runs before every fix application (§6, §12).
        A REJECT verdict prevents apply; reasoning feeds the next attempt.
        On --dry-run: adjudication still runs; nothing is applied or committed (E6).
        """

    async def _run_attempt(
        self,
        attempt_num: int,
        bug: Bug,
        auth_context: AuthContext,
        prior_failure: str | None,
    ) -> tuple[Attempt, RootCause | None, Fix | None]:
        """
        Run one attempt: probe URL, run 4 subagents in parallel, return partial results.

        Returns a tuple of (Attempt, RootCause, selected Fix); Fix is None on REJECT.
        """

    async def _run_subagents_parallel(
        self,
        bug: Bug,
        auth_context: AuthContext,
    ) -> tuple["FailingTest", RootCause, list[Fix]]:
        """Launch Reproducer, CauseTracer, FixAuthor concurrently via anyio; return outputs."""

    def _select_fix(self, candidates: list[Fix]) -> Fix:
        """Return the lowest-risk fix candidate (index 0 from FixAuthor ranking)."""

    def _adjudicate(
        self,
        root_cause: RootCause,
        fix: Fix,
        source_context: str,
    ) -> Adjudication | None:
        """
        Call WatsonxClient.adjudicate; return None on any infrastructure failure (fail-open, §12).

        Sets report.adjudicator_available = False when returning None.
        """

    def _build_source_context(self, root_cause: RootCause) -> str:
        """Assemble redacted source context lines around root_cause.file:line for the adjudicator."""

    def _determine_final_status(
        self,
        attempts: list[Attempt],
        adjudications: list[Adjudication],
    ) -> ReportStatus:
        """Return FIXED, PARTIAL, or NEEDS_HUMAN based on attempt outcomes (§6, §7)."""
```

**Pattern:** Orchestrator — single class owns the loop, delegates I/O and AI
calls to injected collaborators.

---

## 19. `src/culprit/application/report_builder.py`

```python
"""
ReportBuilder — assembles the final CulpritReport (Builder pattern).

Accumulates data from the orchestrator loop incrementally and produces
a single, validated CulpritReport at the end of the run.
"""

from __future__ import annotations
import time

from culprit.domain.models import (
    Bug, RootCause, Fix, RegressionTest, Attempt,
    Adjudication, CulpritReport, ReportStatus,
)

class ReportBuilder:
    """
    Incrementally assembles a CulpritReport during the orchestration loop.

    Pattern: Builder — separates the construction of the complex report
    object from the loop logic in Orchestrator.
    """

    def __init__(self, bug: Bug) -> None:
        """Initialise with the Bug; start the elapsed-time clock."""

    def add_attempt(self, attempt: Attempt) -> None:
        """Append an Attempt record to the accumulator."""

    def add_adjudication(self, adjudication: Adjudication) -> None:
        """Append an Adjudication record to the accumulator."""

    def set_root_cause(self, root_cause: RootCause) -> None:
        """Record the confirmed RootCause for the report."""

    def set_fix(self, fix: Fix) -> None:
        """Record the applied Fix (only set when fix was actually applied)."""

    def set_regression_test(self, regression_test: RegressionTest) -> None:
        """Record the regression test written by Guard."""

    def set_adjudicator_available(self, available: bool) -> None:
        """Record whether watsonx.ai was reachable during this run."""

    def set_bobcoins_used(self, amount: float) -> None:
        """Record the final bobcoin consumption from BobClient."""

    def build(self, status: ReportStatus) -> CulpritReport:
        """Finalise elapsed_seconds and return the validated CulpritReport."""
```

---

## 20. `src/culprit/cli.py`

> Thin Click wrapper — all logic delegated to `Orchestrator`. §2.

```python
"""
Culprit CLI entry point (§2).

Defines the `culprit debug` Click command. Parses and validates CLI flags,
loads .env, instantiates infrastructure objects, delegates entirely to
Orchestrator, and renders the CulpritReport to the terminal via Rich.

This file contains NO business logic. It is a pure presentation layer.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional

import click

from culprit.domain.models import AuthScheme
from culprit.application.orchestrator import Orchestrator

@click.group()
def cli() -> None:
    """Culprit — AI-powered backend debugger."""

@cli.command("debug")
@click.argument("inputs", nargs=-1)
@click.option("--issue", required=True, help="Bug description.")
@click.option("--url", multiple=True, help="Live endpoint URL (repeatable, max 3).")
@click.option("--folder", multiple=True, type=click.Path(exists=True), help="Local code folder (repeatable, 1-6).")
@click.option("--git", "git_urls", multiple=True, help="Remote repo to clone (repeatable, 1-4).")
@click.option("--trace", type=click.Path(exists=True), default=None, help="Path to stack trace or error log.")
@click.option("--auth-bearer", "auth_scheme", flag_value="BEARER", help="Read BEARER_TOKEN from .env.")
@click.option("--auth-basic",  "auth_scheme", flag_value="BASIC",  help="Read BASIC_USER, BASIC_PASS from .env.")
@click.option("--auth-oauth2", "auth_scheme", flag_value="OAUTH2", help="Read OAUTH_* vars from .env.")
@click.option("--auth-apikey", "auth_scheme", flag_value="APIKEY", help="Read API_KEY, API_KEY_HEADER from .env.")
@click.option("--no-auth",     "auth_scheme", flag_value="NONE",   help="Explicit for public endpoints.")
@click.option("--dry-run", is_flag=True, default=False, help="Analyze only; no fix applied, no commit.")
def debug_command(
    inputs: tuple[str, ...],
    issue: str,
    url: tuple[str, ...],
    folder: tuple[str, ...],
    git_urls: tuple[str, ...],
    trace: Optional[str],
    auth_scheme: str,
    dry_run: bool,
) -> None:
    """
    Debug a live backend service and attempt an automated fix.

    Validates flag counts against MAX_URLS and MAX_CODEBASES, builds
    infrastructure objects, and delegates to Orchestrator.run().
    Renders the CulpritReport with Rich on completion.
    """
```

**Pattern:** Facade — the CLI is the single public face of the package;
everything internal is hidden behind `Orchestrator`.

---

## 21. Adjudication flow (§6 constraint re-stated)

The following sequence is **invariant** — enforced in `Orchestrator._run_attempt()`:

```
subagents complete
       │
       ▼
confidence >= CONFIDENCE_FLOOR ?  ──No──> NEEDS_HUMAN
       │ Yes
       ▼
adjudicate(root_cause, fix)          # watsonx.ai call; fail-open
       │
       ├─ REJECT ──> record reasoning as Attempt.failure_reason
       │              feed reasoning to next attempt; fix NOT applied
       │
       └─ APPROVE ──> apply(fix)
                       test_runner.run()
                             │
                     passed ─┴─ failed
                        │              │
                    commit           revert
                    FIXED        record failure
```

On `--dry-run`: adjudication still runs and its result is recorded, but the
`apply(fix)` and `commit` branches are skipped (E6, §12).

---

## 22. Secrets policy summary (§10)

| Location                                | Credential visible? |
|-----------------------------------------|---------------------|
| `.env` file                             | Yes (source only)   |
| `AuthContext.token` (in-memory)         | Yes                 |
| `AuthContext` serialised to JSON/report | **No** (`exclude=True`) |
| Bob chat context / prompt               | **No** (redacted)   |
| watsonx.ai prompt / source context      | **No** (redacted)   |
| Log lines                               | **No**              |
| `CulpritReport` (written to disk/stdout)| **No**              |

`redact_headers()` in `BobClient` and `_redact()` in `WatsonxClient` both
strip headers whose lowercased name appears in `config.REDACTED_HEADER_NAMES`.

---

## 23. Exception-to-status mapping

| Exception            | Edge case | Orchestrator action           |
|----------------------|-----------|-------------------------------|
| `AuthError`          | E7        | Abort immediately, no report  |
| `UnreachableError`   | E8        | Abort immediately, no report  |
| `BugNotObservable`   | E9        | `NEEDS_HUMAN` + message       |
| `MissingCodebaseError`| E10      | `NEEDS_HUMAN` + message       |
| `BudgetExhausted`    | E4        | `PARTIAL`                     |
| `SubagentError`      | E2        | Record failure; next attempt  |
| All attempts REJECT  | E11       | `NEEDS_HUMAN` + last reasoning|
| No tests found       | E1        | Skip Guard; `PARTIAL`         |

---

*End of ARCHITECTURE.md — scope bounded to SPEC.md v1.0.*
