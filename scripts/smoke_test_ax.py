"""End-to-end smoke test using AXRecordStore (real Arize write + read-back).

Writes records via OTLP then polls the v2 REST API until they appear
(Arize has ingestion latency; typically < 30 s).

Required env vars:
  ARIZE_API_KEY   – Arize API key
  ARIZE_SPACE_ID  – Arize space ID
  TALLY_PROJECT   – Arize project name to write tally records into
                    (default: tally-demo)
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tally.feed.file import FileEvalSource
from tally.log.ax import AXRecordStore
from tally.log.records import make_record
from tally.worker import run_once


def translate(evals: object) -> object:
    return bill(units=1)  # type: ignore[name-defined]  # noqa: F821


def _poll(store: AXRecordStore, type: str, target_trace_ids: set[str],
          *, timeout: int = 90, interval: int = 5) -> list[object]:
    """Poll store.list() until all target_trace_ids appear or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        records = store.list(type=type)  # type: ignore[arg-type]
        found = {r.trace_id for r in records} & target_trace_ids
        if found == target_trace_ids:
            return records
        remaining = int(deadline - time.time())
        print(f"  waiting for {type} records ({len(found)}/{len(target_trace_ids)} found,"
              f" {remaining}s left)...")
        time.sleep(interval)
    return store.list(type=type)  # type: ignore[arg-type]


def main() -> None:
    api_key = os.environ.get("ARIZE_API_KEY", "")
    space_id = os.environ.get("ARIZE_SPACE_ID", "")
    project = os.environ.get("TALLY_PROJECT", "tally-demo")

    if not api_key or not space_id:
        print("Set ARIZE_API_KEY and ARIZE_SPACE_ID", file=sys.stderr)
        sys.exit(1)

    store = AXRecordStore(api_key=api_key, space_id=space_id, project=project)

    # ------------------------------------------------------------------
    # Stage 1: write a single known record directly and read it back
    # ------------------------------------------------------------------
    probe_trace = str(uuid.uuid4())
    probe_span = str(uuid.uuid4())[:8]
    probe = make_record(
        type="DECISION",
        trace_id=probe_trace,
        span_id=probe_span,
        customer_id="smoke-customer",
        policy_name="smoke-policy",
        policy_version="v0",
        policy_hash="h0",
        payload={
            "decision": "BILL",
            "units": 1,
            "reason": "smoke test",
            "review": None,
            "decided_at": datetime.now(tz=timezone.utc).isoformat(),
            "eval_snapshot": {},
            "translate_source": "",
        },
    )

    print(f"Writing probe record trace_id={probe_trace[:8]}… to project={project!r}")
    store.write(probe)
    print("Write OK (OTLP 200). Polling for read-back...")

    records = _poll(store, "DECISION", {probe_trace})
    matched = [r for r in records if r.trace_id == probe_trace]

    if not matched:
        print("FAIL: probe record not found after polling timeout")
        sys.exit(1)

    r = matched[0]
    assert r.trace_id == probe_trace, f"trace_id mismatch: {r.trace_id}"
    assert r.span_id == probe_span, f"span_id mismatch: {r.span_id}"
    assert r.customer_id == "smoke-customer"
    assert r.payload["decision"] == "BILL"
    assert r.content_hash == probe.content_hash, "content_hash mismatch — record corrupted in transit"
    print(f"Read-back OK: trace_id={r.trace_id[:8]}… decision={r.payload['decision']}")

    # ------------------------------------------------------------------
    # Stage 2: full worker pipeline with AXRecordStore
    # ------------------------------------------------------------------
    from tally.policy.declaration import make_policy  # noqa: PLC0415

    policy = make_policy(
        "ax-smoke", "post_review_comment", "customer_id",
        ["fix_landed"], translate,
    )
    source = FileEvalSource(Path("tests/feed/fixtures/feed_fixture.json"))

    print("\nRunning full worker pipeline (shadow, AXRecordStore)...")
    run_once(
        policies=[policy],
        source=source,
        store=store,
        cursor=datetime(2024, 1, 1, tzinfo=timezone.utc),
        stripe_key=None,
        mode="shadow",
    )
    print("run_once complete. Polling for DECISION + EMISSION records...")

    # The fixture produces 2 spans → 2 DECISION + 2 EMISSION records
    # We can identify them by policy_name
    def _policy_records(recs: list, policy_name: str) -> list:  # type: ignore[type-arg]
        return [r for r in recs if r.policy_name == policy_name]

    # Poll decisions
    all_decisions = _poll(store, "DECISION", set(), timeout=90, interval=5)
    policy_decisions = _policy_records(all_decisions, "ax-smoke")

    # Poll emissions
    all_emissions = _poll(store, "EMISSION", set(), timeout=90, interval=5)
    policy_emissions = _policy_records(all_emissions, "ax-smoke")

    # Give it up to 2 more poll cycles if emissions haven't landed yet
    deadline = time.time() + 60
    while len(policy_emissions) == 0 and time.time() < deadline:
        time.sleep(5)
        all_emissions = store.list(type="EMISSION")  # type: ignore[arg-type]
        policy_emissions = _policy_records(all_emissions, "ax-smoke")
        print(f"  emissions so far: {len(policy_emissions)}")

    print(f"\nDECISION records for policy 'ax-smoke': {len(policy_decisions)}")
    for d in policy_decisions:
        print(f"  {d.trace_id[:8]}…/{d.span_id[:8]}… -> {d.payload['decision']}"
              f" (units={d.payload['units']})")

    print(f"EMISSION records for policy 'ax-smoke': {len(policy_emissions)}")
    for e in policy_emissions:
        print(f"  {e.trace_id[:8]}…/{e.span_id[:8]}… shadow={e.payload['shadow']}"
              f" status={e.payload['status']}")

    assert len(policy_decisions) >= 2, (
        f"Expected >=2 DECISION records for ax-smoke, got {len(policy_decisions)}"
    )
    assert len(policy_emissions) >= 2, (
        f"Expected >=2 EMISSION records for ax-smoke, got {len(policy_emissions)}"
    )
    assert all(e.payload["shadow"] is True for e in policy_emissions), \
        "Non-shadow emission in shadow mode"

    print("\nAll assertions passed. AXRecordStore end-to-end OK.")


if __name__ == "__main__":
    main()
