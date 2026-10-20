from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tally.emit.stripe import StripeEmitter
from tally.log.memory import InMemoryRecordStore
from tally.log.records import Record, make_record


def _bill_decision(
    trace_id: str = "t1",
    span_id: str = "s1",
    customer_id: str = "cust-1",
    policy_name: str = "p",
    decided_at: str = "2026-10-15T12:00:00Z",
    units: int = 1,
) -> Record:
    return make_record(
        type="DECISION",
        trace_id=trace_id, span_id=span_id, customer_id=customer_id,
        policy_name=policy_name, policy_version="v1", policy_hash="h1",
        payload={
            "decision": "BILL", "units": units,
            "decided_at": decided_at,
        },
    )


def _mock_stripe_ok() -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {"id": "evt_001", "object": "billing.meter_event"}
    return m


def test_shadow_mode_writes_emission_no_http() -> None:
    store = InMemoryRecordStore()
    store.write(_bill_decision())
    emitter = StripeEmitter(store=store, mode="shadow", stripe_key=None, meter="p")
    with patch("requests.post") as mock_post:
        emitter.emit()
    mock_post.assert_not_called()
    emissions = store.list(type="EMISSION")
    assert len(emissions) == 1
    assert emissions[0].payload["shadow"] is True


def test_live_mode_posts_to_stripe_and_writes_emission() -> None:
    store = InMemoryRecordStore()
    store.write(_bill_decision())
    emitter = StripeEmitter(store=store, mode="live", stripe_key="sk_test_xxx", meter="p")
    with patch("requests.post", return_value=_mock_stripe_ok()):
        emitter.emit()
    emissions = store.list(type="EMISSION")
    assert len(emissions) == 1
    e = emissions[0]
    assert e.payload["shadow"] is False
    assert e.payload["status"] == "sent"
    assert e.payload["stripe_identifier"] == "t1::s1"


def test_already_emitted_decision_is_skipped() -> None:
    store = InMemoryRecordStore()
    store.write(_bill_decision())
    store.write(make_record(
        type="EMISSION", trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"stripe_identifier": "t1::s1", "shadow": True, "status": "sent"},
    ))
    emitter = StripeEmitter(store=store, mode="shadow", stripe_key=None, meter="p")
    with patch("requests.post") as mock_post:
        emitter.emit()
    mock_post.assert_not_called()
    assert len(store.list(type="EMISSION")) == 1  # no new emission


def test_non_bill_decision_is_skipped() -> None:
    store = InMemoryRecordStore()
    store.write(make_record(
        type="DECISION", trace_id="t1", span_id="s1", customer_id="cust-1",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"decision": "NO_CHARGE", "decided_at": "2026-10-15T12:00:00Z"},
    ))
    emitter = StripeEmitter(store=store, mode="shadow", stripe_key=None, meter="p")
    with patch("requests.post") as mock_post:
        emitter.emit()
    mock_post.assert_not_called()
    assert store.list(type="EMISSION") == []


def test_old_decision_guard_writes_failed_emission() -> None:
    """BILL decisions older than 35 days get EMISSION(status='failed'), no Stripe call."""
    store = InMemoryRecordStore()
    store.write(_bill_decision(decided_at="2020-01-01T00:00:00Z"))
    emitter = StripeEmitter(store=store, mode="live", stripe_key="sk_test_xxx", meter="p")
    with patch("requests.post") as mock_post:
        emitter.emit()
    mock_post.assert_not_called()
    emissions = store.list(type="EMISSION")
    assert len(emissions) == 1
    assert emissions[0].payload["status"] == "failed"


def test_live_mode_requires_stripe_key() -> None:
    store = InMemoryRecordStore()
    with pytest.raises(ValueError, match="STRIPE_RESTRICTED_KEY"):
        StripeEmitter(store=store, mode="live", stripe_key=None, meter="p")


def test_mode_gate_skips_shadow_period_decisions() -> None:
    """Decisions made before the live-mode switch get EMISSION(shadow=True), no Stripe call."""
    store = InMemoryRecordStore()
    store.write(make_record(
        type="MODE", trace_id="", span_id="", customer_id="",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"mode": "live", "switched_at": "2026-10-15T14:00:00Z"},
    ))
    store.write(_bill_decision(decided_at="2026-10-15T12:00:00Z"))
    emitter = StripeEmitter(store=store, mode="live", stripe_key="sk_test_xxx", meter="p")
    with patch("requests.post") as mock_post:
        emitter.emit()
    mock_post.assert_not_called()
    emissions = store.list(type="EMISSION")
    assert len(emissions) == 1
    assert emissions[0].payload["shadow"] is True


def test_mode_gate_allows_post_live_decisions() -> None:
    """Decisions after the live-mode switch ARE emitted."""
    store = InMemoryRecordStore()
    store.write(make_record(
        type="MODE", trace_id="", span_id="", customer_id="",
        policy_name="p", policy_version="v1", policy_hash="h1",
        payload={"mode": "live", "switched_at": "2026-10-15T10:00:00Z"},
    ))
    store.write(_bill_decision(decided_at="2026-10-15T12:00:00Z"))
    emitter = StripeEmitter(store=store, mode="live", stripe_key="sk_test_xxx", meter="p")
    with patch("requests.post", return_value=_mock_stripe_ok()):
        emitter.emit()
    emissions = store.list(type="EMISSION")
    assert len(emissions) == 1
    assert emissions[0].payload["shadow"] is False
    assert emissions[0].payload["status"] == "sent"
