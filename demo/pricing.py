"""Demo pricing file. Run against fixtures: python demo/pricing.py"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import tally
from tally.feed.file import FileEvalSource
from tally.log.memory import InMemoryRecordStore
from tally.worker import run_once


def translate(evals: object) -> object:
    return tally.bill(units=1)


policy = tally.policy(
    name="fix-landed",
    span="post_review_comment",
    customer="customer_id",
    evals=["fix_landed"],
    translate=translate,
)

if __name__ == "__main__":
    fixture = Path(__file__).parent / "fixtures" / "feed_fixture.json"
    source = FileEvalSource(fixture)
    store = InMemoryRecordStore()
    cursor = datetime(2024, 1, 1, tzinfo=timezone.utc)

    run_once(
        policies=[policy],
        source=source,
        store=store,
        cursor=cursor,
        stripe_key=None,
        mode="shadow",
    )

    decisions = store.list(type="DECISION")
    emissions = store.list(type="EMISSION")
    print(f"Decisions: {len(decisions)}, Emissions (shadow): {len(emissions)}")
    for d in decisions:
        print(f"  {d.trace_id} → {d.payload['decision']}")
