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
    last_polled: datetime | None = None
    cycles_polled: int = 0


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
    return r1


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
    # Rule 7: HoldResult
    assert isinstance(result, HoldResult)  # noqa: S101
    return Decision(
        outcome="HOLD",
        reason=result.reason,
        review=review,
        decided_at=now,
        eval_snapshot=eval_snapshot,
    )
