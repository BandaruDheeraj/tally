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
