from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from tally.feed.ax import AXEvalSource, _parse_eval_attrs
from tally.feed.file import FileEvalSource
from tally.feed.source import poll_interval
from tally.policy.declaration import make_policy

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


def _translate(evals: object) -> object:
    return None  # never executed; only source text is used


def _policy(
    span: str = "post_review_comment",
    customer: str | object = "customer_id",
) -> object:
    return make_policy("p", span, customer, ["fix_landed"], _translate)  # type: ignore[arg-type]


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


# ---------------------------------------------------------------------------
# AXEvalSource tests  (v2 REST API)
# ---------------------------------------------------------------------------

_PROJECTS_RESPONSE: dict[str, object] = {
    "projects": [{"id": "pid-42", "name": "proj"}]
}


def _v2_spans_response(spans: list[dict[str, object]]) -> dict[str, object]:
    """Build the v2 REST /spans response shape."""
    return {"spans": spans, "pagination": {}}


def _v2_span(
    trace_id: str,
    span_id: str,
    span_name: str,
    attributes: dict[str, object],
    end_time: str,
) -> dict[str, object]:
    """Convenience constructor for a v2 span dict."""
    return {
        "name": span_name,
        "end_time": end_time,
        "context": {"trace_id": trace_id, "span_id": span_id},
        "attributes": attributes,
    }


def _mock_resp(return_value: dict[str, object]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = return_value
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def _mock_ax_source(spans: list[dict[str, object]]) -> tuple[AXEvalSource, MagicMock, MagicMock]:
    """Return (src, mock_get, mock_post) with GET→projects and POST→spans wired up."""
    src = AXEvalSource("key", "space", "proj")
    mock_get = _mock_resp(_PROJECTS_RESPONSE)
    mock_post = _mock_resp(_v2_spans_response(spans))
    return src, mock_get, mock_post


def test_ax_list_spans_returns_span_info() -> None:
    spans = [
        _v2_span("t1", "s1", "post_review_comment", {"customer_id": "cust-1"}, "2024-06-01T10:00:00Z"),
    ]
    src, mock_get, mock_post = _mock_ax_source(spans)
    with patch("requests.get", return_value=mock_get), \
         patch("requests.post", return_value=mock_post):
        result = src.list_new_spans(_policy(), cursor=CURSOR_BEFORE)
    assert len(result) == 1
    assert result[0].trace_id == "t1"
    assert result[0].customer_id == "cust-1"


def test_ax_list_spans_filters_by_cursor() -> None:
    spans = [
        _v2_span("t1", "s1", "post_review_comment", {"customer_id": "cust-1"}, "2024-06-01T10:00:00Z"),
        _v2_span("t2", "s2", "post_review_comment", {"customer_id": "cust-2"}, "2024-06-01T11:00:00Z"),
    ]
    src, mock_get, mock_post = _mock_ax_source(spans)
    with patch("requests.get", return_value=mock_get), \
         patch("requests.post", return_value=mock_post):
        result = src.list_new_spans(_policy(), cursor=CURSOR_BETWEEN)
    assert len(result) == 1
    assert result[0].trace_id == "t2"


def test_ax_list_spans_skips_span_missing_customer_attr() -> None:
    spans = [
        _v2_span("t1", "s1", "post_review_comment", {}, "2024-06-01T10:00:00Z"),
    ]
    src, mock_get, mock_post = _mock_ax_source(spans)
    with patch("requests.get", return_value=mock_get), \
         patch("requests.post", return_value=mock_post):
        result = src.list_new_spans(_policy(customer="customer_id"), cursor=CURSOR_BEFORE)
    assert result == []


def test_ax_get_evals_returns_eval_result() -> None:
    spans = [
        _v2_span(
            "t1", "s1", "some_span",
            {"eval.fix_landed.label": "yes", "eval.fix_landed.score": 1.0},
            "2024-06-01T11:00:00Z",
        ),
    ]
    src, mock_get, mock_post = _mock_ax_source(spans)
    with patch("requests.get", return_value=mock_get), \
         patch("requests.post", return_value=mock_post):
        evals = src.get_evals("t1", "s1")
    assert len(evals) == 1
    e = evals[0]
    assert e.name == "fix_landed"
    assert e.label == "yes"
    assert e.score == 1.0
    assert e.agreement is None  # attrs-based evals have no agreement concept


def test_ax_get_evals_empty_returns_empty() -> None:
    src, mock_get, mock_post = _mock_ax_source([])
    with patch("requests.get", return_value=mock_get), \
         patch("requests.post", return_value=mock_post):
        evals = src.get_evals("t1", "s1")
    assert evals == []


def test_ax_get_evals_raises_on_http_error() -> None:
    src = AXEvalSource("key", "space", "proj")
    mock_get = _mock_resp(_PROJECTS_RESPONSE)
    mock_post = MagicMock()
    mock_post.raise_for_status.side_effect = requests.HTTPError("500")
    with patch("requests.get", return_value=mock_get), \
         patch("requests.post", return_value=mock_post):
        with pytest.raises(requests.HTTPError):
            src.get_evals("t1", "s1")


def test_ax_spans_cache_avoids_extra_requests() -> None:
    """Second call within 60 s must not re-issue HTTP requests."""
    spans = [
        _v2_span("t1", "s1", "post_review_comment", {"customer_id": "cust-1"}, "2024-06-01T10:00:00Z"),
    ]
    src, mock_get, mock_post = _mock_ax_source(spans)
    with patch("requests.get", return_value=mock_get) as patched_get, \
         patch("requests.post", return_value=mock_post) as patched_post:
        src.list_new_spans(_policy(), cursor=CURSOR_BEFORE)
        src.list_new_spans(_policy(), cursor=CURSOR_BEFORE)
    # GET /projects and POST /spans should each be called only once (cache hit on second call)
    assert patched_get.call_count == 1
    assert patched_post.call_count == 1


# ---------------------------------------------------------------------------
# _parse_eval_attrs unit tests
# ---------------------------------------------------------------------------

def test_parse_eval_attrs_extracts_label_and_score() -> None:
    attrs = {"eval.deal_quality.label": "good", "eval.deal_quality.score": 0.85}
    results = _parse_eval_attrs(attrs, "2024-06-01T10:00:00Z")
    assert len(results) == 1
    r = results[0]
    assert r.name == "deal_quality"
    assert r.label == "good"
    assert r.score == 0.85


def test_parse_eval_attrs_multiple_evals() -> None:
    attrs = {
        "eval.a.label": "yes",
        "eval.a.score": 1.0,
        "eval.b.label": "no",
        "eval.b.score": 0.0,
    }
    results = _parse_eval_attrs(attrs, "2024-06-01T10:00:00Z")
    names = {r.name for r in results}
    assert names == {"a", "b"}


def test_parse_eval_attrs_no_evals_returns_empty() -> None:
    attrs = {"user.id": "u1", "llm.model": "gpt-4"}
    assert _parse_eval_attrs(attrs, "2024-06-01T10:00:00Z") == []


def test_parse_eval_attrs_label_only() -> None:
    attrs = {"eval.q.label": "good"}
    results = _parse_eval_attrs(attrs, "2024-06-01T10:00:00Z")
    assert len(results) == 1
    assert results[0].label == "good"
    assert results[0].score is None


def test_parse_eval_attrs_score_only() -> None:
    attrs = {"eval.q.score": 0.5}
    results = _parse_eval_attrs(attrs, "2024-06-01T10:00:00Z")
    assert len(results) == 1
    assert results[0].label is None
    assert results[0].score == 0.5
