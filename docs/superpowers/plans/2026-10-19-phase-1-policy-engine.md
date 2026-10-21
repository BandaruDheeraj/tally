# Tally Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Policy declaration, version hashing, and pure decision engine with all 7 rules and table-driven tests — zero external runtime dependencies.

**Architecture:** Three modules in `tally/policy/`: `declaration.py` holds frozen dataclasses (Policy, BillResult, NoChargeResult, HoldResult) and the version-hash factory; `engine.py` holds Unit, Decision, and the pure `decide()` function with sandboxed translate execution. A thin `tally/feed/source.py` defines EvalResult and SpanInfo as plain frozen dataclasses; the feed ABCs are scaffolded in Phase 2.

**Tech Stack:** Python 3.12, hatchling (build), pytest 8, ruff 0.4, mypy 1.10 (strict). Zero runtime dependencies in Phase 1.

## Global Constraints

- Python `>=3.12` only — use `str | None` union syntax, `match` if needed, no `from __future__ import annotations` required but harmless.
- Package name on PyPI: `tally-billing`; imported as `tally`.
- All public dataclasses are `frozen=True`. Unit is the sole exception (status mutates in-memory).
- No database, no CLI, no config files beyond environment variables.
- `tally.bill`, `tally.no_charge`, `tally.hold` are importable from the top-level `tally` namespace by end of Phase 1.
- License: Apache-2.0.
- `ruff check` and `mypy --strict` must pass with zero errors on every commit.

---

## File Map

| File | Responsibility |
|------|---------------|
| `pyproject.toml` | Build config, dev deps, ruff + mypy settings |
| `tally/__init__.py` | Re-exports: `bill`, `no_charge`, `hold` |
| `tally/feed/__init__.py` | Empty package marker |
| `tally/feed/source.py` | `EvalResult`, `SpanInfo` frozen dataclasses |
| `tally/policy/__init__.py` | Empty package marker |
| `tally/policy/declaration.py` | `BillResult`, `NoChargeResult`, `HoldResult`, `Policy`, `compute_version`, `make_policy` |
| `tally/policy/engine.py` | `Unit`, `Decision`, `_run_translate`, `decide` |
| `tests/__init__.py` | Empty |
| `tests/policy/__init__.py` | Empty |
| `tests/feed/__init__.py` | Empty (Phase 2 will add tests here) |
| `tests/policy/test_declaration.py` | Policy construction + version hashing tests |
| `tests/policy/test_engine.py` | Table-driven tests for all 7 engine rules |

---

### Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `tally/__init__.py`
- Create: `tally/feed/__init__.py`
- Create: `tally/feed/source.py`
- Create: `tally/policy/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/policy/__init__.py`
- Create: `tests/feed/__init__.py`

**Interfaces:**
- Produces: `EvalResult`, `SpanInfo` importable from `tally.feed.source`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "tally-billing"
version = "0.1.0"
description = "Turn Arize AX eval results into Stripe meter events"
readme = "README.md"
requires-python = ">=3.12"
license = "Apache-2.0"
dependencies = []

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "mypy>=1.10",
]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "S"]

[tool.mypy]
python_version = "3.12"
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty package markers**

`tally/__init__.py` — leave empty for now (Task 3 fills it):
```python
```

`tally/feed/__init__.py`:
```python
```

`tally/policy/__init__.py`:
```python
```

`tests/__init__.py`:
```python
```

`tests/policy/__init__.py`:
```python
```

`tests/feed/__init__.py`:
```python
```

- [ ] **Step 3: Create `tally/feed/source.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
    agreement: str | None  # "unanimous" | "split" | None
    evaluator_version: str | None
    scored_at: datetime
    raw: dict[str, object]
```

- [ ] **Step 4: Install dev dependencies and verify package is importable**

```bash
pip install -e ".[dev]"
python -c "from tally.feed.source import EvalResult, SpanInfo; print('ok')"
```

Expected output: `ok`

- [ ] **Step 5: Commit**

```bash
git init
git add pyproject.toml tally/ tests/
git commit -m "feat: scaffold project and define EvalResult/SpanInfo"
```

---

### Task 2: Policy Declaration + Version Hashing

**Files:**
- Create: `tally/policy/declaration.py`
- Create: `tests/policy/test_declaration.py`

**Interfaces:**
- Consumes: nothing (pure stdlib)
- Produces:
  - `BillResult(units: int)` — frozen dataclass
  - `NoChargeResult(reason: str)` — frozen dataclass
  - `HoldResult(reason: str)` — frozen dataclass
  - `Policy` — frozen dataclass with fields: `name`, `span`, `customer`, `evals`, `watch`, `translate` (source str), `version` (SHA-256 hex), `meter`, `wait`
  - `compute_version(name, span, evals, watch, translate_source) -> str`
  - `make_policy(name, span, customer, evals, translate, watch, wait, meter) -> Policy`

- [ ] **Step 1: Write the failing tests**

`tests/policy/test_declaration.py`:
```python
import hashlib
import inspect
import json
from datetime import timedelta

import pytest

from tally.policy.declaration import (
    BillResult,
    HoldResult,
    NoChargeResult,
    Policy,
    compute_version,
    make_policy,
)


# ── result type construction ──────────────────────────────────────────────────


def test_bill_result():
    r = BillResult(units=5)
    assert r.units == 5


def test_no_charge_result():
    r = NoChargeResult(reason="free tier")
    assert r.reason == "free tier"


def test_hold_result():
    r = HoldResult(reason="needs review")
    assert r.reason == "needs review"


def test_results_are_frozen():
    r = BillResult(units=1)
    with pytest.raises(Exception):
        r.units = 2  # type: ignore[misc]


# ── make_policy fields ────────────────────────────────────────────────────────


def _translate_bill(evals):  # type: ignore[no-untyped-def]
    return bill(units=1)  # noqa: F821


def _translate_no_charge(evals):  # type: ignore[no-untyped-def]
    return no_charge(reason="free")  # noqa: F821


def test_make_policy_basic_fields():
    p = make_policy(
        name="review_comment",
        span="post_review_comment",
        customer="customer_id",
        evals=["fix_landed"],
        translate=_translate_bill,
    )
    assert p.name == "review_comment"
    assert p.span == "post_review_comment"
    assert p.customer == "customer_id"
    assert p.evals == ["fix_landed"]
    assert p.watch == []
    assert p.meter == "review_comment"  # defaults to name
    assert p.wait == timedelta(days=7)
    assert isinstance(p.translate, str)
    assert len(p.version) == 64  # SHA-256 hex digest


def test_make_policy_meter_defaults_to_name():
    p = make_policy("my_meter", "s", "c", ["e"], _translate_bill)
    assert p.meter == "my_meter"


def test_make_policy_meter_override():
    p = make_policy("n", "s", "c", ["e"], _translate_bill, meter="custom_meter")
    assert p.meter == "custom_meter"


def test_make_policy_stores_translate_source():
    p = make_policy("n", "s", "c", ["e"], _translate_bill)
    source = inspect.getsource(_translate_bill)
    assert p.translate == source


def test_make_policy_customer_callable():
    def get_customer(attrs: dict[str, str]) -> str:
        return attrs["tenant_id"]

    p = make_policy("n", "s", get_customer, ["e"], _translate_bill)
    assert callable(p.customer)


def test_policy_is_frozen():
    p = make_policy("n", "s", "c", ["e"], _translate_bill)
    with pytest.raises(Exception):
        p.name = "other"  # type: ignore[misc]


# ── version hashing ───────────────────────────────────────────────────────────


def test_version_deterministic():
    p1 = make_policy("n", "s", "c", ["e"], _translate_bill)
    p2 = make_policy("n", "s", "c", ["e"], _translate_bill)
    assert p1.version == p2.version


def test_version_matches_manual_hash():
    source = inspect.getsource(_translate_bill)
    expected = hashlib.sha256(
        json.dumps(
            {"name": "n", "span": "s", "evals": ["e"], "watch": [], "translate": source},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    p = make_policy("n", "s", "c", ["e"], _translate_bill)
    assert p.version == expected


def test_version_changes_on_name():
    p1 = make_policy("n1", "s", "c", ["e"], _translate_bill)
    p2 = make_policy("n2", "s", "c", ["e"], _translate_bill)
    assert p1.version != p2.version


def test_version_changes_on_span():
    p1 = make_policy("n", "s1", "c", ["e"], _translate_bill)
    p2 = make_policy("n", "s2", "c", ["e"], _translate_bill)
    assert p1.version != p2.version


def test_version_changes_on_evals():
    p1 = make_policy("n", "s", "c", ["e1"], _translate_bill)
    p2 = make_policy("n", "s", "c", ["e2"], _translate_bill)
    assert p1.version != p2.version


def test_version_changes_on_watch():
    p1 = make_policy("n", "s", "c", ["e"], _translate_bill, watch=[])
    p2 = make_policy("n", "s", "c", ["e"], _translate_bill, watch=["sentiment"])
    assert p1.version != p2.version


def test_version_changes_on_translate_body():
    p1 = make_policy("n", "s", "c", ["e"], _translate_bill)
    p2 = make_policy("n", "s", "c", ["e"], _translate_no_charge)
    assert p1.version != p2.version
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/policy/test_declaration.py -v
```

Expected: all fail with `ModuleNotFoundError: No module named 'tally.policy.declaration'`

- [ ] **Step 3: Implement `tally/policy/declaration.py`**

```python
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable


@dataclass(frozen=True)
class BillResult:
    units: int


@dataclass(frozen=True)
class NoChargeResult:
    reason: str


@dataclass(frozen=True)
class HoldResult:
    reason: str


@dataclass(frozen=True)
class Policy:
    name: str
    span: str
    customer: str | Callable[[dict[str, object]], str]
    evals: list[str]
    watch: list[str]
    translate: str  # source text of the translate function
    version: str  # SHA-256 hex
    meter: str
    wait: timedelta


def compute_version(
    name: str,
    span: str,
    evals: list[str],
    watch: list[str],
    translate_source: str,
) -> str:
    """Return SHA-256 hex of the canonical policy declaration fields."""
    payload = json.dumps(
        {
            "name": name,
            "span": span,
            "evals": evals,
            "watch": watch,
            "translate": translate_source,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def make_policy(
    name: str,
    span: str,
    customer: str | Callable[[dict[str, object]], str],
    evals: list[str],
    translate: Callable[..., object],
    watch: list[str] | None = None,
    wait: timedelta = timedelta(days=7),
    meter: str | None = None,
) -> Policy:
    """Build a Policy from a translate callable, extracting source and computing version."""
    _watch = watch if watch is not None else []
    source = inspect.getsource(translate)
    version = compute_version(name, span, evals, _watch, source)
    return Policy(
        name=name,
        span=span,
        customer=customer,
        evals=evals,
        watch=_watch,
        translate=source,
        version=version,
        meter=meter if meter is not None else name,
        wait=wait,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/policy/test_declaration.py -v
```

Expected: all 15 tests pass.

- [ ] **Step 5: Run linter and type checker**

```bash
ruff check tally/policy/declaration.py tests/policy/test_declaration.py
mypy tally/policy/declaration.py
```

Expected: zero errors. If mypy complains about `Callable` without type params, add `# type: ignore[type-arg]` on that line only.

- [ ] **Step 6: Commit**

```bash
git add tally/policy/declaration.py tests/policy/test_declaration.py
git commit -m "feat: add Policy declaration, version hashing, and result types"
```

---

### Task 3: Policy Engine + All 7 Rules

**Files:**
- Create: `tally/policy/engine.py`
- Create: `tests/policy/test_engine.py`
- Modify: `tally/__init__.py` — add re-exports

**Interfaces:**
- Consumes:
  - `EvalResult` from `tally.feed.source`
  - `Policy`, `BillResult`, `NoChargeResult`, `HoldResult`, `compute_version` from `tally.policy.declaration`
- Produces:
  - `Unit` — mutable dataclass (status changes in-memory)
  - `Decision(outcome, units, reason, review, decided_at, eval_snapshot)` — frozen dataclass
  - `decide(policy, unit, evals, watch, now) -> Decision | None`

- [ ] **Step 1: Write the failing tests**

`tests/policy/test_engine.py`:
```python
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tally.feed.source import EvalResult
from tally.policy.declaration import Policy, compute_version
from tally.policy.engine import Decision, Unit, decide

# ── shared helpers ────────────────────────────────────────────────────────────

NOW = datetime(2024, 6, 1, 12, 0)


def _eval(
    name: str,
    label: str = "yes",
    agreement: str | None = "unanimous",
) -> EvalResult:
    return EvalResult(
        name=name,
        label=label,
        score=1.0,
        explanation="",
        agreement=agreement,
        evaluator_version=None,
        scored_at=datetime(2024, 1, 1),
        raw={},
    )


def _policy(translate_source: str, watch: list[str] | None = None) -> Policy:
    """Build a Policy directly from source text — no inspect.getsource needed."""
    _watch = watch or []
    version = compute_version("p", "my_span", ["quality"], _watch, translate_source)
    return Policy(
        name="p",
        span="my_span",
        customer="customer_id",
        evals=["quality"],
        watch=_watch,
        translate=translate_source,
        version=version,
        meter="p",
        wait=timedelta(days=7),
    )


def _unit(policy: Policy, deadline_days: float = 1.0) -> Unit:
    return Unit(
        trace_id="trace-abc",
        span_id="span-xyz",
        customer_id="cust-1",
        policy_name=policy.name,
        policy_version=policy.version,
        first_seen=NOW,
        deadline=NOW + timedelta(days=deadline_days),
    )


# ── Rule 1: stay pending when declared eval missing and deadline not passed ───


def test_rule1_pending_missing_eval_before_deadline() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=1)\n")
    unit = _unit(policy, deadline_days=1.0)
    result = decide(policy, unit, evals={}, watch={}, now=NOW)
    assert result is None


# ── Rule 2: NO_CHARGE when declared eval missing and deadline passed ──────────


def test_rule2_no_charge_missing_eval_after_deadline() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=1)\n")
    unit = _unit(policy, deadline_days=-1.0)  # deadline 1 day in the past
    result = decide(policy, unit, evals={}, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "NO_CHARGE"
    assert result.reason == "unscored"


# ── Rule 3: ESCALATE when declared eval has split agreement ──────────────────


def test_rule3_escalate_on_split_declared_eval() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=1)\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality", agreement="split")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "ESCALATE"


# ── Rule 4a: HOLD when translate raises an exception ─────────────────────────


def test_rule4a_hold_translate_raises() -> None:
    policy = _policy("def translate(evals):\n    raise ValueError('bad input')\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "HOLD"
    assert result.reason == "translate error"


# ── Rule 4b: HOLD when translate returns an invalid type ─────────────────────


def test_rule4b_hold_translate_invalid_return_type() -> None:
    policy = _policy("def translate(evals):\n    return 42\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "HOLD"
    assert result.reason == "translate error"


# ── Rule 4c: HOLD when translate returns different values on two calls ────────
# Uses a mutable default argument to increment a counter across calls.
# Since exec runs once and the function is called twice in the same namespace,
# the mutable default persists and produces different results on the second call.


def test_rule4c_hold_translate_nondeterministic() -> None:
    src = (
        "def translate(evals, _state=[0]):\n"
        "    _state[0] += 1\n"
        "    return bill(units=_state[0])\n"
    )
    policy = _policy(src)
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "HOLD"
    assert result.reason == "translate error"


# ── Rule 5: BILL when translate returns bill(units=n) ────────────────────────


def test_rule5_bill_returns_correct_units() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=3)\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "BILL"
    assert result.units == 3


def test_rule5_bill_uses_eval_label_in_translate() -> None:
    # translate checks the label; only bills if label == "yes"
    src = (
        "def translate(evals):\n"
        "    if evals['quality'].label == 'yes':\n"
        "        return bill(units=1)\n"
        "    return no_charge(reason='low quality')\n"
    )
    policy = _policy(src)
    unit = _unit(policy)

    evals_yes = {"quality": _eval("quality", label="yes")}
    result_yes = decide(policy, unit, evals=evals_yes, watch={}, now=NOW)
    assert result_yes is not None and result_yes.outcome == "BILL"

    evals_no = {"quality": _eval("quality", label="no")}
    result_no = decide(policy, unit, evals=evals_no, watch={}, now=NOW)
    assert result_no is not None and result_no.outcome == "NO_CHARGE"


# ── Rule 6: NO_CHARGE when translate returns no_charge(reason) ───────────────


def test_rule6_no_charge_from_translate() -> None:
    policy = _policy(
        "def translate(evals):\n    return no_charge(reason='free tier')\n"
    )
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "NO_CHARGE"
    assert result.reason == "free tier"


# ── Rule 7: HOLD when translate returns hold(reason) ─────────────────────────


def test_rule7_hold_from_translate() -> None:
    policy = _policy(
        "def translate(evals):\n    return hold(reason='needs manual review')\n"
    )
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.outcome == "HOLD"
    assert result.reason == "needs manual review"


# ── Review flag: set when a watch eval has split agreement ───────────────────


def test_review_flag_set_for_split_watch_eval() -> None:
    policy = _policy(
        "def translate(evals):\n    return bill(units=1)\n",
        watch=["sentiment"],
    )
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    watch = {"sentiment": _eval("sentiment", agreement="split")}
    result = decide(policy, unit, evals=evals, watch=watch, now=NOW)
    assert result is not None
    assert result.review is True
    assert result.outcome == "BILL"  # watch split does not change outcome


def test_review_flag_false_when_watch_unanimous() -> None:
    policy = _policy(
        "def translate(evals):\n    return bill(units=1)\n",
        watch=["sentiment"],
    )
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    watch = {"sentiment": _eval("sentiment", agreement="unanimous")}
    result = decide(policy, unit, evals=evals, watch=watch, now=NOW)
    assert result is not None
    assert result.review is False


# ── Determinism: identical inputs always produce equal Decision objects ───────


def test_identical_inputs_produce_equal_decisions() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=2)\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    r1 = decide(policy, unit, evals=evals, watch={}, now=NOW)
    r2 = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert r1 == r2


# ── Eval snapshot: decision records which evals were used ────────────────────


def test_decision_contains_eval_snapshot() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=1)\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality", label="yes")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert "quality" in result.eval_snapshot
    snap = result.eval_snapshot["quality"]
    assert snap["label"] == "yes"


# ── decided_at is set to the `now` argument passed in ────────────────────────


def test_decided_at_matches_now_argument() -> None:
    policy = _policy("def translate(evals):\n    return bill(units=1)\n")
    unit = _unit(policy)
    evals = {"quality": _eval("quality")}
    result = decide(policy, unit, evals=evals, watch={}, now=NOW)
    assert result is not None
    assert result.decided_at == NOW
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/policy/test_engine.py -v
```

Expected: all fail with `ModuleNotFoundError: No module named 'tally.policy.engine'`

- [ ] **Step 3: Implement `tally/policy/engine.py`**

```python
from __future__ import annotations

import builtins as _builtins_module
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from tally.feed.source import EvalResult
from tally.policy.declaration import BillResult, HoldResult, NoChargeResult, Policy

logger = logging.getLogger(__name__)

_SANDBOX_BUILTINS: dict[str, Any] = {
    name: getattr(_builtins_module, name)
    for name in ("min", "max", "sum", "len", "any", "all", "abs", "round", "sorted")
}


@dataclass
class Unit:
    trace_id: str
    span_id: str
    customer_id: str
    policy_name: str
    policy_version: str
    first_seen: datetime
    deadline: datetime
    status: Literal["pending", "decided"] = "pending"


@dataclass(frozen=True)
class Decision:
    outcome: Literal["BILL", "NO_CHARGE", "HOLD", "ESCALATE"]
    units: int = 0
    reason: str = ""
    review: bool = False
    decided_at: datetime = field(default_factory=datetime.utcnow)
    eval_snapshot: dict[str, Any] = field(default_factory=dict)


def _run_translate(
    source: str,
    evals: dict[str, EvalResult],
) -> BillResult | NoChargeResult | HoldResult:
    """
    Compile and exec the translate source in a restricted namespace,
    then call the resulting function twice to verify determinism.
    Raises on any error (exception, invalid return type, or nondeterminism).
    """
    ns: dict[str, Any] = {
        "__builtins__": _SANDBOX_BUILTINS,
        "bill": BillResult,
        "no_charge": NoChargeResult,
        "hold": HoldResult,
    }
    exec(compile(source, "<translate>", "exec"), ns)  # noqa: S102
    fn = ns["translate"]
    r1 = fn(evals)
    r2 = fn(evals)
    if r1 != r2:
        raise ValueError("translate returned different results on two runs")
    if not isinstance(r1, (BillResult, NoChargeResult, HoldResult)):
        raise TypeError(f"translate returned {type(r1).__name__!r}")
    return r1  # type: ignore[return-value]


def decide(
    policy: Policy,
    unit: Unit,
    evals: dict[str, EvalResult],
    watch: dict[str, EvalResult],
    now: datetime,
) -> Decision | None:
    """
    Pure decision function. Returns None to keep the unit pending,
    or a Decision with one of: BILL, NO_CHARGE, HOLD, ESCALATE.
    """
    eval_snapshot: dict[str, Any] = {
        name: {"label": e.label, "score": e.score, "agreement": e.agreement}
        for name, e in {**evals, **watch}.items()
    }
    review = any(e.agreement == "split" for e in watch.values())

    # Rule 1 & 2: missing declared evals
    missing = [name for name in policy.evals if name not in evals]
    if missing:
        if now < unit.deadline:
            return None  # Rule 1: stay pending
        return Decision(  # Rule 2
            outcome="NO_CHARGE",
            reason="unscored",
            review=review,
            decided_at=now,
            eval_snapshot=eval_snapshot,
        )

    # Rule 3: split agreement on any declared eval → ESCALATE
    if any(e.agreement == "split" for e in evals.values()):
        return Decision(
            outcome="ESCALATE",
            review=review,
            decided_at=now,
            eval_snapshot=eval_snapshot,
        )

    # Rules 4–7: run translate (catches raises, invalid types, nondeterminism)
    try:
        result = _run_translate(policy.translate, evals)
    except Exception:
        logger.exception(
            "translate error trace_id=%s span_id=%s policy=%s",
            unit.trace_id,
            unit.span_id,
            policy.name,
        )
        return Decision(
            outcome="HOLD",
            reason="translate error",
            review=review,
            decided_at=now,
            eval_snapshot=eval_snapshot,
        )

    if isinstance(result, BillResult):  # Rule 5
        return Decision(
            outcome="BILL",
            units=result.units,
            review=review,
            decided_at=now,
            eval_snapshot=eval_snapshot,
        )
    if isinstance(result, NoChargeResult):  # Rule 6
        return Decision(
            outcome="NO_CHARGE",
            reason=result.reason,
            review=review,
            decided_at=now,
            eval_snapshot=eval_snapshot,
        )
    # Rule 7: HoldResult (isinstance check is exhaustive given _run_translate's type check)
    assert isinstance(result, HoldResult)
    return Decision(
        outcome="HOLD",
        reason=result.reason,
        review=review,
        decided_at=now,
        eval_snapshot=eval_snapshot,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/policy/test_engine.py -v
```

Expected: all 17 tests pass.

- [ ] **Step 5: Wire up top-level re-exports in `tally/__init__.py`**

```python
from tally.policy.declaration import BillResult as _BillResult
from tally.policy.declaration import HoldResult as _HoldResult
from tally.policy.declaration import NoChargeResult as _NoChargeResult


def bill(units: int) -> _BillResult:
    return _BillResult(units=units)


def no_charge(reason: str) -> _NoChargeResult:
    return _NoChargeResult(reason=reason)


def hold(reason: str) -> _HoldResult:
    return _HoldResult(reason=reason)


__all__ = ["bill", "no_charge", "hold"]
```

Verify:
```bash
python -c "import tally; print(tally.bill(units=2), tally.no_charge(reason='x'), tally.hold(reason='y'))"
```

Expected output: `BillResult(units=2) NoChargeResult(reason='x') HoldResult(reason='y')`

- [ ] **Step 6: Run full test suite + linting + type checks**

```bash
pytest tests/ -v --tb=short
ruff check tally/ tests/
mypy tally/
```

Expected: all tests pass, zero lint errors, zero mypy errors.

- [ ] **Step 7: Commit**

```bash
git add tally/policy/engine.py tally/__init__.py tests/policy/test_engine.py
git commit -m "feat: add policy engine with all 7 decision rules and table-driven tests"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|-----------------|-----------|
| Policy dataclass (all fields) | Task 2 |
| Version hashing (SHA-256 of name/span/evals/watch/translate) | Task 2 |
| `tally.bill`, `tally.no_charge`, `tally.hold` importable | Task 3 Step 5 |
| Rule 1: missing eval, before deadline → None | Task 3 test_rule1 |
| Rule 2: missing eval, after deadline → NO_CHARGE "unscored" | Task 3 test_rule2 |
| Rule 3: split declared eval → ESCALATE | Task 3 test_rule3 |
| Rule 4: translate raises/invalid type/nondeterministic → HOLD | Task 3 test_rule4a/b/c |
| Rule 5: bill(units=n) → BILL | Task 3 test_rule5 |
| Rule 6: no_charge(reason) → NO_CHARGE | Task 3 test_rule6 |
| Rule 7: hold(reason) → HOLD | Task 3 test_rule7 |
| Review flag on watch split | Task 3 test_review_flag |
| Identical inputs → identical decisions | Task 3 test_determinism |
| Sandboxed exec (restricted builtins + bill/no_charge/hold only) | Task 3 engine impl |
| eval_snapshot stored in Decision | Task 3 test_decision_contains_eval_snapshot |
| decided_at set to `now` argument | Task 3 test_decided_at |
| `customer` can be str or callable | Task 2 test_make_policy_customer_callable |
| `watch` defaults to `[]` | Task 2 test_make_policy_basic_fields |
| `meter` defaults to `name` | Task 2 test_make_policy_meter_defaults_to_name |
| `wait` defaults to 7 days | Task 2 test_make_policy_basic_fields |

**Gaps:** None found. EvalSource ABC and AXEvalSource are intentionally Phase 2 scope.
