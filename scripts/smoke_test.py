"""Smoke test: full worker pipeline with InMemoryRecordStore (shadow mode).

Also verifies OTLP write reaches Arize by sending one record directly.
Set ARIZE_API_KEY and ARIZE_SPACE_ID env vars to enable the OTLP write check.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from tally.feed.file import FileEvalSource
from tally.log.memory import InMemoryRecordStore
from tally.policy.declaration import make_policy
from tally.worker import run_once


def translate(evals: object) -> object:
    return bill(units=1)  # type: ignore[name-defined]  # noqa: F821


def main() -> None:
    api_key = os.environ.get("ARIZE_API_KEY")
    space_id = os.environ.get("ARIZE_SPACE_ID")

    policy = make_policy(
        "fix-landed", "post_review_comment", "customer_id",
        ["fix_landed"], translate,
    )

    source = FileEvalSource(Path("tests/feed/fixtures/feed_fixture.json"))
    store = InMemoryRecordStore()

    print("Running full worker pipeline (shadow mode, FileEvalSource)...")
    run_once(
        policies=[policy],
        source=source,
        store=store,
        cursor=datetime(2024, 1, 1, tzinfo=timezone.utc),
        stripe_key=None,
        mode="shadow",
    )

    decisions = store.list(type="DECISION")
    emissions = store.list(type="EMISSION")

    print(f"DECISION records: {len(decisions)}")
    for d in decisions:
        print(f"  {d.trace_id}/{d.span_id} -> {d.payload['decision']} "
              f"(units={d.payload['units']})")
    print(f"EMISSION records: {len(emissions)}")
    for e in emissions:
        print(f"  {e.trace_id}/{e.span_id} shadow={e.payload['shadow']} "
              f"status={e.payload['status']}")

    assert len(decisions) > 0, "No decisions produced"
    assert len(emissions) > 0, "No emissions produced"
    assert all(e.payload["shadow"] is True for e in emissions), "Non-shadow emission in shadow mode"
    print("\nAll assertions passed.")

    # Optional: verify OTLP write reaches Arize
    if api_key and space_id:
        print("\nVerifying OTLP write to Arize...")
        import requests
        now_ns = int(time.time() * 1e9)
        record = decisions[0]
        payload = {
            "resourceSpans": [{
                "resource": {"attributes": [
                    {"key": "model_id", "value": {"stringValue": "tally-demo"}},
                    {"key": "openinference.project.name",
                     "value": {"stringValue": "tally-demo"}},
                ]},
                "scopeSpans": [{"scope": {"name": "tally"}, "spans": [{
                    "traceId": record.trace_id.replace("-", "").ljust(32, "0")[:32],
                    "spanId": record.span_id.replace("-", "").ljust(16, "0")[:16],
                    "name": record.type,
                    "kind": 1,
                    "startTimeUnixNano": now_ns - 1_000_000_000,
                    "endTimeUnixNano": now_ns,
                    "attributes": [{"key": "input.value", "value": {
                        "stringValue": json.dumps({
                            "type": record.type,
                            "trace_id": record.trace_id,
                            "decision": record.payload.get("decision"),
                        })
                    }}],
                }]}],
            }],
        }
        hdrs = {"api_key": api_key, "space_id": space_id, "content-type": "application/json"}
        resp = requests.post("https://otlp.arize.com/v1/traces",
                             headers=hdrs, json=payload, timeout=15)
        print(f"OTLP write: {resp.status_code} "
              f"{'OK' if resp.status_code == 200 else resp.text[:100]}")


if __name__ == "__main__":
    main()
