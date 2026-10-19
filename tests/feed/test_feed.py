from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from tally.feed.ax import AXEvalSource
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


# ---------------------------------------------------------------------------
# AXEvalSource tests
# ---------------------------------------------------------------------------


def _ax_list_spans_response(spans: list[dict[str, object]]) -> dict[str, object]:
    """Build the GraphQL JSON response shape for list_new_spans."""
    return {
        "data": {
            "project": {
                "spans": {
                    "edges": [
                        {
                            "node": {
                                "context": {
                                    "traceId": s["trace_id"],
                                    "spanId": s["span_id"],
                                },
                                "name": s["span_name"],
                                "attributes": json.dumps(s["attributes"]),
                                "endTime": s["ended_at"],
                            }
                        }
                        for s in spans
                    ]
                }
            }
        }
    }


def _ax_get_evals_response(annotations: list[dict[str, object]]) -> dict[str, object]:
    """Build the GraphQL JSON response shape for get_evals."""
    return {
        "data": {
            "project": {
                "spans": {
                    "edges": [
                        {
                            "node": {
                                "spanAnnotations": annotations
                            }
                        }
                    ]
                }
            }
        }
    }


def _ax_empty_spans_response() -> dict[str, object]:
    return {"data": {"project": {"spans": {"edges": []}}}}


def _mock_ax(return_value: dict[str, object]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = return_value
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def test_ax_list_spans_returns_span_info() -> None:
    src = AXEvalSource("key", "space", "proj")
    raw_spans = [
        {
            "trace_id": "t1", "span_id": "s1",
            "span_name": "post_review_comment",
            "attributes": {"customer_id": "cust-1"},
            "ended_at": "2024-06-01T10:00:00Z",
        }
    ]
    with patch("requests.post", return_value=_mock_ax(_ax_list_spans_response(raw_spans))):
        spans = src.list_new_spans(_policy(), cursor=CURSOR_BEFORE)
    assert len(spans) == 1
    assert spans[0].trace_id == "t1"
    assert spans[0].customer_id == "cust-1"


def test_ax_get_evals_returns_eval_result() -> None:
    src = AXEvalSource("key", "space", "proj")
    annotations: list[dict[str, object]] = [
        {
            "name": "fix_landed",
            "label": "yes",
            "score": 1.0,
            "explanation": "The fix was applied",
            "annotatorKind": "gpt-4",
            "createdAt": "2024-06-01T11:00:00Z",
        }
    ]
    with patch("requests.post", return_value=_mock_ax(_ax_get_evals_response(annotations))):
        evals = src.get_evals("t1", "s1")
    assert len(evals) == 1
    e = evals[0]
    assert e.name == "fix_landed"
    assert e.label == "yes"
    assert e.evaluator_version == "gpt-4"
    assert e.agreement is None  # single annotation → None


def test_ax_get_evals_unanimous_on_matching_labels() -> None:
    src = AXEvalSource("key", "space", "proj")
    ann_base: dict[str, object] = {
        "name": "q", "label": "yes", "score": 1.0, "explanation": "",
        "createdAt": "2024-01-01T00:00:00Z",
    }
    annotations: list[dict[str, object]] = [
        {**ann_base, "annotatorKind": "llm-1"},
        {**ann_base, "annotatorKind": "llm-2"},
    ]
    with patch("requests.post", return_value=_mock_ax(_ax_get_evals_response(annotations))):
        evals = src.get_evals("t1", "s1")
    assert evals[0].agreement == "unanimous"


def test_ax_get_evals_split_on_differing_labels() -> None:
    src = AXEvalSource("key", "space", "proj")
    ann_base: dict[str, object] = {
        "name": "q", "explanation": "", "createdAt": "2024-01-01T00:00:00Z",
    }
    annotations: list[dict[str, object]] = [
        {**ann_base, "label": "yes", "score": 1.0, "annotatorKind": "llm-1"},
        {**ann_base, "label": "no",  "score": 0.0, "annotatorKind": "llm-2"},
    ]
    with patch("requests.post", return_value=_mock_ax(_ax_get_evals_response(annotations))):
        evals = src.get_evals("t1", "s1")
    assert evals[0].agreement == "split"


def test_ax_get_evals_empty_edges_returns_empty() -> None:
    src = AXEvalSource("key", "space", "proj")
    with patch("requests.post", return_value=_mock_ax(_ax_empty_spans_response())):
        evals = src.get_evals("t1", "s1")
    assert evals == []


def test_ax_get_evals_raises_on_http_error() -> None:
    src = AXEvalSource("key", "space", "proj")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("500")
    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            src.get_evals("t1", "s1")


def test_ax_parity_with_file_for_single_annotation() -> None:
    """AXEvalSource and FileEvalSource produce identical EvalResult fields for the same data."""
    file_src = FileEvalSource(FIXTURE)
    file_evals = file_src.get_evals("trace-001", "span-001")

    # Build AX mock to return the same logical data (single annotation, no agreement)
    annotations: list[dict[str, object]] = [
        {
            "name": "fix_landed",
            "label": "yes",
            "score": 1.0,
            "explanation": "The fix was applied",
            "annotatorKind": "gpt-4",
            "createdAt": "2024-06-01T11:00:00Z",
        }
    ]
    ax_src = AXEvalSource("key", "space", "proj")
    with patch("requests.post", return_value=_mock_ax(_ax_get_evals_response(annotations))):
        ax_evals = ax_src.get_evals("trace-001", "span-001")

    assert len(file_evals) == len(ax_evals) == 1
    f, a = file_evals[0], ax_evals[0]
    assert f.name == a.name
    assert f.label == a.label
    assert f.score == a.score
    assert f.explanation == a.explanation
    assert f.agreement == a.agreement  # both None — single annotation
    assert f.evaluator_version == a.evaluator_version
    assert f.scored_at == a.scored_at
