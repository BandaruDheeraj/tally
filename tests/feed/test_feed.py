from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tally.feed.file import FileEvalSource
from tally.feed.source import poll_interval
from tally.policy.declaration import Policy, compute_version

FIXTURE = Path(__file__).parent / "fixtures" / "feed_fixture.json"


def test_poll_interval_n0_is_60s() -> None:
    assert poll_interval(0) == timedelta(seconds=60)


def test_poll_interval_n1_is_120s() -> None:
    assert poll_interval(1) == timedelta(seconds=120)


def test_poll_interval_n2_is_240s() -> None:
    assert poll_interval(2) == timedelta(seconds=240)


def test_poll_interval_cap_at_30min() -> None:
    assert poll_interval(100) == timedelta(minutes=30)


def test_poll_interval_custom_base_and_cap() -> None:
    result = poll_interval(3, base=timedelta(seconds=10), cap=timedelta(seconds=50))
    assert result == timedelta(seconds=50)  # 10 * 2^3 = 80, capped at 50


def _policy(span: str = "post_review_comment", customer: str = "customer_id") -> Policy:
    src = "def translate(evals):\n    return bill(units=1)\n"
    version = compute_version("p", span, ["fix_landed"], [], src)
    return Policy(
        name="p", span=span, customer=customer,
        evals=["fix_landed"], watch=[],
        translate=src, version=version, meter="p",
        wait=timedelta(days=7),
    )


CURSOR_BEFORE = datetime(2024, 6, 1, 9, 0, tzinfo=timezone.utc)
CURSOR_BETWEEN = datetime(2024, 6, 1, 10, 30, tzinfo=timezone.utc)
CURSOR_AFTER_ALL = datetime(2024, 6, 1, 23, 0, tzinfo=timezone.utc)


def test_file_list_spans_returns_matching_span_name() -> None:
    src = FileEvalSource(FIXTURE)
    spans = src.list_new_spans(_policy(), cursor=CURSOR_BEFORE)
    names = {s.span_name for s in spans}
    assert names == {"post_review_comment"}


def test_file_list_spans_cursor_filters_old_spans() -> None:
    src = FileEvalSource(FIXTURE)
    spans = src.list_new_spans(_policy(), cursor=CURSOR_BETWEEN)
    assert len(spans) == 1
    assert spans[0].trace_id == "trace-002"


def test_file_list_spans_cursor_after_all_returns_empty() -> None:
    src = FileEvalSource(FIXTURE)
    spans = src.list_new_spans(_policy(), cursor=CURSOR_AFTER_ALL)
    assert spans == []


def test_file_list_spans_extracts_customer_id() -> None:
    src = FileEvalSource(FIXTURE)
    spans = src.list_new_spans(_policy(customer="customer_id"), cursor=CURSOR_BEFORE)
    customer_ids = {s.customer_id for s in spans}
    assert customer_ids == {"cust-1", "cust-2"}


def test_file_list_spans_callable_customer() -> None:
    src = FileEvalSource(FIXTURE)
    policy = _policy(customer=lambda attrs: str(attrs["customer_id"]) + "-x")
    spans = src.list_new_spans(policy, cursor=CURSOR_BEFORE)
    assert all(s.customer_id.endswith("-x") for s in spans)


def test_file_get_evals_returns_correct_fields() -> None:
    src = FileEvalSource(FIXTURE)
    evals = src.get_evals("trace-001", "span-001")
    assert len(evals) == 1
    e = evals[0]
    assert e.name == "fix_landed"
    assert e.label == "yes"
    assert e.score == 1.0
    assert e.evaluator_version == "gpt-4"


def test_file_get_evals_unknown_span_returns_empty() -> None:
    src = FileEvalSource(FIXTURE)
    assert src.get_evals("no-such-trace", "no-such-span") == []
