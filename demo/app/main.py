from __future__ import annotations

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
