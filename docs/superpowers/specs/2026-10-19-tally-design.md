# Tally — Design Spec

**Date:** 2026-10-19  
**Author:** Dheeraj Bandaru  
**License:** Apache-2.0  
**PyPI package:** `tally-billing` (imported as `tally`)  
**Python:** 3.12

---

## Overview

Tally is a Python library that turns Arize AX eval results into Stripe meter events. A developer writes one pricing file, runs it as a worker process, and every billable span in their agent's traces is decided and (optionally) billed automatically. No database, no CLI, no config file beyond environment variables.

The worker runs four stages in a synchronous loop: discover new spans from AX, poll each pending span for its eval results, run the policy engine to produce a decision, and emit one Stripe meter event per BILL decision. Each stage reads the previous stage's committed records from a second AX project, so a crash at any point resumes cleanly.

---

## Architecture

### Worker loop (Option A — synchronous polling)

One `while True` loop drives `run()`; `run_once()` executes one cycle. Stripe calls are the only place concurrency is used: a `ThreadPoolExecutor` parallelizes across customers while serializing within each customer (Stripe constraint).

```
one cycle
────────────────────────────────────────────────────
Stage 1 — Discover
  For each registered policy:
    cursor = store.latest_cursor(policy.name)
    new_spans = source.list_new_spans(policy, cursor)
    For each span:
      if store has a DECISION record → skip
      add Unit(pending, deadline=ended_at + wait + 1h grace) to in-memory dict

Stage 2 — Poll
  For each pending Unit:
    evals = source.get_evals(trace_id, span_id)
    cache in memory for this cycle

Stage 3 — Engine
  For each pending Unit with evals fetched:
    decision = engine.decide(policy, unit, evals, now)
    if None → stays pending
    else → store.write(DECISION record); unit.status = decided

Stage 4 — Emit
  Select BILL decisions with no EMISSION record
  Group by customer_id
  ThreadPoolExecutor: one thread per customer, serialized within customer
    live mode  → POST Stripe meter event → store.write(EMISSION)
    shadow mode → store.write(EMISSION, shadow=True), no Stripe call
────────────────────────────────────────────────────
```

**Restart safety:** Stage 1 skips spans with a DECISION record. Stage 4 skips spans with an EMISSION record. The in-memory unit dict is rebuilt from AX on every startup.

**Error handling:** Any `EvalSource` or `RecordStore` exception is caught, logged with `trace_id`/`span_id` (never with eval explanation text), and the unit stays pending. The loop never raises into the caller. The `@tally.outcome` decorator wraps span creation in `try/except` and always returns normally.

---

## Repo Layout

```
tally/
  __init__.py             # public API re-exports
  feed/
    source.py             # EvalSource ABC, SpanInfo, EvalResult dataclasses
    ax.py                 # AX GraphQL implementation (read-only)
    file.py               # file-based fixture implementation (Phase 1 + tests)
  policy/
    declaration.py        # Policy dataclass, version hashing
    engine.py             # pure decide() function
  log/
    records.py            # Record dataclass, content hashing (SHA-256 / RFC 8785)
    store.py              # RecordStore ABC
    ax.py                 # AX OTLP implementation
    memory.py             # in-memory implementation (tests)
  emit/
    stripe.py             # Stripe meter event sender + ThreadPoolExecutor
  sdk/
    api.py                # policy(), run(), run_once(), outcome decorator/context manager
    explain.py            # tally.explain()
    resolve.py            # tally.resolve()
    verify.py             # tally.verify()
  worker.py               # four-stage loop

demo/
  agent.py                # code-review agent with @tally.outcome
  pricing.py              # the pricing file from the spec
  fixtures/               # recorded AX spans + evals as JSON
  setup/                  # seed_repo.py, setup_evals.py

tests/
  policy/                 # Phase 1: table-driven engine tests
  feed/                   # Phase 2: file vs AX source parity
  log/                    # Phase 3: hash verification, explain, restart
  emit/                   # Phase 4: Stripe test-mode tests
  e2e/                    # Phase 5: full shadow-mode run

docs/
  superpowers/specs/

pyproject.toml
.github/workflows/
  ci.yml
  publish.yml
  quickstart.yml
```

---

## Data Model

All types are plain frozen dataclasses. No ORM, no database.

### Policy

```python
@dataclass(frozen=True)
class Policy:
    name: str
    span: str
    customer: str | Callable        # span attr name or fn(attrs) -> str
    evals: list[str]                # declared, waited for
    watch: list[str]                # recorded if present, never waited for
    translate: str                  # source text of the translate function
    version: str                    # SHA-256(canonical(name, span, evals, watch, translate))
    meter: str                      # defaults to name
    wait: timedelta                 # defaults to 7 days
```

**Version hashing:** `SHA-256(json.dumps({"name":…, "span":…, "evals":…, "watch":…, "translate": inspect.getsource(fn)}, sort_keys=True))`. Any change to the declaration or function body produces a new version. The first use of a version writes a `POLICY` record.

### SpanInfo & EvalResult

```python
@dataclass(frozen=True)
class SpanInfo:
    trace_id: str
    span_id: str
    customer_id: str
    span_name: str
    ended_at: datetime

@dataclass(frozen=True)
class EvalResult:
    name: str
    label: str | None
    score: float | None
    explanation: str
    agreement: str | None       # "unanimous" | "split" | None
    evaluator_version: str | None
    scored_at: datetime
    raw: dict
```

### Unit (in-memory only)

```python
@dataclass
class Unit:
    trace_id: str
    span_id: str
    customer_id: str
    policy_name: str
    policy_version: str
    first_seen: datetime
    deadline: datetime
    status: Literal["pending", "decided"]
```

### Record (immutable, written to AX)

```python
@dataclass(frozen=True)
class Record:
    type: Literal["POLICY", "MODE", "DECISION", "EMISSION", "RULING"]
    trace_id: str
    span_id: str
    customer_id: str
    policy_name: str
    policy_version: str
    policy_hash: str
    payload: dict           # type-specific fields (see below)
    content_hash: str       # SHA-256(RFC8785(all fields except content_hash))
```

**Non-span records (`MODE`, `POLICY`)** use `trace_id = ""`, `span_id = ""`, `customer_id = ""` as sentinels. These records are retrieved by `type` filter only, never by span identity.

**Record payload fields by type:**

| Type | Key payload fields |
|------|--------------------|
| `POLICY` | full declaration, `translate` source, version hash |
| `MODE` | `mode` (`shadow`/`live`) |
| `DECISION` | `decision`, `units`, `reason`, `review` flag, eval snapshot |
| `EMISSION` | `stripe_identifier`, `stripe_response`, `shadow` flag, `status` |
| `RULING` | `ruling` (`bill`/`no-charge`), `resolved_by` |

---

## Interfaces

### EvalSource

```python
class EvalSource(ABC):
    def list_new_spans(self, policy: Policy, cursor: datetime) -> list[SpanInfo]: ...
    def get_evals(self, trace_id: str, span_id: str) -> list[EvalResult]: ...
```

Implementations: `AXEvalSource` (AX GraphQL, read-only), `FileEvalSource` (JSON fixtures).

**Polling backoff:** Each pending unit is polled once per cycle. As a unit ages, the minimum inter-poll interval increases: `min(base * 2^n, cap)` where `base = 60s`, `cap = 30min`, `n = number of cycles since first seen`. This avoids hammering AX for units whose evals are expected to take hours.

### RecordStore

```python
class RecordStore(ABC):
    def write(self, record: Record) -> None: ...
    def list(self, *, trace_id=None, span_id=None, type=None) -> list[Record]: ...
    def latest_cursor(self, policy_name: str) -> datetime: ...
```

Implementations: `AXRecordStore` (OTLP write, GraphQL read), `InMemoryRecordStore` (tests).

---

## Policy Engine

Pure function — no I/O, no randomness.

```python
def decide(
    policy: Policy,
    unit: Unit,
    evals: dict[str, EvalResult],   # declared evals
    watch: dict[str, EvalResult],   # watch evals if present
    now: datetime,
) -> Decision | None:
```

**Rules (first match wins):**

| # | Condition | Result |
|---|-----------|--------|
| 1 | Any declared eval missing AND deadline not passed | `None` (stay pending) |
| 2 | Any declared eval missing AND deadline passed | `NO_CHARGE`, reason `"unscored"` |
| 3 | Any declared eval has `agreement == "split"` | `ESCALATE` |
| 4 | `translate` raises, returns invalid type, or gives different answers on two runs | `HOLD`, reason `"translate error"` |
| 5 | `translate` returns `bill(units=n)` | `BILL`, n units |
| 6 | `translate` returns `no_charge(reason)` | `NO_CHARGE` |
| 7 | `translate` returns `hold(reason)` | `HOLD` |

**Sandboxed execution:** The stored `translate` source is `compile()`'d and `exec()`'d into a namespace containing only the permitted builtins (`min, max, sum, len, any, all, abs, round, sorted`) plus `tally.bill`, `tally.no_charge`, `tally.hold`. Called twice with identical inputs — if results differ, rule 4 fires.

**Review flag:** If any watch eval has `agreement == "split"`, `Decision.review = True`. Does not change the decision.

---

## Decision Log & Verification

**Content hashing:** `json.dumps(fields_excluding_content_hash, sort_keys=True, ensure_ascii=True)` → `SHA-256.hexdigest()`. Recomputed on every read to detect tampering.

**`tally.explain(trace_id, span_id) -> dict`**  
Fetches all records for the span, assembles them in order, returns: policy declaration (from matching `POLICY` record), eval snapshot, decision, emission (if any), ruling (if any). Self-contained from records alone — no vendor repo needed.

**`tally.resolve(trace_id, span_id, ruling: Literal["bill", "no-charge"]) -> None`**  
Writes a `RULING` record. Never edits the original `DECISION` record. Resolves `HOLD` or `ESCALATE` outcomes.

**`tally.verify(trace_id=None, span_id=None) -> list[VerificationFailure]`**  
For each matching `DECISION` record:
1. Recomputes `content_hash` — mismatch → failed
2. Re-runs stored `translate` source on stored eval snapshot — decision mismatch → failed
3. If span still in AX, fetches live evals and compares to snapshot — mismatch → failed

With no arguments, checks every record still in AX.

---

## Billing Emitter

**Stripe event fields:**

```
event_name          = policy.meter
stripe_customer_id  = unit.customer_id
value               = decision.units
identifier          = f"{trace_id}::{span_id}"
timestamp           = decision.decided_at
```

**Idempotency (two layers):**
1. Store: Stage 4 only selects BILL decisions with no EMISSION record.
2. Stripe: drops repeated `identifier` for ~24 hours (covers crash-after-send-before-write).

**Old decision guard:** If a BILL decision is older than 35 days with no EMISSION record, write `EMISSION(status="failed")`, log it, do not retry.

**Mode gate:** Emitter only considers DECISION records with `decided_at` after the latest `MODE=live` record's timestamp. Shadow-period decisions are permanently skipped with `EMISSION(shadow=True)`.

**Credentials:** `STRIPE_RESTRICTED_KEY` read from env at startup. Worker raises immediately if live mode is set but key is absent. Key is never read in shadow mode.

---

## Public API

```python
tally.policy(name, span, customer, evals, translate, watch=[], wait=timedelta(days=7), meter=None)
tally.run()                         # blocking loop, 60s sleep between cycles
tally.run_once()                    # one cycle

@tally.outcome(span, customer)      # decorator OR context manager
tally.bill(units: int) -> BillResult
tally.no_charge(reason: str) -> NoChargeResult
tally.hold(reason: str) -> HoldResult

tally.explain(trace_id, span_id) -> dict
tally.resolve(trace_id, span_id, ruling: Literal["bill", "no-charge"]) -> None
tally.verify(trace_id=None, span_id=None) -> list[VerificationFailure]
```

`tally.policy()` registers into a module-level registry at import time. `tally.run()` / `tally.run_once()` read the registry and instantiate the worker with environment-driven concrete implementations.

---

## Environment Variables

| Variable | Meaning |
|----------|---------|
| `ARIZE_API_KEY` | Key for the AX space; reads agent project, writes decision project |
| `ARIZE_SPACE_ID` | AX space ID |
| `ARIZE_PROJECT` | AX project to read spans and eval scores from |
| `TALLY_MODE` | `shadow` (default) or `live` |
| `TALLY_PROJECT` | AX project for decision records; defaults to `<ARIZE_PROJECT>-tally` |
| `STRIPE_RESTRICTED_KEY` | Restricted key (meter events only); required only in live mode |

---

## Demo

- `demo/agent.py` — minimal code-review agent using `@tally.outcome("post_review_comment", customer="customer_id")` against a seeded Git repo.
- `demo/pricing.py` — the pricing file from the spec, runnable as `python pricing.py`.
- `demo/fixtures/` — pre-recorded AX spans and eval results as JSON, consumed by `FileEvalSource`. Runs without a live AX account.
- `demo/setup/seed_repo.py` — creates the fake Git repo for the agent to review.
- `demo/setup/setup_evals.py` — registers `fix_landed` and `cites_real_code` evals in AX via the Arize CLI.

---

## Testing

Tests follow the five build phases:

| Phase | Module | What must pass |
|-------|--------|----------------|
| 1 | `tests/policy/` | Table-driven engine tests for all 7 rules; identical inputs → identical decisions; fixture replay |
| 2 | `tests/feed/` | `FileEvalSource` and `AXEvalSource` return identical normalized results; AX error leaves unit pending |
| 3 | `tests/log/` | Edited snapshot/decision fails `verify`; `explain` works from records alone; restart skips decided spans |
| 4 | `tests/emit/` | Stripe test mode: one event per BILL; repeated send deduped; crash-between-send-and-write safe; live mode never bills shadow period |
| 5 | `tests/e2e/` | Demo run in shadow mode logs expected decisions, makes no Stripe call; decisions match hand labels |

---

## CI/CD

**`ci.yml`** — triggers on every PR:
- `ruff check` (lint)
- `mypy` (type check)
- `pytest tests/` with coverage threshold

**`publish.yml`** — triggers on `v*` tag:
- `hatch build` (wheel + sdist)
- Publish to PyPI via Trusted Publisher (OIDC — no stored API key)

**`quickstart.yml`** — DX acceptance job:
- `pip install tally-billing` on a clean runner
- `python demo/pricing.py` against fixtures
- Assert a shadow-mode decision appears (aspirational: within 30 minutes; against fixtures, effectively immediate)

---

## Build Phases

| Phase | Deliverable |
|-------|-------------|
| 1 | Policy dataclass + version hashing + pure engine with all 7 rules + table-driven tests |
| 2 | `EvalSource` ABC + `FileEvalSource` + `AXEvalSource` + feed stage + polling backoff |
| 3 | `RecordStore` ABC + `InMemoryRecordStore` + `AXRecordStore` + all record types + `explain` + `verify` |
| 4 | Stripe emitter + shadow/live mode gate + `ThreadPoolExecutor` per-customer + `EMISSION` records |
| 5 | `@tally.outcome` decorator + `run()` / `run_once()` + demo agent + demo pricing file + fixtures + e2e tests + CI/CD + PyPI packaging |

---

## Guarantees & Limits

- Tally never raises into the agent's request path.
- Billing cannot start by accident — `TALLY_MODE=shadow` by default, no Stripe key needed.
- Multiple workers can run concurrently — decisions are deterministic and Stripe drops repeated identifiers.
- Decision records expire with AX's retention window (15 days Free, 30 days Pro, configurable Enterprise).
- `tally.verify` cannot catch a deleted record or a coordinated rewrite of snapshot + decision + hash.
- v1 has no dispute console UI — `tally.resolve` is a Python API call only.
