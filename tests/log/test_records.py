from __future__ import annotations

from tally.log.records import compute_content_hash, make_record


def _base_kwargs() -> dict[str, object]:
    return {
        "type": "DECISION",
        "trace_id": "t1",
        "span_id": "s1",
        "customer_id": "cust-1",
        "policy_name": "p",
        "policy_version": "abc123",
        "policy_hash": "def456",
        "payload": {"decision": "BILL", "units": 1},
    }


def test_make_record_sets_content_hash() -> None:
    r = make_record(**_base_kwargs())  # type: ignore[arg-type]
    assert len(r.content_hash) == 64  # SHA-256 hex


def test_content_hash_is_deterministic() -> None:
    r1 = make_record(**_base_kwargs())  # type: ignore[arg-type]
    r2 = make_record(**_base_kwargs())  # type: ignore[arg-type]
    assert r1.content_hash == r2.content_hash


def test_content_hash_changes_on_payload_change() -> None:
    r1 = make_record(**_base_kwargs())  # type: ignore[arg-type]
    kwargs = _base_kwargs()
    kwargs["payload"] = {"decision": "NO_CHARGE", "units": 0}
    r2 = make_record(**kwargs)  # type: ignore[arg-type]
    assert r1.content_hash != r2.content_hash


def test_tamper_detection() -> None:
    """Recomputing hash on a tampered record reveals the mismatch."""
    r = make_record(**_base_kwargs())  # type: ignore[arg-type]
    tampered_payload = {**r.payload, "units": 999}
    expected_hash = compute_content_hash(
        type=r.type,
        trace_id=r.trace_id,
        span_id=r.span_id,
        customer_id=r.customer_id,
        policy_name=r.policy_name,
        policy_version=r.policy_version,
        policy_hash=r.policy_hash,
        payload=tampered_payload,
    )
    assert expected_hash != r.content_hash


def test_sentinel_fields_for_non_span_records() -> None:
    """POLICY and MODE records use empty strings for span identity."""
    r = make_record(
        type="POLICY",
        trace_id="",
        span_id="",
        customer_id="",
        policy_name="p",
        policy_version="v1",
        policy_hash="h1",
        payload={"translate": "def translate(evals): ..."},
    )
    assert r.trace_id == ""
    assert r.span_id == ""
    assert r.customer_id == ""
