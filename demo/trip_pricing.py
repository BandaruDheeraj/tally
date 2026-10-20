"""Trip planner billing policy.

Reads plan_trip spans + deal_quality evals from Arize, then decides
whether to bill the customer (good deal → 1 unit, poor deal → no charge).

Usage:
    python demo/trip_pricing.py          # shadow mode (no real Stripe calls)
    python demo/trip_pricing.py --live   # live mode (requires STRIPE_KEY)

Required env vars:
    ARIZE_API_KEY, ARIZE_SPACE_ID
Optional:
    TALLY_PROJECT   (default: tally-demo)
    STRIPE_KEY      (required for --live)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import tally
from tally.feed.ax import AXEvalSource
from tally.log.ax import AXRecordStore
from tally.worker import run_once


def translate(evals: object) -> object:  # evals: dict[str, EvalResult]
    dq = evals.get("deal_quality")  # type: ignore[union-attr]
    if dq is not None and dq.label == "good":  # type: ignore[union-attr]
        return bill(units=1)  # type: ignore[name-defined]  # noqa: F821
    label = dq.label if dq is not None else None  # type: ignore[union-attr]
    return no_charge(reason="deal_quality=" + str(label))  # type: ignore[name-defined]  # noqa: F821


policy = tally.policy(
    name="trip-planner",
    span="plan_trip",
    customer="user.id",
    evals=["deal_quality"],
    translate=translate,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Trip planner billing worker")
    parser.add_argument("--live", action="store_true",
                        help="Live mode — fire real Stripe meter events")
    parser.add_argument("--cursor", default="2024-01-01",
                        help="ISO date to process spans from (default: 2024-01-01)")
    args = parser.parse_args()

    api_key = os.environ.get("ARIZE_API_KEY", "")
    space_id = os.environ.get("ARIZE_SPACE_ID", "")

    if not api_key or not space_id:
        print("Error: set ARIZE_API_KEY and ARIZE_SPACE_ID", file=sys.stderr)
        sys.exit(1)

    project = os.environ.get("TALLY_PROJECT", "tally-demo")
    stripe_key = os.environ.get("STRIPE_KEY") if args.live else None
    mode = "live" if args.live else "shadow"

    source = AXEvalSource.from_env()
    store = AXRecordStore.from_env()
    cursor = datetime.fromisoformat(args.cursor).replace(tzinfo=timezone.utc)

    print(f"Running trip-planner billing ({mode} mode)")
    print(f"  Project : {project}")
    print(f"  Cursor  : {cursor.date()}")
    print()

    run_once(
        policies=[policy],
        source=source,
        store=store,
        cursor=cursor,
        stripe_key=stripe_key,
        mode=mode,
    )

    decisions = store.list(type="DECISION")
    emissions = store.list(type="EMISSION")

    trip_decisions = [d for d in decisions if d.policy_name == "trip-planner"]
    trip_emissions = [e for e in emissions if e.policy_name == "trip-planner"]

    if not trip_decisions:
        print("No trip-planner decisions found.")
        print("Run demo/trip_planner.py first to generate some spans.")
        return

    print(f"{'TRACE':40}  {'CUSTOMER':15}  {'DECISION':8}  {'UNITS':5}  EVAL")
    print("-" * 90)
    for d in trip_decisions:
        snap = d.payload.get("eval_snapshot", {})
        eval_str = "  ".join(
            f"{name}={v.get('label','?')}({v.get('score',0):.2f})"
            for name, v in snap.items()
        )
        print(
            f"{d.trace_id[:38]:40}  "
            f"{d.customer_id[:15]:15}  "
            f"{d.payload['decision']:8}  "
            f"{d.payload['units']:5}  "
            f"{eval_str}"
        )

    print()
    billed = sum(1 for d in trip_decisions if d.payload["decision"] == "BILL")
    no_charge = len(trip_decisions) - billed
    print(f"Summary: {billed} billed, {no_charge} no-charge, "
          f"{len(trip_emissions)} emissions ({mode})")


if __name__ == "__main__":
    main()
