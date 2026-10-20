from __future__ import annotations

from dataclasses import dataclass

from tally.feed.source import _EPOCH, EvalResult
from tally.log.records import compute_content_hash
from tally.log.store import RecordStore
from tally.policy.declaration import BillResult, HoldResult, NoChargeResult
from tally.policy.engine import _run_translate


@dataclass(frozen=True)
class VerificationFailure:
    trace_id: str
    span_id: str
    reason: str  # "content_hash_mismatch" | "translate_replay_mismatch"
    detail: str


def _snapshot_to_evals(snapshot: dict[str, object]) -> dict[str, EvalResult]:
    """Reconstruct minimal EvalResult objects from a stored eval snapshot."""
    result: dict[str, EvalResult] = {}
    for name, raw in snapshot.items():
        data: dict[str, object] = raw if isinstance(raw, dict) else {}
        label = data.get("label")
        score_raw = data.get("score")
        agreement = data.get("agreement")
        result[name] = EvalResult(
            name=name,
            label=str(label) if label is not None else None,
            score=float(score_raw) if isinstance(score_raw, (int, float)) else None,
            agreement=str(agreement) if agreement is not None else None,
            explanation="",
            evaluator_version=None,
            scored_at=_EPOCH,
            raw={},
        )
    return result


def verify(
    trace_id: str | None = None,
    span_id: str | None = None,
    *,
    store: RecordStore,
) -> list[VerificationFailure]:
    """Check all DECISION records for tampering and translate replay consistency."""
    records = store.list(trace_id=trace_id, span_id=span_id, type="DECISION")
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
        if not translate_source:
            continue

        raw_snapshot = r.payload.get("eval_snapshot", {})
        eval_snapshot: dict[str, object] = (
            dict(raw_snapshot) if isinstance(raw_snapshot, dict) else {}
        )
        stored_decision = str(r.payload.get("decision", ""))
        stored_units = r.payload.get("units")

        evals = _snapshot_to_evals(eval_snapshot)
        try:
            result = _run_translate(translate_source, evals)
        except Exception:
            failures.append(
                VerificationFailure(
                    trace_id=r.trace_id,
                    span_id=r.span_id,
                    reason="translate_replay_mismatch",
                    detail="translate raised or returned invalid type on replay",
                )
            )
            continue

        if isinstance(result, BillResult):
            replayed_decision: str = "BILL"
            replayed_units: int | None = result.units
        elif isinstance(result, NoChargeResult):
            replayed_decision = "NO_CHARGE"
            replayed_units = None
        else:
            assert isinstance(result, HoldResult)  # noqa: S101
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
