# Tally Phase 2 — Feed Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add EvalSource ABC, polling backoff, FileEvalSource (JSON fixtures), and AXEvalSource (Phoenix GraphQL) so the worker can discover spans and retrieve eval results.

**Architecture:** `tally/feed/source.py` gains the `EvalSource` ABC and `poll_interval()` pure function. `tally/feed/file.py` and `tally/feed/ax.py` provide the two concrete implementations. Unit in `tally/policy/engine.py` gains `last_polled` and `cycles_polled` to support backoff decisions in the worker. Tests run entirely against fixtures and mocked HTTP — no live AX account required.

**Tech Stack:** Python 3.12, `requests>=2.31` (AX HTTP), `pytest`, `unittest.mock`.

## Global Constraints

- Python `>=3.12`. All new files use `from __future__ import annotations`.
- `ruff check` and `mypy --strict` must pass with zero errors.
- No live network calls in tests — AX HTTP must be mocked with `unittest.mock.patch`.
- `EvalResult.raw` stores the raw source dict verbatim; parity tests compare only name/label/score/explanation/agreement/evaluator_version/scored_at.
- `poll_interval(n=0)` → 60 s, `poll_interval(n=1)` → 120 s, `poll_interval(n=100)` is capped at 1800 s (30 min).
- Fixture key format: `"{trace_id}:{span_id}"`.
- AX GraphQL endpoint: `{base_url}/graphql`; auth headers `x-api-key` and `x-space-id`.
- Run all tests with: `py -3.12 -m pytest tests/ -q`

---

## File Map

| File | Change | Responsibility |
|------|--------|---------------|
| `tally/feed/source.py` | Modify | Add `EvalSource` ABC, `_extract_customer`, `_parse_dt`, `poll_interval` |
| `tally/policy/engine.py` | Modify | Add `last_polled`, `cycles_polled` fields to `Unit` |
| `tally/feed/file.py` | Create | `FileEvalSource` — loads JSON fixture |
| `tally/feed/ax.py` | Create | `AXEvalSource` — Phoenix GraphQL via `requests` |
| `tests/feed/fixtures/feed_fixture.json` | Create | Shared test fixture |
| `tests/feed/test_feed.py` | Create | All feed tests: FileEvalSource, AXEvalSource, backoff |
| `pyproject.toml` | Modify | Add `requests>=2.31` to `dependencies` |

---

### Task 1: EvalSource ABC, poll_interval, Unit update

**Files:**
- Modify: `tally/feed/source.py`
- Modify: `tally/policy/engine.py`
- Test: `tests/feed/test_feed.py` (poll_interval tests only in this task)

**Interfaces:**
- Produces:
  - `EvalSource(ABC)` with abstract methods `list_new_spans(policy, cursor) -> list[SpanInfo]` and `get_evals(trace_id, span_id) -> list[EvalResult]`
  - `poll_interval(cycles_polled: int, base: timedelta = timedelta(seconds=60), cap: timedelta = timedelta(minutes=30)) -> timedelta`
  - `_extract_customer(customer: str | Callable[[dict[str, object]], str], attrs: dict[str, object]) -> str`
  - `_parse_dt(s: str) -> datetime` — parses ISO 8601 with or without trailing Z
  - `Unit.last_polled: datetime | None = None`
  - `Unit.cycles_polled: int = 0`

- [ ] **Step 1: Write failing tests for poll_interval**

Create `tests/feed/test_feed.py`:
```python
from __future__ import annotations

from datetime import timedelta

from tally.feed.source import poll_interval


def test_poll_interval_n0_is_60s() -> None:
    assert poll_interval(0) == timedelta(seconds=60)


def test_poll_interval_n1_is_120s() -> None:
    assert poll_interval(1) == timedelta(seconds=120)


def test_poll_interval_n2_is_240s() -> None:
    assert poll_interval(2) == timedelta(seconds=240)


def test_poll_interval_cap_at_30min() -> None:
    assert poll_interval(100) == timedelta(minutes=30)


def test_poll_interval_custom_base_and_cap() -> None:
    result = poll_interval(3, base=timedelta(seconds=10), cap=timedelta(seconds=50))
    assert result == timedelta(seconds=50)  # 10 * 2^3 = 80, capped at 50
```

- [ ] **Step 2: Run to verify they fail**

```bash
py -3.12 -m pytest tests/feed/test_feed.py -q
```
Expected: ImportError on `poll_interval`.

- [ ] **Step 3: Add EvalSource ABC, helpers, and poll_interval to `tally/feed/source.py`**

Append after the existing `EvalResult` class:

```python
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tally.policy.declaration import Policy


def _parse_dt(s: str) -> datetime:
    """Parse ISO 8601 datetime string; handles trailing Z."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _extract_customer(
    customer: str | Callable[[dict[str, object]], str],
    attrs: dict[str, object],
) -> str:
    """Extract customer_id from span attributes per the policy's customer field."""
    if callable(customer):
        return customer(attrs)
    return str(attrs[customer])


def poll_interval(
    cycles_polled: int,
    base: timedelta = timedelta(seconds=60),
    cap: timedelta = timedelta(minutes=30),
) -> timedelta:
    """Exponential backoff: min(base * 2^cycles_polled, cap)."""
    seconds = base.total_seconds() * (2**cycles_polled)
    return min(timedelta(seconds=seconds), cap)


class EvalSource(ABC):
    @abstractmethod
    def list_new_spans(self, policy: "Policy", cursor: datetime) -> list[SpanInfo]: ...

    @abstractmethod
    def get_evals(self, trace_id: str, span_id: str) -> list[EvalResult]: ...
```

**Note on the full file:** The existing file already has `from __future__ import annotations` and imports. Add only what's missing. The final `tally/feed/source.py` should look like:

```python
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tally.policy.declaration import Policy


@dataclass(frozen=True)
class SpanInfo:
    ...  # (unchanged)


@dataclass(frozen=True)
class EvalResult:
    ...  # (unchanged)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _extract_customer(
    customer: str | Callable[[dict[str, object]], str],
    attrs: dict[str, object],
) -> str:
    if callable(customer):
        return customer(attrs)
    return str(attrs[customer])


def poll_interval(
    cycles_polled: int,
    base: timedelta = timedelta(seconds=60),
    cap: timedelta = timedelta(minutes=30),
) -> timedelta:
    seconds = base.total_seconds() * (2**cycles_polled)
    return min(timedelta(seconds=seconds), cap)


class EvalSource(ABC):
    @abstractmethod
    def list_new_spans(self, policy: Policy, cursor: datetime) -> list[SpanInfo]: ...

    @abstractmethod
    def get_evals(self, trace_id: str, span_id: str) -> list[EvalResult]: ...
```

- [ ] **Step 4: Add `last_polled` and `cycles_polled` to Unit in `tally/policy/engine.py`**

In `engine.py`, find the `Unit` dataclass and add the two new fields with defaults:

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
    status: Literal["pending", "decided"] = "pending"
    last_polled: datetime | None = None   # ADD
    cycles_polled: int = 0                # ADD
```

- [ ] **Step 5: Run tests**

```bash
py -3.12 -m pytest tests/ -q
```
Expected: 37 passed (32 existing + 5 new poll_interval tests).

- [ ] **Step 6: Lint + types**

```bash
py -3.12 -m ruff check tally/ tests/
py -3.12 -m mypy tally/
```
Expected: zero errors.

- [ ] **Step 7: Commit**

```bash
git add tally/feed/source.py tally/policy/engine.py tests/feed/test_feed.py
git commit -m "feat: add EvalSource ABC, poll_interval, and Unit polling fields"
```

---

### Task 2: FileEvalSource

**Files:**
- Create: `tally/feed/file.py`
- Create: `tests/feed/fixtures/feed_fixture.json`
- Modify: `tests/feed/test_feed.py` (add FileEvalSource tests)

**Interfaces:**
- Consumes: `EvalSource`, `SpanInfo`, `EvalResult`, `_extract_customer`, `_parse_dt`, `_parse_eval_dict` from `tally.feed.source`; `Policy` from `tally.policy.declaration`
- Produces: `FileEvalSource(fixture_path: str | Path)` — implements `EvalSource`

- [ ] **Step 1: Create the fixture file**

`tests/feed/fixtures/feed_fixture.json`:
```json
{
  "spans": [
    {
      "trace_id": "trace-001",
      "span_id": "span-001",
      "attributes": {"customer_id": "cust-1", "pr_number": 42},
      "span_name": "post_review_comment",
      "ended_at": "2024-06-01T10:00:00Z"
    },
    {
      "trace_id": "trace-002",
      "span_id": "span-002",
      "attributes": {"customer_id": "cust-2"},
      "span_name": "post_review_comment",
      "ended_at": "2024-06-01T11:00:00Z"
    },
    {
      "trace_id": "trace-003",
      "span_id": "span-003",
      "attributes": {"customer_id": "cust-1"},
      "span_name": "other_span",
      "ended_at": "2024-06-01T12:00:00Z"
    }
  ],
  "evals": {
    "trace-001:span-001": [
      {
        "name": "fix_landed",
        "label": "yes",
        "score": 1.0,
        "explanation": "The fix was applied",
        "agreement": null,
        "evaluator_version": "gpt-4",
        "scored_at": "2024-06-01T11:00:00Z",
        "raw": {"source": "fixture"}
      }
    ],
    "trace-002:span-002": [
      {
        "name": "fix_landed",
        "label": "no",
        "score": 0.0,
        "explanation": "Fix not found",
        "agreement": null,
        "evaluator_version": null,
        "scored_at": "2024-06-01T12:00:00Z",
        "raw": {}
      }
    ]
  }
}
```

- [ ] **Step 2: Also add `_parse_eval_dict` to `tally/feed/source.py` (shared helper)**

Append to `tally/feed/source.py`:

```python
def _parse_eval_dict(e: dict[str, object]) -> EvalResult:
    """Parse a raw eval dict (from fixture or AX annotation) into EvalResult."""
    return EvalResult(
        name=str(e["name"]),
        label=e.get("label") if e.get("label") is not None else None,  # type: ignore[arg-type]
        score=float(e["score"]) if e.get("score") is not None else None,
        explanation=str(e.get("explanation") or ""),
        agreement=e.get("agreement") if e.get("agreement") is not None else None,  # type: ignore[arg-type]
        evaluator_version=str(e["evaluator_version"]) if e.get("evaluator_version") is not None else None,
        scored_at=_parse_dt(str(e["scored_at"])),
        raw={k: v for k, v in e.items()},
    )
```

Actually, use a simpler form:

```python
def _parse_eval_dict(e: dict[str, object]) -> EvalResult:
    label = e.get("label")
    score_raw = e.get("score")
    ev = e.get("evaluator_version")
    return EvalResult(
        name=str(e["name"]),
        label=str(label) if label is not None else None,
        score=float(score_raw) if score_raw is not None else None,  # type: ignore[arg-type]
        explanation=str(e.get("explanation") or ""),
        agreement=str(e["agreement"]) if e.get("agreement") is not None else None,
        evaluator_version=str(ev) if ev is not None else None,
        scored_at=_parse_dt(str(e["scored_at"])),
        raw=dict(e),
    )
```

- [ ] **Step 3: Write failing FileEvalSource tests in `tests/feed/test_feed.py`**

Append to `tests/feed/test_feed.py`:

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tally.feed.file import FileEvalSource
from tally.policy.declaration import Policy, compute_version

FIXTURE = Path(__file__).parent / "fixtures" / "feed_fixture.json"


def _policy(span: str = "post_review_comment", customer: str = "customer_id") -> Policy:
    from tally.policy.declaration import compute_version
    src = "def translate(evals):\n    return bill(units=1)\n"
    version = compute_version("p", span, ["fix_landed"], [], src)
    return Policy(
        name="p", span=span, customer=customer,
        evals=["fix_landed"], watch=[],
        translate=src, version=version, meter="p",
        wait=timedelta(days=7),
    )


CURSOR_BEFORE = datetime(2024, 6, 1, 9, 0, tzinfo=timezone.utc)
CURSOR_BETWEEN = datetime(2024, 6, 1, 10, 30, tzinfo=timezone.utc)
CURSOR_AFTER_ALL = datetime(2024, 6, 1, 23, 0, tzinfo=timezone.utc)


def test_file_list_spans_returns_matching_span_name() -> None:
    src = FileEvalSource(FIXTURE)
    spans = src.list_new_spans(_policy(), cursor=CURSOR_BEFORE)
    names = {s.span_name for s in spans}
    assert names == {"post_review_comment"}


def test_file_list_spans_cursor_filters_old_spans() -> None:
    src = FileEvalSource(FIXTURE)
    spans = src.list_new_spans(_policy(), cursor=CURSOR_BETWEEN)
    assert len(spans) == 1
    assert spans[0].trace_id == "trace-002"


def test_file_list_spans_cursor_after_all_returns_empty() -> None:
    src = FileEvalSource(FIXTURE)
    spans = src.list_new_spans(_policy(), cursor=CURSOR_AFTER_ALL)
    assert spans == []


def test_file_list_spans_extracts_customer_id() -> None:
    src = FileEvalSource(FIXTURE)
    spans = src.list_new_spans(_policy(customer="customer_id"), cursor=CURSOR_BEFORE)
    customer_ids = {s.customer_id for s in spans}
    assert customer_ids == {"cust-1", "cust-2"}


def test_file_list_spans_callable_customer() -> None:
    src = FileEvalSource(FIXTURE)
    policy = _policy(customer=lambda attrs: str(attrs["customer_id"]) + "-x")
    spans = src.list_new_spans(policy, cursor=CURSOR_BEFORE)
    assert all(s.customer_id.endswith("-x") for s in spans)


def test_file_get_evals_returns_correct_fields() -> None:
    src = FileEvalSource(FIXTURE)
    evals = src.get_evals("trace-001", "span-001")
    assert len(evals) == 1
    e = evals[0]
    assert e.name == "fix_landed"
    assert e.label == "yes"
    assert e.score == 1.0
    assert e.evaluator_version == "gpt-4"


def test_file_get_evals_unknown_span_returns_empty() -> None:
    src = FileEvalSource(FIXTURE)
    assert src.get_evals("no-such-trace", "no-such-span") == []
```

- [ ] **Step 4: Run to verify they fail**

```bash
py -3.12 -m pytest tests/feed/test_feed.py -q
```
Expected: ImportError on `tally.feed.file`.

- [ ] **Step 5: Implement `tally/feed/file.py`**

```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tally.feed.source import (
    EvalResult,
    EvalSource,
    SpanInfo,
    _extract_customer,
    _parse_dt,
    _parse_eval_dict,
)
from tally.policy.declaration import Policy


class FileEvalSource(EvalSource):
    """EvalSource backed by a JSON fixture file. Used for tests and demo."""

    def __init__(self, fixture_path: str | Path) -> None:
        with open(fixture_path) as f:
            data: dict = json.load(f)
        self._spans: list[dict] = data["spans"]
        self._evals: dict[str, list[dict]] = data["evals"]

    def list_new_spans(self, policy: Policy, cursor: datetime) -> list[SpanInfo]:
        results: list[SpanInfo] = []
        for s in self._spans:
            if s["span_name"] != policy.span:
                continue
            ended_at = _parse_dt(str(s["ended_at"]))
            if ended_at <= cursor:
                continue
            customer_id = _extract_customer(policy.customer, dict(s["attributes"]))
            results.append(
                SpanInfo(
                    trace_id=str(s["trace_id"]),
                    span_id=str(s["span_id"]),
                    customer_id=customer_id,
                    span_name=str(s["span_name"]),
                    ended_at=ended_at,
                )
            )
        return results

    def get_evals(self, trace_id: str, span_id: str) -> list[EvalResult]:
        key = f"{trace_id}:{span_id}"
        return [_parse_eval_dict(e) for e in self._evals.get(key, [])]
```

- [ ] **Step 6: Run tests**

```bash
py -3.12 -m pytest tests/ -q
```
Expected: 44+ passed.

- [ ] **Step 7: Lint + types, fix issues**

```bash
py -3.12 -m ruff check tally/ tests/ && py -3.12 -m mypy tally/
```

- [ ] **Step 8: Commit**

```bash
git add tally/feed/source.py tally/feed/file.py tests/feed/fixtures/feed_fixture.json tests/feed/test_feed.py
git commit -m "feat: add FileEvalSource with fixture loading and cursor filtering"
```

---

### Task 3: AXEvalSource

**Files:**
- Create: `tally/feed/ax.py`
- Modify: `pyproject.toml` (add `requests>=2.31`)
- Modify: `tests/feed/test_feed.py` (add AXEvalSource + parity tests)

**Interfaces:**
- Consumes: `EvalSource`, `SpanInfo`, `EvalResult`, `_extract_customer`, `_parse_dt`, `_parse_eval_dict` from `tally.feed.source`; `Policy` from `tally.policy.declaration`; `requests` (HTTP)
- Produces:
  - `AXEvalSource(api_key, space_id, project, base_url="https://app.arizeai.com")` — implements `EvalSource`
  - `AXEvalSource.from_env() -> AXEvalSource` — reads `ARIZE_API_KEY`, `ARIZE_SPACE_ID`, `ARIZE_PROJECT` env vars

- [ ] **Step 1: Add `requests>=2.31` to `pyproject.toml`**

In `pyproject.toml`, change:
```toml
dependencies = []
```
to:
```toml
dependencies = ["requests>=2.31"]
```

Then install: `py -3.12 -m pip install -e ".[dev]" -q`

- [ ] **Step 2: Write failing AXEvalSource tests**

Append to `tests/feed/test_feed.py`:

```python
import json
from unittest.mock import MagicMock, patch

from tally.feed.ax import AXEvalSource


def _ax_list_spans_response(spans: list[dict]) -> dict:
    """Build the GraphQL JSON response shape for list_new_spans."""
    return {
        "data": {
            "project": {
                "spans": {
                    "edges": [
                        {
                            "node": {
                                "context": {
                                    "traceId": s["trace_id"],
                                    "spanId": s["span_id"],
                                },
                                "name": s["span_name"],
                                "attributes": json.dumps(s["attributes"]),
                                "endTime": s["ended_at"],
                            }
                        }
                        for s in spans
                    ]
                }
            }
        }
    }


def _ax_get_evals_response(annotations: list[dict]) -> dict:
    """Build the GraphQL JSON response shape for get_evals."""
    return {
        "data": {
            "project": {
                "spans": {
                    "edges": [
                        {
                            "node": {
                                "spanAnnotations": annotations
                            }
                        }
                    ]
                }
            }
        }
    }


def _ax_empty_spans_response() -> dict:
    return {"data": {"project": {"spans": {"edges": []}}}}


def _mock_ax(return_value: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = return_value
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def test_ax_list_spans_returns_span_info() -> None:
    src = AXEvalSource("key", "space", "proj")
    raw_spans = [
        {
            "trace_id": "t1", "span_id": "s1",
            "span_name": "post_review_comment",
            "attributes": {"customer_id": "cust-1"},
            "ended_at": "2024-06-01T10:00:00Z",
        }
    ]
    with patch("requests.post", return_value=_mock_ax(_ax_list_spans_response(raw_spans))):
        spans = src.list_new_spans(_policy(), cursor=CURSOR_BEFORE)
    assert len(spans) == 1
    assert spans[0].trace_id == "t1"
    assert spans[0].customer_id == "cust-1"


def test_ax_get_evals_returns_eval_result() -> None:
    src = AXEvalSource("key", "space", "proj")
    annotations = [
        {
            "name": "fix_landed",
            "label": "yes",
            "score": 1.0,
            "explanation": "The fix was applied",
            "annotatorKind": "gpt-4",
            "createdAt": "2024-06-01T11:00:00Z",
        }
    ]
    with patch("requests.post", return_value=_mock_ax(_ax_get_evals_response(annotations))):
        evals = src.get_evals("t1", "s1")
    assert len(evals) == 1
    e = evals[0]
    assert e.name == "fix_landed"
    assert e.label == "yes"
    assert e.evaluator_version == "gpt-4"
    assert e.agreement is None  # single annotation → None


def test_ax_get_evals_unanimous_on_matching_labels() -> None:
    src = AXEvalSource("key", "space", "proj")
    annotations = [
        {"name": "q", "label": "yes", "score": 1.0, "explanation": "", "annotatorKind": "llm-1", "createdAt": "2024-01-01T00:00:00Z"},
        {"name": "q", "label": "yes", "score": 1.0, "explanation": "", "annotatorKind": "llm-2", "createdAt": "2024-01-01T00:00:00Z"},
    ]
    with patch("requests.post", return_value=_mock_ax(_ax_get_evals_response(annotations))):
        evals = src.get_evals("t1", "s1")
    assert evals[0].agreement == "unanimous"


def test_ax_get_evals_split_on_differing_labels() -> None:
    src = AXEvalSource("key", "space", "proj")
    annotations = [
        {"name": "q", "label": "yes", "score": 1.0, "explanation": "", "annotatorKind": "llm-1", "createdAt": "2024-01-01T00:00:00Z"},
        {"name": "q", "label": "no",  "score": 0.0, "explanation": "", "annotatorKind": "llm-2", "createdAt": "2024-01-01T00:00:00Z"},
    ]
    with patch("requests.post", return_value=_mock_ax(_ax_get_evals_response(annotations))):
        evals = src.get_evals("t1", "s1")
    assert evals[0].agreement == "split"


def test_ax_get_evals_empty_edges_returns_empty() -> None:
    src = AXEvalSource("key", "space", "proj")
    with patch("requests.post", return_value=_mock_ax(_ax_empty_spans_response())):
        evals = src.get_evals("t1", "s1")
    assert evals == []


def test_ax_get_evals_raises_on_http_error() -> None:
    import requests as req
    src = AXEvalSource("key", "space", "proj")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = req.HTTPError("500")
    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(req.HTTPError):
            src.get_evals("t1", "s1")


def test_ax_parity_with_file_for_single_annotation() -> None:
    """AXEvalSource and FileEvalSource produce identical EvalResult fields for the same data."""
    file_src = FileEvalSource(FIXTURE)
    file_evals = file_src.get_evals("trace-001", "span-001")

    # Build AX mock to return the same logical data (single annotation, no agreement)
    annotations = [
        {
            "name": "fix_landed",
            "label": "yes",
            "score": 1.0,
            "explanation": "The fix was applied",
            "annotatorKind": "gpt-4",
            "createdAt": "2024-06-01T11:00:00Z",
        }
    ]
    ax_src = AXEvalSource("key", "space", "proj")
    with patch("requests.post", return_value=_mock_ax(_ax_get_evals_response(annotations))):
        ax_evals = ax_src.get_evals("trace-001", "span-001")

    assert len(file_evals) == len(ax_evals) == 1
    f, a = file_evals[0], ax_evals[0]
    assert f.name == a.name
    assert f.label == a.label
    assert f.score == a.score
    assert f.explanation == a.explanation
    assert f.agreement == a.agreement  # both None — single annotation
    assert f.evaluator_version == a.evaluator_version
    assert f.scored_at == a.scored_at
```

- [ ] **Step 3: Run to verify they fail**

```bash
py -3.12 -m pytest tests/feed/test_feed.py -q 2>&1 | tail -5
```
Expected: ImportError on `tally.feed.ax`.

- [ ] **Step 4: Implement `tally/feed/ax.py`**

```python
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import requests

from tally.feed.source import (
    EvalResult,
    EvalSource,
    SpanInfo,
    _extract_customer,
    _parse_dt,
    _parse_eval_dict,
)
from tally.policy.declaration import Policy

_LIST_SPANS_QUERY = """
query ListSpans($projectName: String!, $filterCondition: String!, $first: Int!) {
    project(name: $projectName) {
        spans(first: $first, filterCondition: $filterCondition) {
            edges { node {
                context { traceId spanId }
                name attributes endTime
            }}
        }
    }
}
"""

_GET_EVALS_QUERY = """
query GetEvals($projectName: String!, $filterCondition: String!) {
    project(name: $projectName) {
        spans(first: 1, filterCondition: $filterCondition) {
            edges { node {
                spanAnnotations {
                    name label score explanation annotatorKind createdAt
                }
            }}
        }
    }
}
"""


def _parse_annotations(annotations: list[dict[str, Any]]) -> list[EvalResult]:
    """Group AX span annotations by name and compute agreement."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for ann in annotations:
        by_name.setdefault(str(ann["name"]), []).append(ann)

    results: list[EvalResult] = []
    for name, anns in by_name.items():
        labels = {a.get("label") for a in anns}
        if len(anns) == 1:
            agreement: str | None = None
        elif len(labels) == 1:
            agreement = "unanimous"
        else:
            agreement = "split"
        ann = anns[0]
        results.append(
            _parse_eval_dict(
                {
                    "name": name,
                    "label": ann.get("label"),
                    "score": ann.get("score"),
                    "explanation": ann.get("explanation") or "",
                    "agreement": agreement,
                    "evaluator_version": ann.get("annotatorKind"),
                    "scored_at": ann["createdAt"],
                    "raw": dict(ann),
                }
            )
        )
    return results


class AXEvalSource(EvalSource):
    """EvalSource that reads from Arize AX (Phoenix) via GraphQL."""

    def __init__(
        self,
        api_key: str,
        space_id: str,
        project: str,
        base_url: str = "https://app.arizeai.com",
    ) -> None:
        self._api_key = api_key
        self._space_id = space_id
        self._project = project
        self._gql_url = f"{base_url.rstrip('/')}/graphql"

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(
            self._gql_url,
            headers={
                "x-api-key": self._api_key,
                "x-space-id": self._space_id,
                "content-type": "application/json",
            },
            json={"query": query, "variables": variables},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def list_new_spans(self, policy: Policy, cursor: datetime) -> list[SpanInfo]:
        filter_cond = f"name == '{policy.span}' and end_time > '{cursor.isoformat()}'"
        data = self._graphql(
            _LIST_SPANS_QUERY,
            {"projectName": self._project, "filterCondition": filter_cond, "first": 100},
        )
        results: list[SpanInfo] = []
        for edge in data["data"]["project"]["spans"]["edges"]:
            node = edge["node"]
            attrs: dict[str, object] = (
                json.loads(node["attributes"])
                if isinstance(node["attributes"], str)
                else node["attributes"]
            )
            results.append(
                SpanInfo(
                    trace_id=str(node["context"]["traceId"]),
                    span_id=str(node["context"]["spanId"]),
                    customer_id=_extract_customer(policy.customer, attrs),
                    span_name=str(node["name"]),
                    ended_at=_parse_dt(str(node["endTime"])),
                )
            )
        return results

    def get_evals(self, trace_id: str, span_id: str) -> list[EvalResult]:
        filter_cond = (
            f"context.trace_id == '{trace_id}' and context.span_id == '{span_id}'"
        )
        data = self._graphql(
            _GET_EVALS_QUERY,
            {"projectName": self._project, "filterCondition": filter_cond},
        )
        edges = data["data"]["project"]["spans"]["edges"]
        if not edges:
            return []
        return _parse_annotations(edges[0]["node"]["spanAnnotations"])

    @classmethod
    def from_env(cls) -> AXEvalSource:
        return cls(
            api_key=os.environ["ARIZE_API_KEY"],
            space_id=os.environ["ARIZE_SPACE_ID"],
            project=os.environ["ARIZE_PROJECT"],
        )
```

- [ ] **Step 5: Run all tests**

```bash
py -3.12 -m pytest tests/ -q
```
Expected: all pass (37 + FileEvalSource tests + AXEvalSource tests).

- [ ] **Step 6: Lint + types**

```bash
py -3.12 -m ruff check tally/ tests/ && py -3.12 -m mypy tally/
```

- [ ] **Step 7: Commit**

```bash
git add tally/feed/ax.py pyproject.toml tests/feed/test_feed.py
git commit -m "feat: add AXEvalSource with GraphQL and mocked HTTP tests"
```
