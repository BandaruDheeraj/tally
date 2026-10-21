# Tally Phase 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the four-stage worker loop, public `tally.policy()` / `tally.run()` / `tally.run_once()` / `@tally.outcome` API, demo agent + pricing file, e2e shadow-mode test, and CI/CD workflows.

**Architecture:** Module-level policy registry; `run()`/`run_once()` instantiate concrete AX+Stripe implementations from env vars; `@tally.outcome` is a decorator/context manager for OTLP span creation (stub — writes nothing, always returns normally); four-stage worker drives discover→poll→decide→emit.

**Tech Stack:** Python 3.12, requests, concurrent.futures, GitHub Actions

## Global Constraints

- Python 3.12, `from __future__ import annotations` in every file
- No new runtime dependencies
- `tally.run()` / `tally.run_once()` read env vars: `ARIZE_API_KEY`, `ARIZE_SPACE_ID`, `ARIZE_PROJECT`, `TALLY_MODE` (default `shadow`), `TALLY_PROJECT` (default `<ARIZE_PROJECT>-tally`), `STRIPE_RESTRICTED_KEY` (only required in live mode)
- Worker never raises into the caller; all stage errors are caught and logged
- `@tally.outcome` always returns normally (catches all exceptions in the wrapped fn)
- `ruff check` and `mypy --strict` must pass after each task
- Tests: `py -3.12 -m pytest tests/ -q`

---

### Task 1: Worker loop (four-stage) + tally.policy() registry

**Files:**
- Create: `tally/worker.py`
- Create: `tally/sdk/registry.py`
- Modify: `tally/__init__.py` (add `policy`, `run`, `run_once`)
- Create: `tests/test_worker.py`

**Interfaces:**
- Consumes: `Policy`, `make_policy`, `Unit`, `decide`, `EvalSource`, `RecordStore`, `StripeEmitter`, `InMemoryRecordStore`, `FileEvalSource`
- Produces: `_REGISTRY`, `policy()`, `run_once()`, `run()`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_worker.py
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from tally.feed.file import FileEvalSource
from tally.log.memory import InMemoryRecordStore
from tally.worker import run_once

FIXTURE = Path(__file__).parent / "feed" / "fixtures" / "feed_fixture.json"


def _make_source_and_store() -> tuple[object, InMemoryRecordStore]:
    source = FileEvalSource(FIXTURE)
    store = InMemoryRecordStore()
    return source, store


def test_run_once_discovers_new_spans() -> None:
    """run_once with a FileEvalSource discovers spans and creates Units."""
    from tally.policy.declaration import make_policy
    from tally.worker import run_once

    def translate(evals: object) -> object:
        return bill(units=1)  # type: ignore[name-defined]

    policy = make_policy(
        "p", "post_review_comment", "customer_id",
        ["fix_landed"], translate,
    )
    source, store = _make_source_and_store()
    cursor = datetime(2024, 1, 1, tzinfo=timezone.utc)
    units = run_once(
        policies=[policy],
        source=source,
        store=store,
        cursor=cursor,
        stripe_key=None,
        mode="shadow",
    )
    # Should have discovered spans and written DECISION records
    decisions = store.list(type="DECISION")
    assert len(decisions) > 0


def test_run_once_skips_already_decided_spans() -> None:
    """Second run_once does not re-decide already-decided spans."""
    from tally.policy.declaration import make_policy
    from tally.worker import run_once

    def translate(evals: object) -> object:
        return bill(units=1)  # type: ignore[name-defined]

    policy = make_policy(
        "p", "post_review_comment", "customer_id",
        ["fix_landed"], translate,
    )
    source, store = _make_source_and_store()
    cursor = datetime(2024, 1, 1, tzinfo=timezone.utc)
    run_once(policies=[policy], source=source, store=store,
             cursor=cursor, stripe_key=None, mode="shadow")
    first_count = len(store.list(type="DECISION"))

    # Second run — cursor advanced past fixture spans
    run_once(policies=[policy], source=source, store=store,
             cursor=datetime(2025, 1, 1, tzinfo=timezone.utc),
             stripe_key=None, mode="shadow")
    assert len(store.list(type="DECISION")) == first_count  # no new decisions


def test_run_once_shadow_mode_writes_emissions() -> None:
    """Shadow mode: EMISSION records written, no Stripe call."""
    from tally.policy.declaration import make_policy
    from tally.worker import run_once

    def translate(evals: object) -> object:
        return bill(units=1)  # type: ignore[name-defined]

    policy = make_policy(
        "p", "post_review_comment", "customer_id",
        ["fix_landed"], translate,
    )
    source, store = _make_source_and_store()
    cursor = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with patch("requests.post") as mock_post:
        run_once(policies=[policy], source=source, store=store,
                 cursor=cursor, stripe_key=None, mode="shadow")
    mock_post.assert_not_called()
    emissions = store.list(type="EMISSION")
    assert len(emissions) > 0
    assert all(e.payload["shadow"] is True for e in emissions)
```

- [ ] **Step 2: Run to verify they fail**

```
py -3.12 -m pytest tests/test_worker.py -q
```
Expected: ImportError

- [ ] **Step 3: Implement `tally/sdk/registry.py`**

```python
from __future__ import annotations

from tally.policy.declaration import Policy

_REGISTRY: list[Policy] = []


def register(policy: Policy) -> None:
    _REGISTRY.append(policy)


def get_registry() -> list[Policy]:
    return list(_REGISTRY)
```

- [ ] **Step 4: Implement `tally/worker.py`**

```python
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from tally.emit.stripe import StripeEmitter
from tally.feed.source import EvalResult, EvalSource, SpanInfo, _EPOCH, _parse_dt
from tally.log.records import make_record
from tally.log.store import RecordStore
from tally.policy.declaration import Policy
from tally.policy.engine import Unit, decide

logger = logging.getLogger(__name__)

_GRACE = timedelta(hours=1)


def run_once(
    *,
    policies: list[Policy],
    source: EvalSource,
    store: RecordStore,
    cursor: datetime,
    stripe_key: str | None,
    mode: Literal["shadow", "live"],
) -> None:
    """Execute one four-stage worker cycle."""
    now = datetime.now(tz=timezone.utc)

    # Stage 1: Discover new spans; skip already-decided
    decided_spans = {
        (r.trace_id, r.span_id)
        for r in store.list(type="DECISION")
    }
    units_map: dict[tuple[str, str], Unit] = {}

    for policy in policies:
        try:
            spans = source.list_new_spans(policy, cursor)
        except Exception:
            logger.exception("list_new_spans failed policy=%s", policy.name)
            continue
        for span in spans:
            key = (span.trace_id, span.span_id)
            if key in decided_spans:
                continue
            units_map[key] = Unit(
                trace_id=span.trace_id,
                span_id=span.span_id,
                customer_id=span.customer_id,
                policy_name=policy.name,
                policy_version=policy.version,
                first_seen=now,
                deadline=span.ended_at + policy.wait + _GRACE,
            )

    if not units_map:
        return

    # Stage 2: Poll evals for each pending unit
    evals_cache: dict[tuple[str, str], list[EvalResult]] = {}
    for key, unit in units_map.items():
        try:
            evals_cache[key] = source.get_evals(unit.trace_id, unit.span_id)
        except Exception:
            logger.exception(
                "get_evals failed trace_id=%s span_id=%s", unit.trace_id, unit.span_id
            )

    # Stage 3: Run engine; write DECISION records
    policy_map = {p.name: p for p in policies}
    for key, unit in units_map.items():
        if key not in evals_cache:
            continue
        policy = policy_map.get(unit.policy_name)
        if policy is None:
            continue

        all_evals = {e.name: e for e in evals_cache[key]}
        declared_evals = {n: all_evals[n] for n in policy.evals if n in all_evals}
        watch_evals = {n: all_evals[n] for n in policy.watch if n in all_evals}

        decision = decide(policy, unit, declared_evals, watch_evals, now)
        if decision is None:
            continue

        store.write(make_record(
            type="DECISION",
            trace_id=unit.trace_id,
            span_id=unit.span_id,
            customer_id=unit.customer_id,
            policy_name=unit.policy_name,
            policy_version=unit.policy_version,
            policy_hash=policy.version,
            payload={
                "decision": decision.outcome,
                "units": decision.units,
                "reason": decision.reason,
                "review": decision.review,
                "decided_at": decision.decided_at.isoformat(),
                "eval_snapshot": decision.eval_snapshot,
                "translate_source": policy.translate,
            },
        ))
        unit.status = "decided"

    # Stage 4: Emit
    emitter = StripeEmitter(
        store=store,
        mode=mode,
        stripe_key=stripe_key,
        meter=policies[0].meter if policies else "default",
    )
    emitter.emit()


def run(
    *,
    policies: list[Policy],
    source: EvalSource,
    store: RecordStore,
    stripe_key: str | None,
    mode: Literal["shadow", "live"],
    sleep_seconds: int = 60,
) -> None:
    """Blocking worker loop. Runs run_once() every sleep_seconds forever."""
    cursor = _EPOCH
    while True:
        try:
            run_once(
                policies=policies,
                source=source,
                store=store,
                cursor=cursor,
                stripe_key=stripe_key,
                mode=mode,
            )
            # Advance cursor to now so next cycle only picks up truly new spans
            cursor = datetime.now(tz=timezone.utc)
        except Exception:
            logger.exception("run_once failed unexpectedly")
        time.sleep(sleep_seconds)
```

- [ ] **Step 5: Update `tally/__init__.py`**

Add after existing imports:

```python
from tally.sdk.registry import get_registry, register
from tally.policy.declaration import make_policy as _make_policy
from tally.policy.declaration import Policy as _Policy
from datetime import timedelta as _timedelta
from typing import Callable as _Callable

def policy(
    name: str,
    span: str,
    customer: str | _Callable[..., str],
    evals: list[str],
    translate: _Callable[..., object],
    watch: list[str] | None = None,
    wait: _timedelta = _timedelta(days=7),
    meter: str | None = None,
) -> _Policy:
    p = _make_policy(name, span, customer, evals, translate,
                     watch=watch, wait=wait, meter=meter)
    register(p)
    return p
```

- [ ] **Step 6: Run tests**

```
py -3.12 -m pytest tests/test_worker.py -q
```
Expected: 3 passed

- [ ] **Step 7: Run full suite + lint**

```
py -3.12 -m pytest tests/ -q && py -3.12 -m ruff check . && py -3.12 -m mypy tally/
```
Expected: all clean

- [ ] **Step 8: Commit**

```bash
git add tally/worker.py tally/sdk/registry.py tally/__init__.py tests/test_worker.py
git commit -m "feat: add four-stage worker loop and tally.policy() registry"
```

---

### Task 2: @tally.outcome decorator + context manager

**Files:**
- Create: `tally/sdk/outcome.py`
- Modify: `tally/__init__.py` (add `outcome`)
- Create: `tests/test_outcome.py`

**Interfaces:**
- Produces: `outcome` (decorator and context manager); always returns normally

The `@tally.outcome(span, customer)` decorator wraps a function call in try/except, returning normally regardless of exception. As a context manager it does the same. It does not actually write OTLP spans (that's the agent's job); it's a no-op wrapper that ensures the billing code never raises into the request path.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_outcome.py
from __future__ import annotations

import tally


def test_outcome_decorator_returns_normally() -> None:
    @tally.outcome("post_review_comment", customer="customer_id")
    def agent_fn(x: int) -> int:
        return x * 2

    result = agent_fn(5)
    assert result == 10


def test_outcome_decorator_catches_exceptions() -> None:
    @tally.outcome("post_review_comment", customer="customer_id")
    def failing_fn() -> int:
        raise RuntimeError("boom")

    result = failing_fn()
    assert result is None  # exception swallowed, returns None


def test_outcome_context_manager() -> None:
    results: list[int] = []
    with tally.outcome("post_review_comment", customer="customer_id"):
        results.append(42)
    assert results == [42]


def test_outcome_context_manager_catches_exceptions() -> None:
    with tally.outcome("post_review_comment", customer="customer_id"):
        raise RuntimeError("boom")
    # No exception raised out of context manager
```

- [ ] **Step 2: Run to verify they fail**

```
py -3.12 -m pytest tests/test_outcome.py -q
```
Expected: ImportError / AttributeError

- [ ] **Step 3: Implement `tally/sdk/outcome.py`**

```python
from __future__ import annotations

import functools
import logging
from types import TracebackType
from typing import Any, Callable

logger = logging.getLogger(__name__)


class outcome:
    """Decorator and context manager that ensures the wrapped code never raises.

    Used as: @tally.outcome("span_name", customer="attr_name")
    Or:      with tally.outcome("span_name", customer="attr_name"): ...
    """

    def __init__(self, span: str, *, customer: str) -> None:
        self._span = span
        self._customer = customer

    def __call__(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.exception(
                    "outcome swallowed exception span=%s", self._span
                )
                return None
        return wrapper

    def __enter__(self) -> outcome:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            logger.exception(
                "outcome swallowed exception span=%s", self._span
            )
        return True  # suppress exception
```

- [ ] **Step 4: Add `outcome` to `tally/__init__.py`**

```python
from tally.sdk.outcome import outcome as outcome
```

And add to `__all__`.

- [ ] **Step 5: Run tests**

```
py -3.12 -m pytest tests/test_outcome.py -q
```
Expected: 4 passed

- [ ] **Step 6: Run full suite + lint**

```
py -3.12 -m pytest tests/ -q && py -3.12 -m ruff check . && py -3.12 -m mypy tally/
```
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add tally/sdk/outcome.py tally/__init__.py tests/test_outcome.py
git commit -m "feat: add @tally.outcome decorator and context manager"
```

---

### Task 3: Demo + e2e shadow-mode test

**Files:**
- Create: `demo/__init__.py`
- Create: `demo/pricing.py`
- Create: `demo/agent.py`
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/test_shadow_run.py`

**Interfaces:**
- Consumes: `tally.policy`, `tally.outcome`, `tally.run_once`, `FileEvalSource`, `InMemoryRecordStore`
- The demo pricing.py registers one policy using `tally.policy()` and calls `tally.run_once()`

- [ ] **Step 1: Write the e2e test first**

```python
# tests/e2e/test_shadow_run.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tally.feed.file import FileEvalSource
from tally.log.memory import InMemoryRecordStore
from tally.worker import run_once
from tally.policy.declaration import make_policy

FIXTURE = Path(__file__).parent.parent / "feed" / "fixtures" / "feed_fixture.json"


def test_shadow_run_produces_decisions_and_emissions_no_stripe() -> None:
    """Full shadow-mode cycle: discover → poll → decide → emit, zero Stripe calls."""

    def translate(evals: object) -> object:
        return bill(units=1)  # type: ignore[name-defined]

    policy = make_policy(
        "fix-landed",
        "post_review_comment",
        "customer_id",
        ["fix_landed"],
        translate,
    )

    source = FileEvalSource(FIXTURE)
    store = InMemoryRecordStore()
    cursor = datetime(2024, 1, 1, tzinfo=timezone.utc)

    with patch("requests.post") as mock_post:
        run_once(
            policies=[policy],
            source=source,
            store=store,
            cursor=cursor,
            stripe_key=None,
            mode="shadow",
        )

    mock_post.assert_not_called()

    decisions = store.list(type="DECISION")
    emissions = store.list(type="EMISSION")

    assert len(decisions) > 0, "expected at least one DECISION"
    assert len(emissions) > 0, "expected at least one shadow EMISSION"
    assert all(d.payload["decision"] in ("BILL", "NO_CHARGE", "HOLD", "ESCALATE")
               for d in decisions)
    assert all(e.payload["shadow"] is True for e in emissions)
```

- [ ] **Step 2: Run to verify it fails**

```
py -3.12 -m pytest tests/e2e/test_shadow_run.py -q
```
Expected: FAIL (no decisions produced yet without correct fixture evals)

If the test passes immediately, that's fine — the fixture has evals.

- [ ] **Step 3: Create demo files**

```python
# demo/__init__.py
# (empty)
```

```python
# demo/agent.py
"""Minimal demo agent using @tally.outcome."""
from __future__ import annotations

import tally


@tally.outcome("post_review_comment", customer="customer_id")
def review_pr(pr_id: str, customer_id: str) -> str:
    """Simulate a PR review agent call."""
    return f"Reviewed PR {pr_id} for customer {customer_id}"


if __name__ == "__main__":
    result = review_pr("PR-001", customer_id="cust-demo")
    print(result)
```

```python
# demo/pricing.py
"""Demo pricing file. Run against fixtures: python demo/pricing.py"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import tally
from tally.feed.file import FileEvalSource
from tally.log.memory import InMemoryRecordStore
from tally.worker import run_once


@tally.outcome("post_review_comment", customer="customer_id")
def translate(evals: object) -> object:
    return tally.bill(units=1)


policy = tally.policy(
    name="fix-landed",
    span="post_review_comment",
    customer="customer_id",
    evals=["fix_landed"],
    translate=translate,
)

if __name__ == "__main__":
    fixture = Path(__file__).parent / "fixtures" / "feed_fixture.json"
    source = FileEvalSource(fixture)
    store = InMemoryRecordStore()
    cursor = datetime(2024, 1, 1, tzinfo=timezone.utc)

    run_once(
        policies=[policy],
        source=source,
        store=store,
        cursor=cursor,
        stripe_key=None,
        mode="shadow",
    )

    decisions = store.list(type="DECISION")
    emissions = store.list(type="EMISSION")
    print(f"Decisions: {len(decisions)}, Emissions (shadow): {len(emissions)}")
    for d in decisions:
        print(f"  {d.trace_id} → {d.payload['decision']}")
```

- [ ] **Step 4: Create `demo/fixtures/` directory with symlink to test fixture**

Copy the fixture:
```bash
mkdir -p demo/fixtures
cp tests/feed/fixtures/feed_fixture.json demo/fixtures/feed_fixture.json
```

- [ ] **Step 5: Run the e2e test**

```
py -3.12 -m pytest tests/e2e/test_shadow_run.py -q
```
Expected: 1 passed

- [ ] **Step 6: Run full suite + lint**

```
py -3.12 -m pytest tests/ -q && py -3.12 -m ruff check . && py -3.12 -m mypy tally/
```
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add demo/ tests/e2e/
git commit -m "feat: add demo agent, pricing file, and e2e shadow-mode test"
```

---

### Task 4: CI/CD workflows

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/publish.yml`
- Create: `.github/workflows/quickstart.yml`

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: mypy tally/
      - run: pytest tests/ --cov=tally --cov-fail-under=80 -q
```

- [ ] **Step 2: Create `.github/workflows/publish.yml`**

```yaml
name: Publish to PyPI

on:
  push:
    tags:
      - "v*"

jobs:
  build-and-publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

- [ ] **Step 3: Create `.github/workflows/quickstart.yml`**

```yaml
name: Quickstart DX

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  quickstart:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .
      - run: python demo/pricing.py
```

- [ ] **Step 4: Run full test suite (CI smoke-check)**

```
py -3.12 -m pytest tests/ -q && py -3.12 -m ruff check . && py -3.12 -m mypy tally/
```
Expected: all clean

- [ ] **Step 5: Commit**

```bash
git add .github/
git commit -m "feat: add CI, publish, and quickstart GitHub Actions workflows"
```
