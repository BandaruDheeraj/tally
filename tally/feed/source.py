from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tally.policy.declaration import Policy


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


def _parse_eval_dict(e: dict[str, object]) -> "EvalResult":
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


def poll_interval(
    cycles_polled: int,
    base: timedelta = timedelta(seconds=60),
    cap: timedelta = timedelta(minutes=30),
) -> timedelta:
    """Exponential backoff: min(base * 2^cycles_polled, cap)."""
    base_secs = base.total_seconds()
    cap_secs = cap.total_seconds()
    # Avoid overflow: if exponent would push past cap, return cap directly
    try:
        seconds = base_secs * (2**cycles_polled)
    except (OverflowError, ValueError):
        return cap
    if seconds >= cap_secs:
        return cap
    return timedelta(seconds=seconds)


class EvalSource(ABC):
    @abstractmethod
    def list_new_spans(self, policy: "Policy", cursor: datetime) -> list[SpanInfo]: ...

    @abstractmethod
    def get_evals(self, trace_id: str, span_id: str) -> list[EvalResult]: ...
