from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import requests as _requests
import tally
from demo.trip_planner import _analyse_deals, _search_deals, _send_span
from tally import bill, no_charge
from tally.feed.ax import AXEvalSource, _parse_eval_attrs, _parse_evaluations
from tally.feed.source import _parse_dt
from tally.log.memory import InMemoryRecordStore
from tally.log.records import make_record
from tally.policy.engine import Unit, decide

STATIC_DIR = Path(__file__).parent / "static"

_executor = ThreadPoolExecutor(max_workers=4)

_POLL_ATTEMPTS: int = 24
_POLL_SLEEP: float = 5.0

# Arize evaluator task ID (Deal Quality - tally-demo)
_ARIZE_TASK_ID = "T25saW5lVGFzazo1MTg5NDpVMkk5"
_ARIZE_REST_BASE = "https://api.arize.com/v2"


def translate(evals: object) -> object:
    dq = evals.get("deal_quality")  # type: ignore[union-attr]
    if dq is not None and dq.label == "good":  # type: ignore[union-attr]
        return bill(units=100)
    label = dq.label if dq is not None else "none"  # type: ignore[union-attr]
    return no_charge(reason="deal_quality=" + label)


_POLICY = tally.policy(
    name="trip-planner",
    span="plan_trip",
    customer="user.id",
    evals=["deal_quality"],
    translate=translate,
)


class PlanRequest(BaseModel):
    destination: str
    user_id: str = "demo-user"


app = FastAPI(title="TravelAI Demo")
store = InMemoryRecordStore()

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _sse(type: str, **data: object) -> str:
    return "data: " + json.dumps({"type": type, **data}) + "\n\n"


def _trigger_and_wait_eval(
    api_key: str,
    space_id: str,
    session_id: str,
    project_id: str,
    span_end_time: str,
    max_wait: int = 90,
) -> None:
    """Wait for span ingest, trigger Arize evaluator task, wait for completion."""
    from datetime import timedelta
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-space-id": space_id,
        "Content-Type": "application/json",
    }

    # Step 1: wait until span appears in Arize (up to 30s)
    for _ in range(10):
        time.sleep(3)
        r = _requests.post(
            f"{_ARIZE_REST_BASE}/spans",
            headers=headers,
            json={"project_id": project_id},
            timeout=15,
        )
        r.raise_for_status()
        spans = r.json().get("spans", [])
        for span in spans:
            attrs: dict = span.get("attributes") or {}
            if attrs.get("session.id") == session_id:
                span_end_time = str(span.get("end_time", span_end_time))
                break
        else:
            continue
        break  # found the span

    # Step 2: trigger evaluator task for the span's time window
    try:
        end_dt = _parse_dt(span_end_time)
    except Exception:
        end_dt = datetime.now(tz=timezone.utc)


    window_start = (end_dt - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    window_end = (end_dt + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    resp = _requests.post(
        f"{_ARIZE_REST_BASE}/tasks/{_ARIZE_TASK_ID}/trigger",
        headers=headers,
        json={"data_start_time": window_start, "data_end_time": window_end},
        timeout=15,
    )
    resp.raise_for_status()
    run_id = resp.json()["id"]

    # Step 3: poll until task run COMPLETED or FAILED
    for _ in range(max_wait // 3):
        time.sleep(3)
        r = _requests.get(
            f"{_ARIZE_REST_BASE}/task-runs/{run_id}",
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        status = r.json().get("status", "")
        if status in ("COMPLETED", "FAILED", "CANCELED"):
            return
    # Timed out — caller will still attempt to read evals


def _evals_by_session(source: AXEvalSource, session_id: str) -> list:
    """Find a span by session.id and return its Arize-computed evaluations.

    Arize re-encodes OTLP trace/span IDs on ingest (treats hex as base64),
    so we cannot match by trace_id/span_id. session.id is a plain string
    attribute that Arize preserves exactly, making it a reliable lookup key.

    Evals come from span["evaluations"] — results written by Arize's
    continuous LLM evaluator task, not from span attributes we self-embed.
    """
    for span in source._all_spans():
        attrs: dict = span.get("attributes") or {}
        if attrs.get("session.id") == session_id:
            evaluations: list = span.get("evaluations") or []
            if evaluations:
                return _parse_evaluations(evaluations)
            # Fall back to embedded eval attrs only if evaluations not present yet
            return _parse_eval_attrs(attrs, str(span.get("end_time", "")))
    return []


async def _plan_generator(destination: str, user_id: str) -> AsyncGenerator[str, None]:
    try:
        loop = asyncio.get_running_loop()

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
        project = os.environ.get("TALLY_PROJECT", "tally-demo")
        session_id = uuid.uuid4().hex
        span_end_time = datetime.now(tz=timezone.utc).isoformat()
        trace_id, span_id = await loop.run_in_executor(
            _executor,
            _send_span,
            api_key, space_id, user_id, session_id,
            destination, plan_text,
        )

        # Stage 4.5: trigger Arize LLM evaluator and wait for it to score this span.
        # Arize's continuous task runs on its own schedule; we trigger it explicitly
        # after confirming the span is indexed, so the eval is ready within ~30s.
        yield _sse("status", message="Triggering Arize evaluator...")
        source_for_trigger = AXEvalSource(api_key=api_key, space_id=space_id, project=project)
        arize_project_id = await loop.run_in_executor(
            _executor, source_for_trigger._get_project_id
        )
        await loop.run_in_executor(
            _executor,
            _trigger_and_wait_eval,
            api_key, space_id, session_id, arize_project_id, span_end_time,
        )

        # Stage 5: poll Arize for evals by session.id
        # (Arize transforms OTLP trace/span IDs on ingest, so we match by
        # the session.id attribute which is preserved exactly as sent.)
        evals = []
        for attempt in range(1, _POLL_ATTEMPTS + 1):
            yield _sse("status", message=f"Waiting for eval... (attempt {attempt})")
            source = AXEvalSource(api_key=api_key, space_id=space_id, project=project)
            raw_evals = await loop.run_in_executor(
                _executor, _evals_by_session, source, session_id
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
    except Exception as exc:  # noqa: BLE001
        yield _sse("error", message=str(exc))
        yield _sse("done")


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


@app.post("/api/plan")
async def plan(body: PlanRequest) -> StreamingResponse:
    return StreamingResponse(
        _plan_generator(body.destination, body.user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
