from __future__ import annotations

from tally.log.memory import InMemoryRecordStore
from tally.log.records import make_record
from tally.sdk.explain import explain


def _store_with_decision() -> InMemoryRecordStore:
    store = InMemoryRecordStore()
    store.write(make_record(
        type="POLICY",
        trace_id="", span_id="", customer_id="",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"translate": "def translate(evals): return bill(units=1)"},
    ))
    store.write(make_record(
        type="DECISION",
        trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={
            "decision": "BILL", "units": 1, "reason": "",
            "decided_at": "2024-06-01T12:00:00Z",
            "eval_snapshot": {"fix_landed": {"label": "yes", "score": 1.0}},
        },
    ))
    return store


def test_explain_returns_policy_and_decision() -> None:
    store = _store_with_decision()
    result = explain("t1", "s1", store=store)
    assert result["decision"]["decision"] == "BILL"  # type: ignore[index]
    assert result["policy"]["translate"] == "def translate(evals): return bill(units=1)"  # type: ignore[index]


def test_explain_includes_emission_when_present() -> None:
    store = _store_with_decision()
    store.write(make_record(
        type="EMISSION",
        trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"stripe_identifier": "t1::s1", "shadow": False, "status": "sent"},
    ))
    result = explain("t1", "s1", store=store)
    assert result["emission"]["stripe_identifier"] == "t1::s1"  # type: ignore[index]


def test_explain_emission_key_absent_when_not_emitted() -> None:
    store = _store_with_decision()
    result = explain("t1", "s1", store=store)
    assert "emission" not in result


def test_explain_includes_ruling_when_present() -> None:
    store = _store_with_decision()
    store.write(make_record(
        type="RULING",
        trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"ruling": "bill", "resolved_by": "user"},
    ))
    result = explain("t1", "s1", store=store)
    assert result["ruling"]["ruling"] == "bill"  # type: ignore[index]
