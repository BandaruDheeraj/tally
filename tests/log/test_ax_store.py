from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from tally.log.ax import AXRecordStore
from tally.log.records import Record, make_record


def _sample_record() -> Record:
    return make_record(
        type="DECISION",
        trace_id="t1",
        span_id="s1",
        customer_id="cust-1",
        policy_name="p",
        policy_version="v1",
        policy_hash="h1",
        payload={"decision": "BILL", "units": 1, "decided_at": "2024-06-01T12:00:00Z"},
    )


def _mock_ok() -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {}
    return m


def _list_response(records: list[Record]) -> dict[str, object]:
    """GraphQL response shape for listing records."""
    return {
        "data": {
            "project": {
                "spans": {
                    "edges": [
                        {
                            "node": {
                                "name": r.type,
                                "attributes": json.dumps({
                                    "tally.record": json.dumps({
                                        "type": r.type,
                                        "trace_id": r.trace_id,
                                        "span_id": r.span_id,
                                        "customer_id": r.customer_id,
                                        "policy_name": r.policy_name,
                                        "policy_version": r.policy_version,
                                        "policy_hash": r.policy_hash,
                                        "payload": r.payload,
                                        "content_hash": r.content_hash,
                                    })
                                }),
                            }
                        }
                        for r in records
                    ]
                }
            }
        }
    }


def test_write_posts_to_otlp() -> None:
    store = AXRecordStore("key", "space", "tally-proj")
    with patch("requests.post", return_value=_mock_ok()) as mock_post:
        store.write(_sample_record())
    assert mock_post.called
    call_args = mock_post.call_args
    assert "/v1/traces" in str(call_args)


def test_write_raises_on_http_error() -> None:
    store = AXRecordStore("key", "space", "tally-proj")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("500")
    with patch("requests.post", return_value=mock_resp):
        with pytest.raises(requests.HTTPError):
            store.write(_sample_record())


def test_list_deserializes_records() -> None:
    store = AXRecordStore("key", "space", "tally-proj")
    r = _sample_record()
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = _list_response([r])
    with patch("requests.post", return_value=mock_resp):
        records = store.list(type="DECISION")
    assert len(records) == 1
    assert records[0].type == "DECISION"
    assert records[0].trace_id == "t1"
    assert records[0].content_hash == r.content_hash


def test_list_empty_returns_empty() -> None:
    store = AXRecordStore("key", "space", "tally-proj")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = _list_response([])
    with patch("requests.post", return_value=mock_resp):
        records = store.list(type="DECISION")
    assert records == []
