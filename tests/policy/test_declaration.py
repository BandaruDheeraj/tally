import hashlib
import inspect
import json
from datetime import timedelta

import pytest

from tally.policy.declaration import (
    BillResult,
    HoldResult,
    NoChargeResult,
    make_policy,
)

# ── result type construction ──────────────────────────────────────────────────


def test_bill_result() -> None:
    r = BillResult(units=5)
    assert r.units == 5


def test_no_charge_result() -> None:
    r = NoChargeResult(reason="free tier")
    assert r.reason == "free tier"


def test_hold_result() -> None:
    r = HoldResult(reason="needs review")
    assert r.reason == "needs review"


def test_results_are_frozen() -> None:
    r = BillResult(units=1)
    with pytest.raises(Exception):
        r.units = 2  # type: ignore[misc]


# ── make_policy fields ────────────────────────────────────────────────────────


def _translate_bill(evals):  # type: ignore[no-untyped-def]
    return bill(units=1)  # noqa: F821


def _translate_no_charge(evals):  # type: ignore[no-untyped-def]
    return no_charge(reason="free")  # noqa: F821


def test_make_policy_basic_fields() -> None:
    p = make_policy(
        name="review_comment",
        span="post_review_comment",
        customer="customer_id",
        evals=["fix_landed"],
        translate=_translate_bill,
    )
    assert p.name == "review_comment"
    assert p.span == "post_review_comment"
    assert p.customer == "customer_id"
    assert p.evals == ["fix_landed"]
    assert p.watch == []
    assert p.meter == "review_comment"  # defaults to name
    assert p.wait == timedelta(days=7)
    assert isinstance(p.translate, str)
    assert len(p.version) == 64  # SHA-256 hex digest


def test_make_policy_meter_defaults_to_name() -> None:
    p = make_policy("my_meter", "s", "c", ["e"], _translate_bill)
    assert p.meter == "my_meter"


def test_make_policy_meter_override() -> None:
    p = make_policy("n", "s", "c", ["e"], _translate_bill, meter="custom_meter")
    assert p.meter == "custom_meter"


def test_make_policy_stores_translate_source() -> None:
    p = make_policy("n", "s", "c", ["e"], _translate_bill)
    source = inspect.getsource(_translate_bill)
    assert p.translate == source


def test_make_policy_customer_callable() -> None:
    def get_customer(attrs: dict[str, str]) -> str:
        return attrs["tenant_id"]

    p = make_policy("n", "s", get_customer, ["e"], _translate_bill)
    assert callable(p.customer)


def test_policy_is_frozen() -> None:
    p = make_policy("n", "s", "c", ["e"], _translate_bill)
    with pytest.raises(Exception):
        p.name = "other"  # type: ignore[misc]


# ── version hashing ───────────────────────────────────────────────────────────


def test_version_deterministic() -> None:
    p1 = make_policy("n", "s", "c", ["e"], _translate_bill)
    p2 = make_policy("n", "s", "c", ["e"], _translate_bill)
    assert p1.version == p2.version


def test_version_matches_manual_hash() -> None:
    source = inspect.getsource(_translate_bill)
    expected = hashlib.sha256(
        json.dumps(
            {"name": "n", "span": "s", "evals": ["e"], "watch": [], "translate": source},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    p = make_policy("n", "s", "c", ["e"], _translate_bill)
    assert p.version == expected


def test_version_changes_on_name() -> None:
    p1 = make_policy("n1", "s", "c", ["e"], _translate_bill)
    p2 = make_policy("n2", "s", "c", ["e"], _translate_bill)
    assert p1.version != p2.version


def test_version_changes_on_span() -> None:
    p1 = make_policy("n", "s1", "c", ["e"], _translate_bill)
    p2 = make_policy("n", "s2", "c", ["e"], _translate_bill)
    assert p1.version != p2.version


def test_version_changes_on_evals() -> None:
    p1 = make_policy("n", "s", "c", ["e1"], _translate_bill)
    p2 = make_policy("n", "s", "c", ["e2"], _translate_bill)
    assert p1.version != p2.version


def test_version_changes_on_watch() -> None:
    p1 = make_policy("n", "s", "c", ["e"], _translate_bill, watch=[])
    p2 = make_policy("n", "s", "c", ["e"], _translate_bill, watch=["sentiment"])
    assert p1.version != p2.version


def test_version_changes_on_translate_body() -> None:
    p1 = make_policy("n", "s", "c", ["e"], _translate_bill)
    p2 = make_policy("n", "s", "c", ["e"], _translate_no_charge)
    assert p1.version != p2.version
