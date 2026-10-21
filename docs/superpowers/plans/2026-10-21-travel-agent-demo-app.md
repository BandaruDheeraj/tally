# Travel Agent Demo App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-stack travel agent web app that runs the complete production tally billing pipeline (Agent → Arize → Tally → Shadow Stripe) and makes every stage visible in the UI.

**Architecture:** FastAPI backend with three routes; the `POST /api/plan` route streams SSE events as the pipeline progresses (search → LLM → Arize span → Arize eval poll → tally decision). Plain HTML/CSS/JS frontend — no build step. Reuses all existing `demo/trip_planner.py` helpers and `tally.*` internals unchanged.

**Tech Stack:** Python 3.12, FastAPI ≥ 0.111, uvicorn ≥ 0.29, vanilla JS (ES2020), existing tally package

## Global Constraints

- Python 3.12+ required (project-wide)
- No new Python dependencies beyond `fastapi`, `uvicorn`, `aiofiles` (add to `[demo]` extras in `pyproject.toml`)
- No JS build step — plain ES2020 in a single `app.js`
- All tally billing runs in shadow mode — no Stripe key needed
- Arize credentials via `ARIZE_API_KEY`, `ARIZE_SPACE_ID`, `TALLY_PROJECT` (default: `tally-demo`)
- LLM via `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY`
- Arize eval polling: new `AXEvalSource` instance per attempt (busts 60 s cache), 5 s sleep between attempts, 24 attempts max (2 min total)
- Follow existing code style: `from __future__ import annotations`, ruff-clean, mypy-strict

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `pyproject.toml` | Add `[demo]` extras: `fastapi`, `uvicorn`, `aiofiles` |
| Create | `demo/app/__init__.py` | Empty package marker |
| Create | `demo/app/main.py` | FastAPI app, all routes, SSE generator, `InMemoryRecordStore` singleton |
| Create | `demo/app/static/index.html` | Two-column page shell |
| Create | `demo/app/static/style.css` | Dark theme, two-column layout, message bubbles, billing cards |
| Create | `demo/app/static/app.js` | SSE client (fetch + ReadableStream), DOM updates, ledger |
| Create | `tests/app/__init__.py` | Empty package marker |
| Create | `tests/app/test_main.py` | Tests for all three routes |

---

## Task 1: FastAPI scaffold — GET /, GET /api/decisions, static files

**Files:**
- Modify: `pyproject.toml`
- Create: `demo/app/__init__.py`
- Create: `demo/app/main.py`
- Create: `demo/app/static/index.html` (placeholder)
- Create: `tests/app/__init__.py`
- Create: `tests/app/test_main.py`

**Interfaces:**
- Produces:
  - `app: FastAPI` — importable as `demo.app.main:app`
  - `store: InMemoryRecordStore` — module-level singleton, used by later tasks
  - `GET /` → `FileResponse(index.html)`
  - `GET /api/decisions` → `JSONResponse([])` initially

- [ ] **Step 1: Add `[demo]` extras to pyproject.toml**

Open `pyproject.toml` and add after the existing `[project.optional-dependencies]` `dev` group:

```toml
demo = [
    "fastapi>=0.111",
    "uvicorn>=0.29",
    "aiofiles>=23.0",
]
```

- [ ] **Step 2: Install demo extras**

```bash
pip install -e ".[demo]"
```

Expected: installs fastapi, uvicorn, aiofiles with no errors.

- [ ] **Step 3: Write failing tests**

Create `tests/app/__init__.py` (empty).

Create `tests/app/test_main.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient


def test_index_returns_html() -> None:
    from demo.app.main import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_decisions_empty_initially() -> None:
    from demo.app.main import app, store
    store._records.clear()
    store._seen.clear()
    client = TestClient(app)
    response = client.get("/api/decisions")
    assert response.status_code == 200
    assert response.json() == []
```

- [ ] **Step 4: Run tests to confirm they fail**

```bash
pytest tests/app/test_main.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `demo.app.main` does not exist yet.

- [ ] **Step 5: Create `demo/app/__init__.py`**

```python
```

(empty file)

- [ ] **Step 6: Create `demo/app/static/index.html` placeholder**

```html
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>TravelAI</title></head>
<body><p>Loading...</p></body>
</html>
```

- [ ] **Step 7: Create `demo/app/main.py`**

```python
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tally.log.memory import InMemoryRecordStore

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="TravelAI Demo")
store = InMemoryRecordStore()

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/decisions")
async def decisions() -> JSONResponse:
    records = store.list(type="DECISION")
    return JSONResponse([
        {
            "trace_id": r.trace_id,
            "customer_id": r.customer_id,
            "policy_name": r.policy_name,
            "decision": r.payload.get("decision"),
            "units": r.payload.get("units"),
            "reason": r.payload.get("reason"),
            "eval_snapshot": r.payload.get("eval_snapshot"),
            "decided_at": r.payload.get("decided_at"),
            "destination": r.payload.get("destination"),
        }
        for r in records
    ])
```

- [ ] **Step 8: Run tests — expect pass**

```bash
pytest tests/app/test_main.py -v
```

Expected:
```
tests/app/test_main.py::test_index_returns_html PASSED
tests/app/test_main.py::test_decisions_empty_initially PASSED
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml demo/app/__init__.py demo/app/main.py demo/app/static/index.html tests/app/__init__.py tests/app/test_main.py
git commit -m "feat: add FastAPI scaffold with GET / and GET /api/decisions"
```

---

## Task 2: POST /api/plan — search + LLM + span stages (SSE events 1–4)

**Files:**
- Modify: `demo/app/main.py`
- Modify: `tests/app/test_main.py`

**Interfaces:**
- Consumes:
  - `demo.trip_planner._search_deals(destination: str) -> list[dict[str, str]]`
  - `demo.trip_planner._analyse_deals(destination: str, results: list[dict[str, str]]) -> tuple[str, str, float]`
  - `demo.trip_planner._send_span(api_key, space_id, user_id, session_id, destination, plan, label, score) -> tuple[str, str]`
- Produces:
  - `POST /api/plan` body: `{"destination": str, "user_id": str}`
  - SSE events in order: `status`, `status`, `chunk`, `status`
  - `_plan_generator(destination, user_id)` — async generator, importable for tests

- [ ] **Step 1: Write failing test**

Add to `tests/app/test_main.py`:

```python
import asyncio
import json
from unittest.mock import MagicMock, patch


async def _collect(gen: object) -> list[dict]:  # type: ignore[type-arg]
    """Collect all SSE data payloads from an async generator."""
    events: list[dict] = []  # type: ignore[type-arg]
    async for chunk in gen:  # type: ignore[union-attr]
        text = chunk if isinstance(chunk, str) else chunk.decode()
        for line in text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_plan_generator_stages_1_to_4() -> None:
    """Generator emits status, status, chunk, status before Arize poll."""
    mock_results = [{"title": "Cheap flights", "body": "From $299", "href": ""}]

    with (
        patch("demo.app.main._search_deals", return_value=mock_results),
        patch(
            "demo.app.main._analyse_deals",
            return_value=("Great trip plan for Tokyo.", "good", 0.85),
        ),
        patch(
            "demo.app.main._send_span",
            return_value=("a" * 32, "b" * 16),
        ),
        patch("demo.app.main.AXEvalSource") as mock_cls,
        patch.dict(
            "os.environ",
            {"ARIZE_API_KEY": "k", "ARIZE_SPACE_ID": "s"},
        ),
    ):
        # Make the first get_evals call block forever so we only see stages 1-4
        mock_source = MagicMock()
        mock_cls.return_value = mock_source
        mock_source.get_evals.return_value = []  # no evals → loop runs out

        from demo.app.main import _plan_generator

        events = asyncio.run(_collect(_plan_generator("Tokyo", "alice")))

    types = [e["type"] for e in events]
    # First four events: searching, analysing, chunk, sending
    assert types[0] == "status"
    assert "Search" in events[0]["message"]
    assert types[1] == "status"
    assert "Analys" in events[1]["message"]
    chunk_events = [e for e in events if e["type"] == "chunk"]
    assert chunk_events, "expected at least one chunk event"
    assert chunk_events[0]["text"] == "Great trip plan for Tokyo."
    send_events = [e for e in events if e["type"] == "status" and "Arize" in e["message"]]
    assert send_events, "expected a 'Sending span to Arize' status event"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/app/test_main.py::test_plan_generator_stages_1_to_4 -v
```

Expected: `ImportError` — `_plan_generator` not defined yet.

- [ ] **Step 3: Add imports and policy definition to `demo/app/main.py`**

Add after the existing imports block at the top of `demo/app/main.py`:

```python
import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

import tally
from demo.trip_planner import _analyse_deals, _search_deals, _send_span
from tally.feed.ax import AXEvalSource
from tally.log.records import make_record
from tally.policy.engine import Unit, decide

_executor = ThreadPoolExecutor(max_workers=4)


def _translate(evals: object) -> object:
    dq = evals.get("deal_quality")  # type: ignore[union-attr]
    if dq is not None and dq.label == "good":  # type: ignore[union-attr]
        return tally.bill(units=1)
    label = dq.label if dq is not None else None  # type: ignore[union-attr]
    return tally.no_charge(reason="deal_quality=" + str(label))


_POLICY = tally.policy(
    name="trip-planner",
    span="plan_trip",
    customer="user.id",
    evals=["deal_quality"],
    translate=_translate,
)


class PlanRequest(BaseModel):
    destination: str
    user_id: str = "demo-user"
```

- [ ] **Step 4: Add `_sse` helper and `_plan_generator` (stages 1–4 only) to `demo/app/main.py`**

Add after `PlanRequest`:

```python
def _sse(type: str, **data: object) -> str:
    return "data: " + json.dumps({"type": type, **data}) + "\n\n"


async def _plan_generator(destination: str, user_id: str):  # type: ignore[return]
    loop = asyncio.get_event_loop()

    # Stage 1: search
    yield _sse("status", message="Searching for deals...")
    results = await loop.run_in_executor(_executor, _search_deals, destination)

    # Stage 2: LLM analysis
    yield _sse("status", message="Analysing with LLM...")
    plan_text, quality_label, quality_score = await loop.run_in_executor(
        _executor, _analyse_deals, destination, results
    )

    # Stage 3: stream plan text
    yield _sse("chunk", text=plan_text)

    # Stage 4: send span to Arize
    yield _sse("status", message="Sending span to Arize...")
    api_key = os.environ["ARIZE_API_KEY"]
    space_id = os.environ["ARIZE_SPACE_ID"]
    session_id = uuid.uuid4().hex
    trace_id, span_id = await loop.run_in_executor(
        _executor,
        _send_span,
        api_key, space_id, user_id, session_id,
        destination, plan_text, quality_label, quality_score,
    )

    # Stages 5–7 added in Task 3
    yield _sse("done")
```

- [ ] **Step 5: Add `POST /api/plan` route to `demo/app/main.py`**

Add after the `decisions` route:

```python
from fastapi import Request  # add to existing fastapi import line
from fastapi.responses import StreamingResponse  # add to existing fastapi.responses import line


@app.post("/api/plan")
async def plan(body: PlanRequest) -> StreamingResponse:
    return StreamingResponse(
        _plan_generator(body.destination, body.user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 6: Run test — expect pass**

```bash
pytest tests/app/test_main.py::test_plan_generator_stages_1_to_4 -v
```

Expected: `PASSED`

- [ ] **Step 7: Run full test suite — no regressions**

```bash
pytest tests/ -v
```

Expected: all existing tests pass, new test passes.

- [ ] **Step 8: Commit**

```bash
git add demo/app/main.py tests/app/test_main.py
git commit -m "feat: add POST /api/plan SSE — search, LLM, and span stages"
```

---

## Task 3: POST /api/plan — Arize polling + tally decision (SSE stages 5–7)

**Files:**
- Modify: `demo/app/main.py`
- Modify: `tests/app/test_main.py`

**Interfaces:**
- Consumes:
  - `AXEvalSource(api_key, space_id, project).get_evals(trace_id, span_id) -> list[EvalResult]`
  - `decide(_POLICY, unit, declared_evals, {}, now) -> Decision | None`
  - `make_record(type, trace_id, span_id, customer_id, policy_name, policy_version, policy_hash, payload) -> Record`
  - `store.write(record)`
- Produces:
  - SSE `eval` event: `{"type": "eval", "evals": {"deal_quality": {"label": str, "score": float}}}`
  - SSE `decision` event: `{"type": "decision", "outcome": str, "units": int, "reason": str, "eval_snapshot": dict}`
  - SSE `done` event: `{"type": "done"}`
  - `GET /api/decisions` now returns decisions written to `store`

- [ ] **Step 1: Write failing tests**

Add to `tests/app/test_main.py`:

```python
def test_plan_generator_bill_decision() -> None:
    """Generator emits BILL decision when deal_quality=good."""
    mock_results = [{"title": "Cheap flights", "body": "From $299", "href": ""}]
    mock_eval = MagicMock()
    mock_eval.name = "deal_quality"
    mock_eval.label = "good"
    mock_eval.score = 0.85
    mock_eval.agreement = None
    mock_eval.explanation = ""
    mock_eval.evaluator_version = None
    from datetime import datetime, timezone
    mock_eval.scored_at = datetime.now(tz=timezone.utc)
    mock_eval.raw = {}

    with (
        patch("demo.app.main._search_deals", return_value=mock_results),
        patch(
            "demo.app.main._analyse_deals",
            return_value=("Great trip plan.", "good", 0.85),
        ),
        patch("demo.app.main._send_span", return_value=("a" * 32, "b" * 16)),
        patch("demo.app.main.AXEvalSource") as mock_cls,
        patch.dict(
            "os.environ",
            {"ARIZE_API_KEY": "k", "ARIZE_SPACE_ID": "s"},
        ),
    ):
        mock_source = MagicMock()
        mock_cls.return_value = mock_source
        mock_source.get_evals.return_value = [mock_eval]

        from demo.app.main import _plan_generator, store
        store._records.clear()
        store._seen.clear()

        events = asyncio.run(_collect(_plan_generator("Tokyo", "alice")))

    types = [e["type"] for e in events]
    assert "eval" in types
    assert "decision" in types
    assert types[-1] == "done"

    decision_event = next(e for e in events if e["type"] == "decision")
    assert decision_event["outcome"] == "BILL"
    assert decision_event["units"] == 1

    assert len(store.list(type="DECISION")) == 1


def test_plan_generator_no_charge_decision() -> None:
    """Generator emits NO_CHARGE when deal_quality=poor."""
    mock_eval = MagicMock()
    mock_eval.name = "deal_quality"
    mock_eval.label = "poor"
    mock_eval.score = 0.3
    mock_eval.agreement = None
    mock_eval.explanation = ""
    mock_eval.evaluator_version = None
    from datetime import datetime, timezone
    mock_eval.scored_at = datetime.now(tz=timezone.utc)
    mock_eval.raw = {}

    with (
        patch("demo.app.main._search_deals", return_value=[]),
        patch(
            "demo.app.main._analyse_deals",
            return_value=("Vague plan.", "poor", 0.3),
        ),
        patch("demo.app.main._send_span", return_value=("c" * 32, "d" * 16)),
        patch("demo.app.main.AXEvalSource") as mock_cls,
        patch.dict(
            "os.environ",
            {"ARIZE_API_KEY": "k", "ARIZE_SPACE_ID": "s"},
        ),
    ):
        mock_source = MagicMock()
        mock_cls.return_value = mock_source
        mock_source.get_evals.return_value = [mock_eval]

        from demo.app.main import _plan_generator, store
        store._records.clear()
        store._seen.clear()

        events = asyncio.run(_collect(_plan_generator("Nowhere", "bob")))

    decision_event = next(e for e in events if e["type"] == "decision")
    assert decision_event["outcome"] == "NO_CHARGE"


def test_plan_generator_hold_on_eval_timeout() -> None:
    """Generator emits HOLD when Arize never returns evals."""
    with (
        patch("demo.app.main._search_deals", return_value=[]),
        patch(
            "demo.app.main._analyse_deals",
            return_value=("Plan.", "good", 0.8),
        ),
        patch("demo.app.main._send_span", return_value=("e" * 32, "f" * 16)),
        patch("demo.app.main.AXEvalSource") as mock_cls,
        patch("demo.app.main._POLL_ATTEMPTS", 2),  # override to 2 attempts for speed
        patch("demo.app.main._POLL_SLEEP", 0),      # no sleep in tests
        patch.dict(
            "os.environ",
            {"ARIZE_API_KEY": "k", "ARIZE_SPACE_ID": "s"},
        ),
    ):
        mock_source = MagicMock()
        mock_cls.return_value = mock_source
        mock_source.get_evals.return_value = []  # never returns evals

        from demo.app.main import _plan_generator

        events = asyncio.run(_collect(_plan_generator("Nowhere", "carol")))

    decision_event = next(e for e in events if e["type"] == "decision")
    assert decision_event["outcome"] == "HOLD"
    assert decision_event["reason"] == "eval_timeout"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/app/test_main.py::test_plan_generator_bill_decision tests/app/test_main.py::test_plan_generator_no_charge_decision tests/app/test_main.py::test_plan_generator_hold_on_eval_timeout -v
```

Expected: all FAIL — `_POLL_ATTEMPTS` and `_POLL_SLEEP` not defined, Arize poll logic missing.

- [ ] **Step 3: Add poll constants and replace the stub `yield _sse("done")` in `_plan_generator`**

At module level in `demo/app/main.py`, add after `_executor`:

```python
_POLL_ATTEMPTS: int = 24   # 24 × 5 s = 2 min
_POLL_SLEEP: float = 5.0
```

Replace the existing `_plan_generator` body (keep stages 1–4 unchanged, replace the final `yield _sse("done")`) with:

```python
async def _plan_generator(destination: str, user_id: str):  # type: ignore[return]
    loop = asyncio.get_event_loop()

    # Stage 1: search
    yield _sse("status", message="Searching for deals...")
    results = await loop.run_in_executor(_executor, _search_deals, destination)

    # Stage 2: LLM analysis
    yield _sse("status", message="Analysing with LLM...")
    plan_text, quality_label, quality_score = await loop.run_in_executor(
        _executor, _analyse_deals, destination, results
    )

    # Stage 3: stream plan text
    yield _sse("chunk", text=plan_text)

    # Stage 4: send span to Arize
    yield _sse("status", message="Sending span to Arize...")
    api_key = os.environ["ARIZE_API_KEY"]
    space_id = os.environ["ARIZE_SPACE_ID"]
    session_id = uuid.uuid4().hex
    trace_id, span_id = await loop.run_in_executor(
        _executor,
        _send_span,
        api_key, space_id, user_id, session_id,
        destination, plan_text, quality_label, quality_score,
    )

    # Stage 5: poll Arize for evals
    project = os.environ.get("TALLY_PROJECT", "tally-demo")
    evals = []
    for attempt in range(1, _POLL_ATTEMPTS + 1):
        yield _sse("status", message=f"Waiting for eval... (attempt {attempt})")
        source = AXEvalSource(api_key=api_key, space_id=space_id, project=project)
        raw_evals = await loop.run_in_executor(
            _executor, source.get_evals, trace_id, span_id
        )
        if raw_evals:
            evals = raw_evals
            break
        await asyncio.sleep(_POLL_SLEEP)

    if not evals:
        yield _sse("decision", outcome="HOLD", units=0, reason="eval_timeout")
        yield _sse("done")
        return

    # Stage 6: emit eval event
    evals_dict = {e.name: e for e in evals}
    yield _sse(
        "eval",
        evals={
            name: {"label": e.label, "score": e.score}
            for name, e in evals_dict.items()
        },
    )

    # Stage 7: tally decide → store → decision event
    now = datetime.now(tz=timezone.utc)
    unit = Unit(
        trace_id=trace_id,
        span_id=span_id,
        customer_id=user_id,
        policy_name=_POLICY.name,
        policy_version=_POLICY.version,
        first_seen=now,
        deadline=now + _POLICY.wait,
    )
    declared_evals = {n: evals_dict[n] for n in _POLICY.evals if n in evals_dict}
    decision = decide(_POLICY, unit, declared_evals, {}, now)

    if decision is None:
        yield _sse("decision", outcome="HOLD", units=0, reason="no_decision")
        yield _sse("done")
        return

    store.write(make_record(
        type="DECISION",
        trace_id=trace_id,
        span_id=span_id,
        customer_id=user_id,
        policy_name=_POLICY.name,
        policy_version=_POLICY.version,
        policy_hash=_POLICY.version,
        payload={
            "decision": decision.outcome,
            "units": decision.units,
            "reason": decision.reason,
            "review": decision.review,
            "decided_at": decision.decided_at.isoformat(),
            "eval_snapshot": decision.eval_snapshot,
            "translate_source": _POLICY.translate,
            "destination": destination,
        },
    ))

    yield _sse(
        "decision",
        outcome=decision.outcome,
        units=decision.units,
        reason=decision.reason,
        eval_snapshot=decision.eval_snapshot,
    )
    yield _sse("done")
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/app/test_main.py -v
```

Expected: all tests PASSED.

- [ ] **Step 5: Smoke-test the API end-to-end (optional — requires real credentials)**

```bash
uvicorn demo.app.main:app --port 8000 &
curl -s http://localhost:8000/api/decisions
```

Expected: `[]`

- [ ] **Step 6: Commit**

```bash
git add demo/app/main.py tests/app/test_main.py
git commit -m "feat: add Arize polling and tally decision to plan SSE stream"
```

---

## Task 4: Frontend — HTML shell + CSS

**Files:**
- Modify: `demo/app/static/index.html`
- Create: `demo/app/static/style.css`

**Interfaces:**
- Produces (DOM IDs consumed by `app.js`):
  - `#chat-form` — form element
  - `#destination` — text input
  - `#user-id` — text input
  - `#messages` — message thread container
  - `#ledger-body` — `<tbody>` for billing table rows
  - `#ledger-footer` — summary line
  - `.policy-code` — `<code>` block showing translate function

- [ ] **Step 1: Replace `demo/app/static/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>TravelAI — powered by tally</title>
  <link rel="stylesheet" href="/static/style.css" />
</head>
<body>
  <div class="layout">

    <!-- Left: Chat -->
    <div class="chat-panel">
      <header class="chat-header">
        <span class="brand">TravelAI</span>
        <span class="badge">powered by tally</span>
      </header>
      <div class="messages" id="messages"></div>
      <form class="input-row" id="chat-form">
        <input
          type="text"
          id="destination"
          placeholder="Where do you want to go?"
          autocomplete="off"
        />
        <input
          type="text"
          id="user-id"
          placeholder="Your name"
          value="alice"
        />
        <button type="submit">Plan Trip</button>
      </form>
    </div>

    <!-- Right: Billing Ledger -->
    <div class="billing-panel">
      <div class="billing-header">
        <span class="billing-title">Tally Billing</span>
        <span class="mode-pill">shadow mode</span>
      </div>
      <table class="ledger">
        <thead>
          <tr>
            <th>Destination</th>
            <th>Decision</th>
            <th>Score</th>
            <th>Units</th>
          </tr>
        </thead>
        <tbody id="ledger-body"></tbody>
      </table>
      <div class="ledger-footer" id="ledger-footer">No trips yet</div>
      <details class="how-it-works">
        <summary>How it works</summary>
        <pre><code class="policy-code">def translate(evals):
    dq = evals.get("deal_quality")
    if dq is not None and dq.label == "good":
        return bill(units=1)
    label = dq.label if dq is not None else None
    return no_charge(reason="deal_quality=" + str(label))</code></pre>
        <p class="how-explanation">
          Tally reads eval results from Arize AX after each AI response.
          If <code>deal_quality = good</code>, the customer is billed
          1 credit. Otherwise, no charge. Running in
          <strong>shadow mode</strong> — no real Stripe charges.
        </p>
      </details>
    </div>

  </div>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `demo/app/static/style.css`**

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #0f1117;
  color: #e2e8f0;
  height: 100vh;
  overflow: hidden;
}

.layout {
  display: flex;
  height: 100vh;
}

/* ── Chat panel ─────────────────────────────────────────── */
.chat-panel {
  flex: 0 0 65%;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #1e2330;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 24px;
  border-bottom: 1px solid #1e2330;
  background: #0f1117;
  flex-shrink: 0;
}

.brand {
  font-size: 20px;
  font-weight: 700;
  color: #60a5fa;
}

.badge {
  font-size: 11px;
  background: #1e2330;
  color: #94a3b8;
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid #2d3748;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message {
  max-width: 80%;
  padding: 12px 16px;
  border-radius: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.message.user {
  background: #1e40af;
  align-self: flex-end;
}

.message.agent {
  background: #1e2330;
  align-self: flex-start;
}

.status-indicator {
  font-size: 13px;
  color: #60a5fa;
  font-style: italic;
  padding: 2px 0 6px;
  min-height: 22px;
}

/* Billing card appended inside each agent message */
.billing-card {
  margin-top: 12px;
  padding: 10px 14px;
  background: #0a0d14;
  border-radius: 8px;
  border: 1px solid #2d3748;
  font-size: 13px;
}

.outcome-badge {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  font-weight: 600;
  font-size: 12px;
  margin-bottom: 6px;
  letter-spacing: 0.03em;
}

.outcome-badge.bill    { background: #065f46; color: #6ee7b7; }
.outcome-badge.no-charge { background: #1e2330; color: #94a3b8; }
.outcome-badge.hold    { background: #78350f; color: #fcd34d; }

.billing-card .eval-line { color: #94a3b8; }

/* Input row */
.input-row {
  display: flex;
  gap: 8px;
  padding: 16px 24px;
  border-top: 1px solid #1e2330;
  background: #0f1117;
  flex-shrink: 0;
}

.input-row input[type="text"] {
  background: #1e2330;
  border: 1px solid #2d3748;
  color: #e2e8f0;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
  transition: border-color 0.15s;
}

.input-row input[type="text"]:focus { border-color: #3b82f6; }

#destination { flex: 1; }
#user-id     { width: 110px; }

.input-row button {
  background: #2563eb;
  color: #fff;
  border: none;
  padding: 10px 20px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
}

.input-row button:hover    { background: #1d4ed8; }
.input-row button:disabled { background: #374151; cursor: not-allowed; }

/* ── Billing panel ──────────────────────────────────────── */
.billing-panel {
  flex: 0 0 35%;
  display: flex;
  flex-direction: column;
  padding: 24px;
  gap: 16px;
  overflow-y: auto;
}

.billing-header {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.billing-title { font-size: 16px; font-weight: 700; }

.mode-pill {
  font-size: 11px;
  background: #78350f;
  color: #fcd34d;
  padding: 3px 8px;
  border-radius: 999px;
}

.ledger {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.ledger th {
  text-align: left;
  color: #64748b;
  font-weight: 500;
  padding: 6px 8px;
  border-bottom: 1px solid #1e2330;
}

.ledger td {
  padding: 8px;
  border-bottom: 1px solid #1e2330;
  color: #e2e8f0;
}

.ledger .bill-cell      { color: #6ee7b7; font-weight: 600; }
.ledger .no-charge-cell { color: #64748b; }
.ledger .hold-cell      { color: #fcd34d; }

.ledger-footer {
  font-size: 13px;
  color: #64748b;
  padding: 4px 8px;
}

/* How it works */
.how-it-works {
  margin-top: auto;
  border: 1px solid #1e2330;
  border-radius: 8px;
  overflow: hidden;
}

.how-it-works summary {
  padding: 10px 14px;
  cursor: pointer;
  font-size: 13px;
  color: #94a3b8;
  user-select: none;
  background: #1e2330;
  list-style: none;
}

.how-it-works summary:hover { background: #2d3748; }

.policy-code {
  display: block;
  padding: 16px;
  font-family: "JetBrains Mono", "Fira Code", "Consolas", monospace;
  font-size: 12px;
  line-height: 1.65;
  color: #e2e8f0;
  background: #0f1117;
  overflow-x: auto;
  white-space: pre;
}

.how-explanation {
  padding: 10px 16px 14px;
  font-size: 13px;
  color: #94a3b8;
  line-height: 1.65;
  background: #0f1117;
}

.how-explanation code {
  background: #1e2330;
  padding: 1px 5px;
  border-radius: 3px;
  font-family: monospace;
  font-size: 12px;
}
```

- [ ] **Step 3: Verify page loads**

```bash
uvicorn demo.app.main:app --port 8000
```

Open `http://localhost:8000` in a browser. Expected: two-column layout with "TravelAI" header on the left and "Tally Billing / shadow mode" on the right. The "How it works" section should be collapsed and expandable.

- [ ] **Step 4: Commit**

```bash
git add demo/app/static/index.html demo/app/static/style.css
git commit -m "feat: add two-column HTML/CSS layout for travel agent demo"
```

---

## Task 5: Frontend JavaScript — SSE client, chat, billing ledger

**Files:**
- Create: `demo/app/static/app.js`

**Interfaces:**
- Consumes (backend):
  - `POST /api/plan` → SSE stream with event types: `status`, `chunk`, `eval`, `decision`, `done`
  - `GET /api/decisions` → `[{destination, decision, eval_snapshot, units, decided_at}]`
- Consumes (DOM):
  - `#chat-form`, `#destination`, `#user-id`, `#messages`, `#ledger-body`, `#ledger-footer`

- [ ] **Step 1: Create `demo/app/static/app.js`**

```javascript
/* app.js — TravelAI SSE client */
(function () {
  "use strict";

  const form       = document.getElementById("chat-form");
  const messagesEl = document.getElementById("messages");
  const ledgerBody = document.getElementById("ledger-body");
  const footerEl   = document.getElementById("ledger-footer");
  const submitBtn  = form.querySelector("button[type='submit']");

  let billed   = 0;
  let noCharge = 0;

  /* ── Helpers ────────────────────────────────────────── */

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  /* ── DOM builders ───────────────────────────────────── */

  function appendUserMessage(text) {
    const div = document.createElement("div");
    div.className = "message user";
    div.textContent = text;
    messagesEl.appendChild(div);
    scrollBottom();
  }

  function createAgentMessage() {
    const wrap = document.createElement("div");
    wrap.className = "message agent";

    const statusEl = document.createElement("div");
    statusEl.className = "status-indicator";
    wrap.appendChild(statusEl);

    const planEl = document.createElement("div");
    wrap.appendChild(planEl);

    messagesEl.appendChild(wrap);
    scrollBottom();
    return { wrap, statusEl, planEl };
  }

  function setStatus(statusEl, text) {
    statusEl.textContent = text;
    scrollBottom();
  }

  function appendBillingCard(parentEl, outcome, units, reason, evalSnapshot) {
    const card = document.createElement("div");
    card.className = "billing-card";

    const outcomeLabel =
      outcome === "BILL"
        ? "BILL \u00B7 " + units + " unit" + (units !== 1 ? "s" : "")
        : outcome;
    const badgeClass =
      outcome === "BILL" ? "bill" : outcome === "NO_CHARGE" ? "no-charge" : "hold";

    const dq = evalSnapshot && evalSnapshot.deal_quality;
    const evalLine = dq
      ? "deal_quality = " +
        escapeHtml(dq.label || "?") +
        " (" +
        (dq.score !== null && dq.score !== undefined
          ? Number(dq.score).toFixed(2)
          : "\u2014") +
        ")"
      : escapeHtml(reason || "");

    card.innerHTML =
      '<span class="outcome-badge ' +
      badgeClass +
      '">' +
      escapeHtml(outcomeLabel) +
      "</span>" +
      '<div class="eval-line">' +
      evalLine +
      "</div>";

    parentEl.appendChild(card);
    scrollBottom();
  }

  function addLedgerRow(destination, outcome, score, units) {
    const tr = document.createElement("tr");
    const cellClass =
      outcome === "BILL"
        ? "bill-cell"
        : outcome === "NO_CHARGE"
        ? "no-charge-cell"
        : "hold-cell";
    const scoreStr =
      score !== null && score !== undefined
        ? Number(score).toFixed(2)
        : "\u2014";
    tr.innerHTML =
      "<td>" + escapeHtml(destination) + "</td>" +
      '<td class="' + cellClass + '">' + escapeHtml(outcome) + "</td>" +
      "<td>" + scoreStr + "</td>" +
      "<td>" + escapeHtml(String(units)) + "</td>";
    ledgerBody.appendChild(tr);

    if (outcome === "BILL") billed++;
    else if (outcome === "NO_CHARGE") noCharge++;

    footerEl.textContent =
      billed + " billed \u00B7 " + noCharge + " no charge \u00B7 $0 (shadow)";
  }

  /* ── SSE stream consumer ────────────────────────────── */

  async function streamPlan(destination, userId) {
    const { statusEl, planEl } = createAgentMessage();
    let evalSnapshot = null;

    const response = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination: destination, user_id: userId }),
    });

    if (!response.ok) {
      setStatus(statusEl, "Error: " + response.status);
      submitBtn.disabled = false;
      return;
    }

    const reader  = response.body.getReader();
    const decoder = new TextDecoder();
    let   buffer  = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep incomplete last line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let evt;
        try {
          evt = JSON.parse(line.slice(6));
        } catch (_) {
          continue;
        }

        if (evt.type === "status") {
          setStatus(statusEl, evt.message);
        } else if (evt.type === "chunk") {
          statusEl.textContent = "";
          planEl.textContent   = evt.text;
          scrollBottom();
        } else if (evt.type === "eval") {
          evalSnapshot = evt.evals;
        } else if (evt.type === "decision") {
          setStatus(statusEl, "");
          const dqScore =
            evalSnapshot && evalSnapshot.deal_quality
              ? evalSnapshot.deal_quality.score
              : null;
          appendBillingCard(
            planEl.parentElement,
            evt.outcome,
            evt.units,
            evt.reason,
            evalSnapshot
          );
          addLedgerRow(destination, evt.outcome, dqScore, evt.units);
        } else if (evt.type === "done") {
          setStatus(statusEl, "");
          submitBtn.disabled = false;
        }
      }
    }

    submitBtn.disabled = false;
  }

  /* ── Page init — restore ledger from server ─────────── */

  async function restoreLedger() {
    try {
      const res  = await fetch("/api/decisions");
      const data = await res.json();
      for (const d of data) {
        const dq    = d.eval_snapshot && d.eval_snapshot.deal_quality;
        const score = dq ? dq.score : null;
        addLedgerRow(
          d.destination || d.customer_id,
          d.decision,
          score,
          d.units || 0
        );
      }
    } catch (_) {
      /* ignore — ledger starts empty */
    }
  }

  /* ── Form handler ───────────────────────────────────── */

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    const destination = document.getElementById("destination").value.trim();
    const userId      = document.getElementById("user-id").value.trim() || "demo-user";
    if (!destination) return;

    submitBtn.disabled = true;
    document.getElementById("destination").value = "";

    appendUserMessage("Plan a trip to " + destination);
    streamPlan(destination, userId).catch(function (err) {
      console.error("streamPlan error:", err);
      submitBtn.disabled = false;
    });
  });

  restoreLedger();
})();
```

- [ ] **Step 2: Manual smoke test — full flow**

```bash
export ARIZE_API_KEY="<your key>"
export ARIZE_SPACE_ID="<your space>"
export ANTHROPIC_API_KEY="<your key>"   # or OPENROUTER_API_KEY
export TALLY_PROJECT="tally-demo"
uvicorn demo.app.main:app --port 8000 --reload
```

Open `http://localhost:8000`. Type "Tokyo" + your name → click Plan Trip.

Expected sequence:
1. User message appears immediately
2. Agent bubble shows "Searching for deals..." → "Analysing with LLM..."
3. Plan text appears in the bubble
4. Status shows "Sending span to Arize..." → "Waiting for eval... (attempt 1)" → ...
5. When Arize returns the eval, a billing card appears: green `BILL · 1 unit` (or grey `NO CHARGE`)
6. Right panel ledger gets a new row with destination, decision, score, units
7. Footer updates: "1 billed · 0 no charge · $0 (shadow)"

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASSED (no frontend tests — JS is tested manually).

- [ ] **Step 4: Commit**

```bash
git add demo/app/static/app.js
git commit -m "feat: add JS SSE client, chat bubbles, and billing ledger"
```

---

## Running the Finished App

```bash
pip install -e ".[demo]"

export ARIZE_API_KEY="..."
export ARIZE_SPACE_ID="..."
export ANTHROPIC_API_KEY="..."   # or OPENROUTER_API_KEY
export TALLY_PROJECT="tally-demo"

uvicorn demo.app.main:app --port 8000 --reload
# open http://localhost:8000
```
