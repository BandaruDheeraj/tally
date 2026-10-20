from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from tally.emit.stripe import StripeEmitter
from tally.feed.source import _EPOCH, EvalResult, EvalSource
from tally.log.records import make_record
from tally.log.store import RecordStore
from tally.policy.declaration import Policy
from tally.policy.engine import Unit, decide

logger = logging.getLogger(__name__)

_GRACE = timedelta(hours=1)


def run_once(
    *,
    policies: list[Policy],
    source: EvalSource,
    store: RecordStore,
    cursor: datetime,
    stripe_key: str | None,
    mode: Literal["shadow", "live"],
) -> None:
    """Execute one four-stage worker cycle."""
    now = datetime.now(tz=timezone.utc)

    # Stage 1: Discover new spans; skip already-decided
    # Key includes policy_name so two policies can independently decide the same span.
    decided_spans = {
        (r.policy_name, r.trace_id, r.span_id)
        for r in store.list(type="DECISION")
    }
    units_map: dict[tuple[str, str, str], tuple[Unit, Policy]] = {}

    for policy in policies:
        try:
            spans = source.list_new_spans(policy, cursor)
        except Exception:
            logger.exception("list_new_spans failed policy=%s", policy.name)
            continue
        for span in spans:
            key = (policy.name, span.trace_id, span.span_id)
            if key in decided_spans:
                continue
            units_map[key] = (
                Unit(
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    customer_id=span.customer_id,
                    policy_name=policy.name,
                    policy_version=policy.version,
                    first_seen=now,
                    deadline=span.ended_at + policy.wait + _GRACE,
                ),
                policy,
            )

    if not units_map:
        return

    # Stage 2: Poll evals for each pending unit
    evals_cache: dict[tuple[str, str, str], list[EvalResult]] = {}
    for key, (unit, _) in units_map.items():
        try:
            evals_cache[key] = source.get_evals(unit.trace_id, unit.span_id)
        except Exception:
            logger.exception(
                "get_evals failed trace_id=%s span_id=%s", unit.trace_id, unit.span_id
            )

    # Stage 3: Run engine; write DECISION records
    for key, (unit, policy) in units_map.items():
        if key not in evals_cache:
            continue

        all_evals = {e.name: e for e in evals_cache[key]}
        declared_evals = {n: all_evals[n] for n in policy.evals if n in all_evals}
        watch_evals = {n: all_evals[n] for n in policy.watch if n in all_evals}

        decision = decide(policy, unit, declared_evals, watch_evals, now)
        if decision is None:
            continue

        store.write(make_record(
            type="DECISION",
            trace_id=unit.trace_id,
            span_id=unit.span_id,
            customer_id=unit.customer_id,
            policy_name=unit.policy_name,
            policy_version=unit.policy_version,
            policy_hash=policy.version,
            payload={
                "decision": decision.outcome,
                "units": decision.units,
                "reason": decision.reason,
                "review": decision.review,
                "decided_at": decision.decided_at.isoformat(),
                "eval_snapshot": decision.eval_snapshot,
                "translate_source": policy.translate,
            },
        ))

    # Stage 4: Emit — one emitter per policy so each uses its own meter name.
    for policy in policies:
        StripeEmitter(
            store=store,
            mode=mode,
            stripe_key=stripe_key,
            meter=policy.meter,
            policy_name=policy.name,
        ).emit()


def run(
    *,
    policies: list[Policy],
    source: EvalSource,
    store: RecordStore,
    stripe_key: str | None,
    mode: Literal["shadow", "live"],
    sleep_seconds: int = 60,
) -> None:
    """Blocking worker loop. Runs run_once() every sleep_seconds forever."""
    cursor = _EPOCH
    while True:
        try:
            run_once(
                policies=policies,
                source=source,
                store=store,
                cursor=cursor,
                stripe_key=stripe_key,
                mode=mode,
            )
            cursor = datetime.now(tz=timezone.utc)
        except Exception:
            logger.exception("run_once failed unexpectedly")
        time.sleep(sleep_seconds)
