# Tally Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Stripe emitter with shadow/live mode gate, per-customer ThreadPoolExecutor serialization, EMISSION record writing, and old-decision guard.

**Architecture:** `StripeEmitter` reads BILL decisions, groups by customer, dispatches via ThreadPoolExecutor (one thread per customer to satisfy Stripe's per-customer serialization requirement), writes EMISSION records. Mode gate skips shadow-period decisions.

**Tech Stack:** Python 3.12, requests, concurrent.futures.ThreadPoolExecutor

## Global Constraints

- Python 3.12, `from __future__ import annotations` in every file
- No new runtime dependencies (requests already declared)
- `STRIPE_RESTRICTED_KEY` read from env; not needed in shadow mode
- Shadow mode: `EMISSION(shadow=True)` written, no Stripe HTTP call
- Live mode: POST to Stripe, then write `EMISSION(shadow=False, status="sent")`
- Old decision guard: BILL decisions older than 35 days with no EMISSION → write `EMISSION(status="failed")`, no retry
- Mode gate: only BILL decisions with `decided_at > latest MODE=live record timestamp` are emitted
- `ruff check` and `mypy --strict` must pass
- Tests run with: `py -3.12 -m pytest tests/ -q`

---

### Task 1: StripeEmitter (shadow mode + EMISSION records)

**Files:**
- Create: `tally/emit/__init__.py`
- Create: `tally/emit/stripe.py`
- Create: `tests/emit/__init__.py`
- Create: `tests/emit/test_stripe.py`

**Interfaces:**
- Consumes: `Record`, `make_record`, `RecordStore`, `InMemoryRecordStore` from tally.log
- Produces: `StripeEmitter`, `emit_bill_decisions()` (used by Phase 5 worker)

Stripe meter event API: `POST https://api.stripe.com/v1/billing/meter_events` with form-encoded body:
```
event_name=<policy.meter>
stripe_customer_id=<customer_id>
value=<units>
identifier=<trace_id>::<span_id>
timestamp=<unix_epoch_int>
```
Auth: `Authorization: Bearer <STRIPE_RESTRICTED_KEY>`

- [ ] **Step 1: Write failing tests**

```python
# tests/emit/test_stripe.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from tally.emit.stripe import StripeEmitter
from tally.log.memory import InMemoryRecordStore
from tally.log.records import Record, make_record


def _bill_decision(
    trace_id: str = "t1",
    span_id: str = "s1",
    customer_id: str = "cust-1",
    policy_name: str = "p",
    decided_at: str = "2024-06-01T12:00:00Z",
    units: int = 1,
) -> Record:
    return make_record(
        type="DECISION",
        trace_id=trace_id, span_id=span_id, customer_id=customer_id,
        policy_name=policy_name, policy_version="v1", policy_hash="h1",
        payload={
            "decision": "BILL", "units": units,
            "decided_at": decided_at,
        },
    )


def _mock_stripe_ok() -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {"id": "evt_001", "object": "billing.meter_event"}
    return m


def test_shadow_mode_writes_emission_no_http() -> None:
    store = InMemoryRecordStore()
    store.write(_bill_decision())
    emitter = StripeEmitter(store=store, mode="shadow", stripe_key=None, meter="p")
    with patch("requests.post") as mock_post:
        emitter.emit()
    mock_post.assert_not_called()
    emissions = store.list(type="EMISSION")
    assert len(emissions) == 1
    assert emissions[0].payload["shadow"] is True


def test_live_mode_posts_to_stripe_and_writes_emission() -> None:
    store = InMemoryRecordStore()
    store.write(_bill_decision())
    emitter = StripeEmitter(store=store, mode="live", stripe_key="sk_test_xxx", meter="p")
    with patch("requests.post", return_value=_mock_stripe_ok()):
        emitter.emit()
    emissions = store.list(type="EMISSION")
    assert len(emissions) == 1
    e = emissions[0]
    assert e.payload["shadow"] is False
    assert e.payload["status"] == "sent"
    assert e.payload["stripe_identifier"] == "t1::s1"


def test_already_emitted_decision_is_skipped() -> None:
    store = InMemoryRecordStore()
    store.write(_bill_decision())
    # Pre-write an EMISSION to simulate already-processed
    store.write(make_record(
        type="EMISSION", trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"stripe_identifier": "t1::s1", "shadow": True, "status": "sent"},
    ))
    emitter = StripeEmitter(store=store, mode="shadow", stripe_key=None, meter="p")
    with patch("requests.post") as mock_post:
        emitter.emit()
    mock_post.assert_not_called()
    assert len(store.list(type="EMISSION")) == 1  # no new emission


def test_non_bill_decision_is_skipped() -> None:
    store = InMemoryRecordStore()
    store.write(make_record(
        type="DECISION", trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"decision": "NO_CHARGE", "decided_at": "2024-06-01T12:00:00Z"},
    ))
    emitter = StripeEmitter(store=store, mode="shadow", stripe_key=None, meter="p")
    with patch("requests.post") as mock_post:
        emitter.emit()
    mock_post.assert_not_called()
    assert store.list(type="EMISSION") == []


def test_old_decision_guard_writes_failed_emission() -> None:
    """BILL decisions older than 35 days get EMISSION(status='failed'), no Stripe call."""
    store = InMemoryRecordStore()
    store.write(_bill_decision(decided_at="2020-01-01T00:00:00Z"))  # very old
    emitter = StripeEmitter(store=store, mode="live", stripe_key="sk_test_xxx", meter="p")
    with patch("requests.post") as mock_post:
        emitter.emit()
    mock_post.assert_not_called()
    emissions = store.list(type="EMISSION")
    assert len(emissions) == 1
    assert emissions[0].payload["status"] == "failed"


def test_live_mode_requires_stripe_key() -> None:
    """Constructing StripeEmitter in live mode without a key raises immediately."""
    store = InMemoryRecordStore()
    with pytest.raises(ValueError, match="STRIPE_RESTRICTED_KEY"):
        StripeEmitter(store=store, mode="live", stripe_key=None, meter="p")
```

- [ ] **Step 2: Run tests to verify they fail**

```
py -3.12 -m pytest tests/emit/test_stripe.py -q
```
Expected: ImportError

- [ ] **Step 3: Create `tally/emit/__init__.py`** and `tests/emit/__init__.py` (empty)

- [ ] **Step 4: Implement `tally/emit/stripe.py`**

```python
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Literal

import requests

from tally.feed.source import _parse_dt
from tally.log.records import make_record
from tally.log.store import RecordStore

logger = logging.getLogger(__name__)

_STRIPE_METER_URL = "https://api.stripe.com/v1/billing/meter_events"
_OLD_DECISION_THRESHOLD = timedelta(days=35)


class StripeEmitter:
    def __init__(
        self,
        *,
        store: RecordStore,
        mode: Literal["shadow", "live"],
        stripe_key: str | None,
        meter: str,
    ) -> None:
        if mode == "live" and not stripe_key:
            raise ValueError("STRIPE_RESTRICTED_KEY is required in live mode")
        self._store = store
        self._mode = mode
        self._stripe_key = stripe_key
        self._meter = meter

    def emit(self) -> None:
        """Process all pending BILL decisions: emit Stripe events or shadow-write."""
        now = datetime.now(tz=timezone.utc)
        decisions = self._store.list(type="DECISION")
        emitted_spans = {
            (r.trace_id, r.span_id)
            for r in self._store.list(type="EMISSION")
        }

        # Collect un-emitted BILL decisions
        pending: list[object] = []  # list[Record]
        for r in decisions:
            if r.payload.get("decision") != "BILL":
                continue
            if (r.trace_id, r.span_id) in emitted_spans:
                continue
            pending.append(r)

        if not pending:
            return

        # Group by customer for per-customer serialization
        by_customer: dict[str, list[object]] = {}
        for r in pending:
            by_customer.setdefault(r.customer_id, []).append(r)  # type: ignore[attr-defined]

        # Dispatch one thread per customer
        with ThreadPoolExecutor(max_workers=len(by_customer)) as executor:
            for customer_records in by_customer.values():
                executor.submit(self._emit_customer, customer_records, now)  # type: ignore[arg-type]

    def _emit_customer(self, records: list[object], now: datetime) -> None:  # list[Record]
        for r in records:
            r = r  # type: ignore[assignment]  # Record
            decided_at_str = str(r.payload.get("decided_at", ""))  # type: ignore[attr-defined]
            try:
                decided_at = _parse_dt(decided_at_str) if decided_at_str else now
            except ValueError:
                decided_at = now

            # Old decision guard
            if now - decided_at > _OLD_DECISION_THRESHOLD:
                self._store.write(make_record(
                    type="EMISSION",
                    trace_id=r.trace_id,  # type: ignore[attr-defined]
                    span_id=r.span_id,  # type: ignore[attr-defined]
                    customer_id=r.customer_id,  # type: ignore[attr-defined]
                    policy_name=r.policy_name,  # type: ignore[attr-defined]
                    policy_version=r.policy_version,  # type: ignore[attr-defined]
                    policy_hash=r.policy_hash,  # type: ignore[attr-defined]
                    payload={
                        "stripe_identifier": f"{r.trace_id}::{r.span_id}",  # type: ignore[attr-defined]
                        "shadow": False,
                        "status": "failed",
                    },
                ))
                logger.warning(
                    "BILL decision too old to emit trace_id=%s span_id=%s",
                    r.trace_id,  # type: ignore[attr-defined]
                    r.span_id,  # type: ignore[attr-defined]
                )
                continue

            identifier = f"{r.trace_id}::{r.span_id}"  # type: ignore[attr-defined]
            units = int(r.payload.get("units", 1))  # type: ignore[attr-defined]

            if self._mode == "shadow":
                self._store.write(make_record(
                    type="EMISSION",
                    trace_id=r.trace_id,  # type: ignore[attr-defined]
                    span_id=r.span_id,  # type: ignore[attr-defined]
                    customer_id=r.customer_id,  # type: ignore[attr-defined]
                    policy_name=r.policy_name,  # type: ignore[attr-defined]
                    policy_version=r.policy_version,  # type: ignore[attr-defined]
                    policy_hash=r.policy_hash,  # type: ignore[attr-defined]
                    payload={
                        "stripe_identifier": identifier,
                        "shadow": True,
                        "status": "shadow",
                    },
                ))
            else:
                self._post_stripe(r, identifier, units, decided_at)  # type: ignore[arg-type]

    def _post_stripe(
        self, r: object, identifier: str, units: int, decided_at: datetime
    ) -> None:
        try:
            resp = requests.post(
                _STRIPE_METER_URL,
                headers={"Authorization": f"Bearer {self._stripe_key}"},
                data={
                    "event_name": self._meter,
                    "stripe_customer_id": r.customer_id,  # type: ignore[attr-defined]
                    "value": str(units),
                    "identifier": identifier,
                    "timestamp": str(int(decided_at.timestamp())),
                },
                timeout=30,
            )
            resp.raise_for_status()
            stripe_response = resp.json()
            status = "sent"
        except Exception:
            logger.exception(
                "Stripe emit failed trace_id=%s span_id=%s",
                r.trace_id,  # type: ignore[attr-defined]
                r.span_id,  # type: ignore[attr-defined]
            )
            stripe_response = {}
            status = "failed"

        self._store.write(make_record(
            type="EMISSION",
            trace_id=r.trace_id,  # type: ignore[attr-defined]
            span_id=r.span_id,  # type: ignore[attr-defined]
            customer_id=r.customer_id,  # type: ignore[attr-defined]
            policy_name=r.policy_name,  # type: ignore[attr-defined]
            policy_version=r.policy_version,  # type: ignore[attr-defined]
            policy_hash=r.policy_hash,  # type: ignore[attr-defined]
            payload={
                "stripe_identifier": identifier,
                "stripe_response": stripe_response,
                "shadow": False,
                "status": status,
            },
        ))
```

Note: The type: ignore comments are because we're using `object` to avoid importing Record (circular-ish) — replace with proper typing:

Actually, let's type it properly by importing Record:

```python
from tally.log.records import Record, make_record

# Replace object → Record in method signatures
def emit(self) -> None:
    ...
    pending: list[Record] = []
    for r in decisions:
        ...
        pending.append(r)

    by_customer: dict[str, list[Record]] = {}

def _emit_customer(self, records: list[Record], now: datetime) -> None:
    for r in records:
        ...

def _post_stripe(self, r: Record, ...) -> None:
    ...
```

- [ ] **Step 5: Run tests**

```
py -3.12 -m pytest tests/emit/test_stripe.py -q
```
Expected: 6 passed

- [ ] **Step 6: Run full suite + lint**

```
py -3.12 -m pytest tests/ -q && py -3.12 -m ruff check . && py -3.12 -m mypy tally/
```
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add tally/emit/__init__.py tally/emit/stripe.py tests/emit/__init__.py tests/emit/test_stripe.py
git commit -m "feat: add StripeEmitter with shadow/live mode and old-decision guard"
```

---

### Task 2: Mode gate (shadow-period decisions skipped in live mode)

**Files:**
- Modify: `tally/emit/stripe.py`
- Modify: `tests/emit/test_stripe.py`

**Interfaces:**
- Consumes: `RecordStore.list(type="MODE")`, MODE record payload with `mode` and `decided_at`

The mode gate: only BILL decisions with `decided_at > latest MODE=live record timestamp` are emitted in live mode. Shadow-period decisions (before live-mode switch) get `EMISSION(shadow=True)` permanently.

- [ ] **Step 1: Write failing tests for mode gate**

Add to `tests/emit/test_stripe.py`:

```python
def test_mode_gate_skips_shadow_period_decisions() -> None:
    """Decisions made before the live-mode switch get EMISSION(shadow=True), no Stripe call."""
    store = InMemoryRecordStore()
    # Write MODE=live record at 2024-06-01T14:00:00Z
    store.write(make_record(
        type="MODE", trace_id="", span_id="", customer_id="",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"mode": "live", "switched_at": "2024-06-01T14:00:00Z"},
    ))
    # Write BILL decision at 2024-06-01T12:00:00Z — BEFORE live-mode switch
    store.write(_bill_decision(decided_at="2024-06-01T12:00:00Z"))
    emitter = StripeEmitter(store=store, mode="live", stripe_key="sk_test_xxx", meter="p")
    with patch("requests.post") as mock_post:
        emitter.emit()
    mock_post.assert_not_called()
    emissions = store.list(type="EMISSION")
    assert len(emissions) == 1
    assert emissions[0].payload["shadow"] is True


def test_mode_gate_allows_post_live_decisions() -> None:
    """Decisions after the live-mode switch ARE emitted."""
    store = InMemoryRecordStore()
    store.write(make_record(
        type="MODE", trace_id="", span_id="", customer_id="",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"mode": "live", "switched_at": "2024-06-01T10:00:00Z"},
    ))
    # Decision AFTER live-mode switch
    store.write(_bill_decision(decided_at="2024-06-01T12:00:00Z"))
    emitter = StripeEmitter(store=store, mode="live", stripe_key="sk_test_xxx", meter="p")
    with patch("requests.post", return_value=_mock_stripe_ok()):
        emitter.emit()
    emissions = store.list(type="EMISSION")
    assert len(emissions) == 1
    assert emissions[0].payload["shadow"] is False
    assert emissions[0].payload["status"] == "sent"
```

- [ ] **Step 2: Run tests to verify they fail**

```
py -3.12 -m pytest tests/emit/test_stripe.py::test_mode_gate_skips_shadow_period_decisions tests/emit/test_stripe.py::test_mode_gate_allows_post_live_decisions -q
```
Expected: FAIL (no mode gate logic yet)

- [ ] **Step 3: Add mode gate to `StripeEmitter.emit()`**

In `emit()`, after collecting decisions, add:

```python
# Mode gate: find latest live-mode switch timestamp
live_since: datetime | None = None
if self._mode == "live":
    mode_records = self._store.list(type="MODE")
    live_switches = [
        _parse_dt(str(r.payload["switched_at"]))
        for r in mode_records
        if r.payload.get("mode") == "live" and "switched_at" in r.payload
    ]
    if live_switches:
        live_since = max(live_switches)

# Filter pending: in live mode, shadow-period decisions → shadow EMISSION immediately
final_pending: list[Record] = []
for r in pending:
    decided_at_str = str(r.payload.get("decided_at", ""))
    decided_at = _parse_dt(decided_at_str) if decided_at_str else now
    if live_since is not None and decided_at <= live_since:
        # Shadow-period decision: permanently skip with shadow=True
        self._store.write(make_record(
            type="EMISSION",
            trace_id=r.trace_id, span_id=r.span_id, customer_id=r.customer_id,
            policy_name=r.policy_name, policy_version=r.policy_version,
            policy_hash=r.policy_hash,
            payload={
                "stripe_identifier": f"{r.trace_id}::{r.span_id}",
                "shadow": True,
                "status": "shadow",
            },
        ))
    else:
        final_pending.append(r)
```

Then use `final_pending` instead of `pending` for the ThreadPoolExecutor grouping.

- [ ] **Step 4: Run all emit tests**

```
py -3.12 -m pytest tests/emit/ -q
```
Expected: 8 passed

- [ ] **Step 5: Run full suite + lint**

```
py -3.12 -m pytest tests/ -q && py -3.12 -m ruff check . && py -3.12 -m mypy tally/
```
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add tally/emit/stripe.py tests/emit/test_stripe.py
git commit -m "feat: add mode gate to skip shadow-period BILL decisions in live mode"
```
