from __future__ import annotations

from dataclasses import replace

from tally.log.memory import InMemoryRecordStore
from tally.log.records import Record, make_record
from tally.sdk.verify import verify


def _decision_record(units: int = 1) -> Record:
    return make_record(
        type="DECISION",
        trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={
            "decision": "BILL", "units": units, "reason": "",
            "review": False, "decided_at": "2024-06-01T12:00:00Z",
            "eval_snapshot": {"fix_landed": {"label": "yes", "score": 1.0}},
            "translate_source": "def translate(evals):\n    return bill(units=1)\n",
        },
    )


def test_verify_clean_record_returns_no_failures() -> None:
    store = InMemoryRecordStore()
    store.write(_decision_record())
    failures = verify(store=store)
    assert failures == []


def test_verify_detects_hash_tamper() -> None:
    """A Record with a mismatched content_hash is flagged."""
    store = InMemoryRecordStore()
    r = _decision_record()
    tampered = replace(r, content_hash="0" * 64)
    store._records.append(tampered)  # type: ignore[attr-defined]
    failures = verify(store=store)
    assert len(failures) == 1
    assert failures[0].reason == "content_hash_mismatch"
    assert failures[0].trace_id == "t1"


def test_verify_detects_translate_replay_mismatch() -> None:
    """If translate source is changed in payload, replay will differ from stored decision."""
    store = InMemoryRecordStore()
    r = _decision_record()
    bad_payload = dict(r.payload)
    bad_payload["translate_source"] = "def translate(evals):\n    return bill(units=2)\n"
    r2 = make_record(
        type=r.type, trace_id=r.trace_id, span_id=r.span_id,
        customer_id=r.customer_id, policy_name=r.policy_name,
        policy_version=r.policy_version, policy_hash=r.policy_hash,
        payload=bad_payload,
    )
    store.write(r2)
    failures = verify(store=store)
    assert len(failures) == 1
    assert failures[0].reason == "translate_replay_mismatch"
