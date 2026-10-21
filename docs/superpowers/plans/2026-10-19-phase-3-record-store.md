# Tally Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the RecordStore layer (Record dataclass + content hashing + ABC + InMemoryRecordStore + AXRecordStore) plus `tally.explain()` and `tally.verify()`.

**Architecture:** Records are frozen dataclasses with a content hash (SHA-256 of all fields except `content_hash`). RecordStore is an ABC with InMemory (tests) and AX (OTLP write + GraphQL read) implementations. `explain()` assembles a span's full audit trail from records. `verify()` detects tampering by recomputing hashes and replaying translate.

**Tech Stack:** Python 3.12, hashlib, json, dataclasses, requests (for AXRecordStore)

## Global Constraints

- Python 3.12, `from __future__ import annotations` in every file
- No new runtime dependencies (requests is already declared)
- All public types in `tally/log/` are frozen dataclasses
- Content hash: `SHA-256(json.dumps(all_fields_except_content_hash, sort_keys=True, ensure_ascii=True))`
- Record types: `Literal["POLICY", "MODE", "DECISION", "EMISSION", "RULING"]`
- Non-span records (POLICY, MODE) use `trace_id=""`, `span_id=""`, `customer_id=""` as sentinels
- `ruff check` and `mypy --strict` must pass after each task
- Tests run with: `py -3.12 -m pytest tests/ -q`

---

### Task 1: Record dataclass + content hashing

**Files:**
- Create: `tally/log/__init__.py`
- Create: `tally/log/records.py`
- Create: `tests/log/__init__.py`
- Create: `tests/log/test_records.py`

**Interfaces:**
- Produces: `Record`, `compute_content_hash`, `make_record` (used by Tasks 2, 3, 4)

- [ ] **Step 1: Write the failing tests**

```python
# tests/log/test_records.py
from __future__ import annotations

from tally.log.records import Record, compute_content_hash, make_record


def _base_kwargs() -> dict[str, object]:
    return {
        "type": "DECISION",
        "trace_id": "t1",
        "span_id": "s1",
        "customer_id": "cust-1",
        "policy_name": "p",
        "policy_version": "abc123",
        "policy_hash": "def456",
        "payload": {"decision": "BILL", "units": 1},
    }


def test_make_record_sets_content_hash() -> None:
    r = make_record(**_base_kwargs())  # type: ignore[arg-type]
    assert len(r.content_hash) == 64  # SHA-256 hex


def test_content_hash_is_deterministic() -> None:
    r1 = make_record(**_base_kwargs())  # type: ignore[arg-type]
    r2 = make_record(**_base_kwargs())  # type: ignore[arg-type]
    assert r1.content_hash == r2.content_hash


def test_content_hash_changes_on_payload_change() -> None:
    r1 = make_record(**_base_kwargs())  # type: ignore[arg-type]
    kwargs = _base_kwargs()
    kwargs["payload"] = {"decision": "NO_CHARGE", "units": 0}
    r2 = make_record(**kwargs)  # type: ignore[arg-type]
    assert r1.content_hash != r2.content_hash


def test_tamper_detection() -> None:
    """Recomputing hash on a tampered record reveals the mismatch."""
    r = make_record(**_base_kwargs())  # type: ignore[arg-type]
    tampered_payload = {**r.payload, "units": 999}
    expected_hash = compute_content_hash(
        type=r.type, trace_id=r.trace_id, span_id=r.span_id,
        customer_id=r.customer_id, policy_name=r.policy_name,
        policy_version=r.policy_version, policy_hash=r.policy_hash,
        payload=tampered_payload,
    )
    assert expected_hash != r.content_hash


def test_sentinel_fields_for_non_span_records() -> None:
    """POLICY and MODE records use empty strings for span identity."""
    r = make_record(
        type="POLICY", trace_id="", span_id="", customer_id="",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"translate": "def translate(evals): ..."},
    )
    assert r.trace_id == ""
    assert r.span_id == ""
    assert r.customer_id == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```
py -3.12 -m pytest tests/log/test_records.py -q
```
Expected: ImportError (module doesn't exist yet)

- [ ] **Step 3: Create empty package files**

```python
# tally/log/__init__.py
```

```python
# tests/log/__init__.py
```

- [ ] **Step 4: Implement `tally/log/records.py`**

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal


RecordType = Literal["POLICY", "MODE", "DECISION", "EMISSION", "RULING"]


@dataclass(frozen=True)
class Record:
    type: RecordType
    trace_id: str
    span_id: str
    customer_id: str
    policy_name: str
    policy_version: str
    policy_hash: str
    payload: dict[str, object]
    content_hash: str  # SHA-256(json.dumps(all fields except content_hash, sort_keys=True))


def compute_content_hash(
    *,
    type: RecordType,
    trace_id: str,
    span_id: str,
    customer_id: str,
    policy_name: str,
    policy_version: str,
    policy_hash: str,
    payload: dict[str, object],
) -> str:
    data: dict[str, object] = {
        "type": type,
        "trace_id": trace_id,
        "span_id": span_id,
        "customer_id": customer_id,
        "policy_name": policy_name,
        "policy_version": policy_version,
        "policy_hash": policy_hash,
        "payload": payload,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def make_record(
    *,
    type: RecordType,
    trace_id: str,
    span_id: str,
    customer_id: str,
    policy_name: str,
    policy_version: str,
    policy_hash: str,
    payload: dict[str, object],
) -> Record:
    content_hash = compute_content_hash(
        type=type,
        trace_id=trace_id,
        span_id=span_id,
        customer_id=customer_id,
        policy_name=policy_name,
        policy_version=policy_version,
        policy_hash=policy_hash,
        payload=payload,
    )
    return Record(
        type=type,
        trace_id=trace_id,
        span_id=span_id,
        customer_id=customer_id,
        policy_name=policy_name,
        policy_version=policy_version,
        policy_hash=policy_hash,
        payload=payload,
        content_hash=content_hash,
    )
```

- [ ] **Step 5: Run tests**

```
py -3.12 -m pytest tests/log/test_records.py -q
```
Expected: 5 passed

- [ ] **Step 6: Lint + type-check**

```
py -3.12 -m ruff check . && py -3.12 -m mypy tally/
```
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add tally/log/__init__.py tally/log/records.py tests/log/__init__.py tests/log/test_records.py
git commit -m "feat: add Record dataclass with SHA-256 content hashing"
```

---

### Task 2: RecordStore ABC + InMemoryRecordStore

**Files:**
- Create: `tally/log/store.py`
- Create: `tally/log/memory.py`
- Create: `tests/log/test_store.py`

**Interfaces:**
- Consumes: `Record`, `make_record` from `tally.log.records`
- Produces: `RecordStore`, `InMemoryRecordStore` (used by Tasks 3, 4 and test fixtures throughout)

- [ ] **Step 1: Write failing tests**

```python
# tests/log/test_store.py
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tally.log.memory import InMemoryRecordStore
from tally.log.records import make_record


def _decision_record(
    trace_id: str = "t1",
    span_id: str = "s1",
    customer_id: str = "cust-1",
    policy_name: str = "p",
    decided_at: str = "2024-06-01T12:00:00Z",
) -> object:
    return make_record(
        type="DECISION",
        trace_id=trace_id,
        span_id=span_id,
        customer_id=customer_id,
        policy_name=policy_name,
        policy_version="v1",
        policy_hash="h1",
        payload={"decision": "BILL", "units": 1, "decided_at": decided_at},
    )


def test_write_and_list_all() -> None:
    store = InMemoryRecordStore()
    r = _decision_record()
    store.write(r)  # type: ignore[arg-type]
    assert store.list() == [r]


def test_list_by_trace_and_span() -> None:
    store = InMemoryRecordStore()
    r1 = _decision_record(trace_id="t1", span_id="s1")
    r2 = _decision_record(trace_id="t2", span_id="s2")
    store.write(r1)  # type: ignore[arg-type]
    store.write(r2)  # type: ignore[arg-type]
    assert store.list(trace_id="t1", span_id="s1") == [r1]


def test_list_by_type() -> None:
    store = InMemoryRecordStore()
    r1 = _decision_record()
    r2 = make_record(
        type="EMISSION",
        trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"stripe_identifier": "t1::s1", "shadow": False},
    )
    store.write(r1)  # type: ignore[arg-type]
    store.write(r2)
    assert store.list(type="DECISION") == [r1]
    assert store.list(type="EMISSION") == [r2]


def test_latest_cursor_returns_epoch_when_empty() -> None:
    store = InMemoryRecordStore()
    cursor = store.latest_cursor("p")
    assert cursor == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_latest_cursor_returns_max_decided_at() -> None:
    store = InMemoryRecordStore()
    store.write(_decision_record(decided_at="2024-06-01T10:00:00Z"))  # type: ignore[arg-type]
    store.write(_decision_record(decided_at="2024-06-01T12:00:00Z", span_id="s2"))  # type: ignore[arg-type]
    cursor = store.latest_cursor("p")
    assert cursor == datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_latest_cursor_ignores_other_policies() -> None:
    store = InMemoryRecordStore()
    store.write(_decision_record(policy_name="p", decided_at="2024-06-01T12:00:00Z"))  # type: ignore[arg-type]
    store.write(_decision_record(policy_name="q", decided_at="2024-06-02T12:00:00Z", span_id="s2"))  # type: ignore[arg-type]
    assert store.latest_cursor("p") == datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_write_is_idempotent_for_same_content_hash() -> None:
    """Writing the same record twice does not create a duplicate."""
    store = InMemoryRecordStore()
    r = _decision_record()
    store.write(r)  # type: ignore[arg-type]
    store.write(r)  # type: ignore[arg-type]
    assert len(store.list()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```
py -3.12 -m pytest tests/log/test_store.py -q
```
Expected: ImportError

- [ ] **Step 3: Implement `tally/log/store.py`**

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from tally.log.records import Record, RecordType


class RecordStore(ABC):
    @abstractmethod
    def write(self, record: Record) -> None: ...

    @abstractmethod
    def list(
        self,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
        type: RecordType | None = None,
    ) -> list[Record]: ...

    @abstractmethod
    def latest_cursor(self, policy_name: str) -> datetime: ...
```

- [ ] **Step 4: Implement `tally/log/memory.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone

from tally.log.records import Record, RecordType
from tally.log.store import RecordStore

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class InMemoryRecordStore(RecordStore):
    def __init__(self) -> None:
        self._records: list[Record] = []
        self._seen: set[str] = set()

    def write(self, record: Record) -> None:
        if record.content_hash in self._seen:
            return
        self._seen.add(record.content_hash)
        self._records.append(record)

    def list(
        self,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
        type: RecordType | None = None,
    ) -> list[Record]:
        result = self._records
        if trace_id is not None:
            result = [r for r in result if r.trace_id == trace_id]
        if span_id is not None:
            result = [r for r in result if r.span_id == span_id]
        if type is not None:
            result = [r for r in result if r.type == type]
        return result

    def latest_cursor(self, policy_name: str) -> datetime:
        decisions = [
            r for r in self._records
            if r.type == "DECISION" and r.policy_name == policy_name
        ]
        if not decisions:
            return _EPOCH
        timestamps = [
            _parse_dt(str(r.payload["decided_at"]))
            for r in decisions
            if "decided_at" in r.payload
        ]
        return max(timestamps) if timestamps else _EPOCH
```

- [ ] **Step 5: Run tests**

```
py -3.12 -m pytest tests/log/test_store.py -q
```
Expected: 7 passed

- [ ] **Step 6: Run full suite + lint**

```
py -3.12 -m pytest tests/ -q && py -3.12 -m ruff check . && py -3.12 -m mypy tally/
```
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add tally/log/store.py tally/log/memory.py tests/log/test_store.py
git commit -m "feat: add RecordStore ABC and InMemoryRecordStore"
```

---

### Task 3: AXRecordStore

**Files:**
- Create: `tally/log/ax.py`
- Create: `tests/log/test_ax_store.py`

**Interfaces:**
- Consumes: `Record`, `make_record`, `RecordStore` from tally.log
- Produces: `AXRecordStore` (used by Phase 5 worker)

Notes on OTLP write: POST JSON-encoded OpenTelemetry spans to `{base_url}/v1/traces`.
Each Record becomes one span: `name = record.type`, `trace_id` from record (zero-padded to 32 hex chars if non-empty, else random), `span_id` similarly (16 hex chars), attributes encode Record fields as JSON string.

Notes on GraphQL read: same pattern as AXEvalSource — query `project(name:…).spans` filtered by attribute JSON.

- [ ] **Step 1: Write failing tests**

```python
# tests/log/test_ax_store.py
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from tally.log.ax import AXRecordStore
from tally.log.records import Record, make_record


def _sample_record() -> Record:
    return make_record(
        type="DECISION",
        trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"decision": "BILL", "units": 1, "decided_at": "2024-06-01T12:00:00Z"},
    )


def _mock_ok() -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {}
    return m


def _list_response(records: list[Record]) -> dict[str, object]:
    """GraphQL response shape for listing records."""
    return {
        "data": {
            "project": {
                "spans": {
                    "edges": [
                        {
                            "node": {
                                "name": r.type,
                                "attributes": json.dumps({
                                    "tally.record": json.dumps({
                                        "type": r.type,
                                        "trace_id": r.trace_id,
                                        "span_id": r.span_id,
                                        "customer_id": r.customer_id,
                                        "policy_name": r.policy_name,
                                        "policy_version": r.policy_version,
                                        "policy_hash": r.policy_hash,
                                        "payload": r.payload,
                                        "content_hash": r.content_hash,
                                    })
                                }),
                            }
                        }
                        for r in records
                    ]
                }
            }
        }
    }


def test_write_posts_to_otlp() -> None:
    store = AXRecordStore("key", "space", "tally-proj")
    with patch("requests.post", return_value=_mock_ok()) as mock_post:
        store.write(_sample_record())
    assert mock_post.called
    call_kwargs = mock_post.call_args
    assert "otlp" in str(call_kwargs).lower() or "/v1/traces" in str(call_kwargs)


def test_write_raises_on_http_error() -> None:
    store = AXRecordStore("key", "space", "tally-proj")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("500")
    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            store.write(_sample_record())


def test_list_deserializes_records() -> None:
    store = AXRecordStore("key", "space", "tally-proj")
    r = _sample_record()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = _list_response([r])
    with patch("requests.post", return_value=mock_resp):
        records = store.list(type="DECISION")
    assert len(records) == 1
    assert records[0].type == "DECISION"
    assert records[0].trace_id == "t1"
    assert records[0].content_hash == r.content_hash


def test_list_empty_returns_empty() -> None:
    store = AXRecordStore("key", "space", "tally-proj")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = _list_response([])
    with patch("requests.post", return_value=mock_resp):
        records = store.list(type="DECISION")
    assert records == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
py -3.12 -m pytest tests/log/test_ax_store.py -q
```
Expected: ImportError

- [ ] **Step 3: Implement `tally/log/ax.py`**

```python
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

import requests

from tally.log.records import Record, RecordType, make_record
from tally.log.store import RecordStore

_LIST_RECORDS_QUERY = """
query ListRecords($projectName: String!, $filterCondition: String!, $first: Int!) {
    project(name: $projectName) {
        spans(first: $first, filterCondition: $filterCondition) {
            edges { node { name attributes } }
        }
    }
}
"""

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _pad_trace_id(s: str) -> str:
    """Zero-pad or truncate to 32 hex chars for OTLP."""
    if not s:
        return secrets.token_hex(16)
    return s.replace("-", "").ljust(32, "0")[:32]


def _pad_span_id(s: str) -> str:
    """Zero-pad or truncate to 16 hex chars for OTLP."""
    if not s:
        return secrets.token_hex(8)
    return s.replace("-", "").ljust(16, "0")[:16]


class AXRecordStore(RecordStore):
    """RecordStore that writes via OTLP and reads via AX GraphQL."""

    def __init__(
        self,
        api_key: str,
        space_id: str,
        project: str,
        base_url: str = "https://otlp.arizeai.com",
        gql_url: str = "https://app.arizeai.com/graphql",
    ) -> None:
        self._project = project
        self._otlp_url = f"{base_url.rstrip('/')}/v1/traces"
        self._gql_url = gql_url
        self._headers = {
            "x-api-key": api_key,
            "x-space-id": space_id,
            "content-type": "application/json",
        }

    def write(self, record: Record) -> None:
        payload = {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "scope": {"name": "tally"},
                            "spans": [
                                {
                                    "traceId": _pad_trace_id(record.trace_id),
                                    "spanId": _pad_span_id(record.span_id),
                                    "name": record.type,
                                    "kind": 1,
                                    "startTimeUnixNano": 0,
                                    "endTimeUnixNano": 0,
                                    "attributes": [
                                        {
                                            "key": "tally.record",
                                            "value": {
                                                "stringValue": json.dumps({
                                                    "type": record.type,
                                                    "trace_id": record.trace_id,
                                                    "span_id": record.span_id,
                                                    "customer_id": record.customer_id,
                                                    "policy_name": record.policy_name,
                                                    "policy_version": record.policy_version,
                                                    "policy_hash": record.policy_hash,
                                                    "payload": record.payload,
                                                    "content_hash": record.content_hash,
                                                })
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        resp = requests.post(self._otlp_url, headers=self._headers, json=payload, timeout=30)
        resp.raise_for_status()

    def list(
        self,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
        type: RecordType | None = None,
    ) -> list[Record]:
        parts = [f"name == '{type}'" if type else "name != 'NONE'"]
        if trace_id:
            parts.append(f"attributes['tally.trace_id'] == '{trace_id}'")
        if span_id:
            parts.append(f"attributes['tally.span_id'] == '{span_id}'")
        filter_cond = " and ".join(parts)
        resp = requests.post(
            self._gql_url,
            headers=self._headers,
            json={
                "query": _LIST_RECORDS_QUERY,
                "variables": {
                    "projectName": self._project,
                    "filterCondition": filter_cond,
                    "first": 1000,
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        records: list[Record] = []
        for edge in data["data"]["project"]["spans"]["edges"]:
            node = edge["node"]
            attrs: dict[str, object] = (
                json.loads(node["attributes"])
                if isinstance(node["attributes"], str)
                else node["attributes"]
            )
            raw_record_str = str(attrs.get("tally.record", "{}"))
            raw: dict[str, Any] = json.loads(raw_record_str)
            r = make_record(
                type=raw["type"],
                trace_id=raw["trace_id"],
                span_id=raw["span_id"],
                customer_id=raw["customer_id"],
                policy_name=raw["policy_name"],
                policy_version=raw["policy_version"],
                policy_hash=raw["policy_hash"],
                payload=raw["payload"],
            )
            # Verify stored hash matches recomputed hash
            if r.content_hash == raw.get("content_hash"):
                records.append(r)
        return records

    def latest_cursor(self, policy_name: str) -> datetime:
        records = self.list(type="DECISION")
        timestamps = [
            _parse_dt(str(r.payload["decided_at"]))
            for r in records
            if r.policy_name == policy_name and "decided_at" in r.payload
        ]
        return max(timestamps) if timestamps else _EPOCH

    @classmethod
    def from_env(cls) -> AXRecordStore:
        arize_project = os.environ["ARIZE_PROJECT"]
        return cls(
            api_key=os.environ["ARIZE_API_KEY"],
            space_id=os.environ["ARIZE_SPACE_ID"],
            project=os.environ.get("TALLY_PROJECT", f"{arize_project}-tally"),
        )
```

- [ ] **Step 4: Run tests**

```
py -3.12 -m pytest tests/log/test_ax_store.py -q
```
Expected: 4 passed

- [ ] **Step 5: Run full suite + lint**

```
py -3.12 -m pytest tests/ -q && py -3.12 -m ruff check . && py -3.12 -m mypy tally/
```
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add tally/log/ax.py tests/log/test_ax_store.py
git commit -m "feat: add AXRecordStore (OTLP write + GraphQL read)"
```

---

### Task 4: explain() and verify()

**Files:**
- Create: `tally/sdk/__init__.py`
- Create: `tally/sdk/explain.py`
- Create: `tally/sdk/verify.py`
- Modify: `tally/__init__.py`
- Create: `tests/log/test_explain.py`
- Create: `tests/log/test_verify.py`

**Interfaces:**
- Consumes: `Record`, `make_record`, `compute_content_hash`, `RecordStore`, `InMemoryRecordStore`; `_run_translate` from `tally.policy.engine`
- Produces: `explain()`, `verify()`, `VerificationFailure` (re-exported from `tally`)

- [ ] **Step 1: Write failing tests**

```python
# tests/log/test_explain.py
from __future__ import annotations

from tally.log.memory import InMemoryRecordStore
from tally.log.records import make_record
from tally.sdk.explain import explain


def _store_with_decision() -> InMemoryRecordStore:
    store = InMemoryRecordStore()
    store.write(make_record(
        type="POLICY", trace_id="", span_id="", customer_id="",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"translate": "def translate(evals): return bill(units=1)"},
    ))
    store.write(make_record(
        type="DECISION", trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={
            "decision": "BILL", "units": 1, "reason": "",
            "decided_at": "2024-06-01T12:00:00Z",
            "eval_snapshot": {"fix_landed": {"label": "yes", "score": 1.0}},
        },
    ))
    return store


def test_explain_returns_policy_and_decision() -> None:
    store = _store_with_decision()
    result = explain("t1", "s1", store=store)
    assert result["decision"]["decision"] == "BILL"
    assert result["policy"]["translate"] == "def translate(evals): return bill(units=1)"


def test_explain_includes_emission_when_present() -> None:
    store = _store_with_decision()
    store.write(make_record(
        type="EMISSION", trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"stripe_identifier": "t1::s1", "shadow": False, "status": "sent"},
    ))
    result = explain("t1", "s1", store=store)
    assert result["emission"]["stripe_identifier"] == "t1::s1"


def test_explain_no_emission_key_absent_when_not_emitted() -> None:
    store = _store_with_decision()
    result = explain("t1", "s1", store=store)
    assert "emission" not in result


def test_explain_includes_ruling_when_present() -> None:
    store = _store_with_decision()
    store.write(make_record(
        type="RULING", trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"ruling": "bill", "resolved_by": "user"},
    ))
    result = explain("t1", "s1", store=store)
    assert result["ruling"]["ruling"] == "bill"
```

```python
# tests/log/test_verify.py
from __future__ import annotations

from tally.log.memory import InMemoryRecordStore
from tally.log.records import Record, make_record
from tally.sdk.verify import VerificationFailure, verify


def _decision_record(units: int = 1) -> Record:
    return make_record(
        type="DECISION", trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={
            "decision": "BILL", "units": units, "reason": "",
            "review": False, "decided_at": "2024-06-01T12:00:00Z",
            "eval_snapshot": {"fix_landed": {"label": "yes", "score": 1.0}},
            "translate_source": "def translate(evals):\n    return bill(units=1)\n",
        },
    )


def test_verify_clean_record_returns_no_failures() -> None:
    store = InMemoryRecordStore()
    store.write(_decision_record())
    failures = verify(store=store)
    assert failures == []


def test_verify_detects_hash_tamper() -> None:
    """A Record with a mismatched content_hash is flagged."""
    store = InMemoryRecordStore()
    r = _decision_record()
    # Manually build a tampered record (bypass make_record by using dataclass replace)
    from dataclasses import replace
    tampered = replace(r, content_hash="0" * 64)
    # Directly inject into store's internal list (bypassing idempotency guard)
    store._records.append(tampered)  # type: ignore[attr-defined]
    failures = verify(store=store)
    assert len(failures) == 1
    assert failures[0].reason == "content_hash_mismatch"
    assert failures[0].trace_id == "t1"


def test_verify_detects_translate_replay_mismatch() -> None:
    """If translate source is changed in payload, replay will differ."""
    store = InMemoryRecordStore()
    # Modify translate source after the fact to return units=2 instead of 1
    r = _decision_record()
    from dataclasses import replace
    bad_payload = dict(r.payload)
    bad_payload["translate_source"] = "def translate(evals):\n    return bill(units=2)\n"
    # Use make_record so content_hash is consistent with modified payload
    r2 = make_record(
        type=r.type, trace_id=r.trace_id, span_id=r.span_id,
        customer_id=r.customer_id, policy_name=r.policy_name,
        policy_version=r.policy_version, policy_hash=r.policy_hash,
        payload=bad_payload,
    )
    store.write(r2)
    failures = verify(store=store)
    assert len(failures) == 1
    assert failures[0].reason == "translate_replay_mismatch"
```

- [ ] **Step 2: Run tests to verify they fail**

```
py -3.12 -m pytest tests/log/test_explain.py tests/log/test_verify.py -q
```
Expected: ImportError

- [ ] **Step 3: Create `tally/sdk/__init__.py`** (empty file)

- [ ] **Step 4: Implement `tally/sdk/explain.py`**

```python
from __future__ import annotations

from tally.log.records import Record
from tally.log.store import RecordStore


def explain(trace_id: str, span_id: str, *, store: RecordStore) -> dict[str, object]:
    """Assemble audit trail for a span from its records."""
    span_records = store.list(trace_id=trace_id, span_id=span_id)

    decision_record: Record | None = next(
        (r for r in span_records if r.type == "DECISION"), None
    )
    emission_record: Record | None = next(
        (r for r in span_records if r.type == "EMISSION"), None
    )
    ruling_record: Record | None = next(
        (r for r in span_records if r.type == "RULING"), None
    )

    result: dict[str, object] = {}

    if decision_record is not None:
        result["decision"] = dict(decision_record.payload)
        # Fetch policy declaration from POLICY record matching this version
        policy_records = store.list(type="POLICY")
        policy_record: Record | None = next(
            (
                r for r in policy_records
                if r.policy_name == decision_record.policy_name
                and r.policy_version == decision_record.policy_version
            ),
            None,
        )
        if policy_record is not None:
            result["policy"] = dict(policy_record.payload)

    if emission_record is not None:
        result["emission"] = dict(emission_record.payload)

    if ruling_record is not None:
        result["ruling"] = dict(ruling_record.payload)

    return result
```

- [ ] **Step 5: Implement `tally/sdk/verify.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from tally.log.records import compute_content_hash
from tally.log.store import RecordStore
from tally.policy.engine import _run_translate


@dataclass(frozen=True)
class VerificationFailure:
    trace_id: str
    span_id: str
    reason: str  # "content_hash_mismatch" | "translate_replay_mismatch"
    detail: str


def verify(
    trace_id: str | None = None,
    span_id: str | None = None,
    *,
    store: RecordStore,
) -> list[VerificationFailure]:
    """Check all DECISION records for tampering and translate replay consistency."""
    records = store.list(
        trace_id=trace_id,
        span_id=span_id,
        type="DECISION",
    )
    failures: list[VerificationFailure] = []
    for r in records:
        # 1. Recompute content hash
        expected_hash = compute_content_hash(
            type=r.type,
            trace_id=r.trace_id,
            span_id=r.span_id,
            customer_id=r.customer_id,
            policy_name=r.policy_name,
            policy_version=r.policy_version,
            policy_hash=r.policy_hash,
            payload=r.payload,
        )
        if expected_hash != r.content_hash:
            failures.append(
                VerificationFailure(
                    trace_id=r.trace_id,
                    span_id=r.span_id,
                    reason="content_hash_mismatch",
                    detail=f"stored={r.content_hash} recomputed={expected_hash}",
                )
            )
            continue  # no point replaying if hash is wrong

        # 2. Replay translate on stored eval snapshot
        translate_source = str(r.payload.get("translate_source", ""))
        eval_snapshot: dict[str, object] = dict(r.payload.get("eval_snapshot", {}))  # type: ignore[arg-type]
        stored_decision = str(r.payload.get("decision", ""))
        stored_units = r.payload.get("units")

        if not translate_source:
            continue  # nothing to replay

        result = _run_translate(translate_source, eval_snapshot, {})
        if result is None:
            # translate error
            failures.append(
                VerificationFailure(
                    trace_id=r.trace_id,
                    span_id=r.span_id,
                    reason="translate_replay_mismatch",
                    detail="translate raised or returned invalid type on replay",
                )
            )
            continue

        from tally.policy.declaration import BillResult, NoChargeResult, HoldResult

        replayed_decision: str
        replayed_units: int | None
        if isinstance(result, BillResult):
            replayed_decision = "BILL"
            replayed_units = result.units
        elif isinstance(result, NoChargeResult):
            replayed_decision = "NO_CHARGE"
            replayed_units = None
        else:
            replayed_decision = "HOLD"
            replayed_units = None

        if replayed_decision != stored_decision or (
            replayed_decision == "BILL" and replayed_units != stored_units
        ):
            failures.append(
                VerificationFailure(
                    trace_id=r.trace_id,
                    span_id=r.span_id,
                    reason="translate_replay_mismatch",
                    detail=(
                        f"stored={stored_decision}(units={stored_units}) "
                        f"replayed={replayed_decision}(units={replayed_units})"
                    ),
                )
            )
    return failures
```

- [ ] **Step 6: Update `tally/__init__.py` to re-export explain, verify, VerificationFailure**

Current `tally/__init__.py`:
```python
from tally.policy.declaration import BillResult, HoldResult, NoChargeResult

def bill(units: int) -> BillResult:
    return BillResult(units=units)

def no_charge(reason: str) -> NoChargeResult:
    return NoChargeResult(reason=reason)

def hold(reason: str) -> HoldResult:
    return HoldResult(reason=reason)
```

Add after the existing content:
```python
from tally.sdk.explain import explain as explain
from tally.sdk.verify import VerificationFailure as VerificationFailure
from tally.sdk.verify import verify as verify
```

- [ ] **Step 7: Run tests**

```
py -3.12 -m pytest tests/log/test_explain.py tests/log/test_verify.py -q
```
Expected: 7 passed (4 explain + 3 verify)

- [ ] **Step 8: Run full suite + lint**

```
py -3.12 -m pytest tests/ -q && py -3.12 -m ruff check . && py -3.12 -m mypy tally/
```
Expected: all clean

- [ ] **Step 9: Commit**

```bash
git add tally/sdk/__init__.py tally/sdk/explain.py tally/sdk/verify.py tally/__init__.py tests/log/test_explain.py tests/log/test_verify.py
git commit -m "feat: add explain() and verify() SDK functions"
```
