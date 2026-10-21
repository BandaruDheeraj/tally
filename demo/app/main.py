from __future__ import annotations

import asyncio
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import tally
from demo.trip_planner import _analyse_deals, _search_deals, _send_span
from tally.log.memory import InMemoryRecordStore

STATIC_DIR = Path(__file__).parent / "static"

_executor = ThreadPoolExecutor(max_workers=4)

_POLL_ATTEMPTS: int = 24
_POLL_SLEEP: float = 5.0


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


app = FastAPI(title="TravelAI Demo")
store = InMemoryRecordStore()

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _sse(type: str, **data: object) -> str:
    return "data: " + json.dumps({"type": type, **data}) + "\n\n"


async def _plan_generator(destination: str, user_id: str):  # type: ignore[return]
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
    session_id = uuid.uuid4().hex
    trace_id, span_id = await loop.run_in_executor(
        _executor,
        _send_span,
        api_key, space_id, user_id, session_id,
        destination, plan_text, quality_label, quality_score,
    )

    # Stages 5–7 added in Task 3
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
