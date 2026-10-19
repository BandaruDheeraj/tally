from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

import requests

from tally.log.records import Record, RecordType, make_record
from tally.log.store import RecordStore

_LIST_RECORDS_QUERY = """
query ListRecords($projectName: String!, $filterCondition: String!, $first: Int!) {
    project(name: $projectName) {
        spans(first: $first, filterCondition: $filterCondition) {
            edges { node { name attributes } }
        }
    }
}
"""

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _pad_trace_id(s: str) -> str:
    """Zero-pad or fill to 32 hex chars for OTLP."""
    if not s:
        return secrets.token_hex(16)
    return s.replace("-", "").ljust(32, "0")[:32]


def _pad_span_id(s: str) -> str:
    """Zero-pad or fill to 16 hex chars for OTLP."""
    if not s:
        return secrets.token_hex(8)
    return s.replace("-", "").ljust(16, "0")[:16]


class AXRecordStore(RecordStore):
    """RecordStore that writes via OTLP and reads via AX GraphQL."""

    def __init__(
        self,
        api_key: str,
        space_id: str,
        project: str,
        base_url: str = "https://otlp.arizeai.com",
        gql_url: str = "https://app.arizeai.com/graphql",
    ) -> None:
        self._project = project
        self._otlp_url = f"{base_url.rstrip('/')}/v1/traces"
        self._gql_url = gql_url
        self._headers = {
            "x-api-key": api_key,
            "x-space-id": space_id,
            "content-type": "application/json",
        }

    def write(self, record: Record) -> None:
        payload = {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [
                        {
                            "scope": {"name": "tally"},
                            "spans": [
                                {
                                    "traceId": _pad_trace_id(record.trace_id),
                                    "spanId": _pad_span_id(record.span_id),
                                    "name": record.type,
                                    "kind": 1,
                                    "startTimeUnixNano": 0,
                                    "endTimeUnixNano": 0,
                                    "attributes": [
                                        {
                                            "key": "tally.record",
                                            "value": {
                                                "stringValue": json.dumps({
                                                    "type": record.type,
                                                    "trace_id": record.trace_id,
                                                    "span_id": record.span_id,
                                                    "customer_id": record.customer_id,
                                                    "policy_name": record.policy_name,
                                                    "policy_version": record.policy_version,
                                                    "policy_hash": record.policy_hash,
                                                    "payload": record.payload,
                                                    "content_hash": record.content_hash,
                                                })
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        resp = requests.post(self._otlp_url, headers=self._headers, json=payload, timeout=30)
        resp.raise_for_status()

    def list(
        self,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
        type: RecordType | None = None,
    ) -> list[Record]:
        parts: list[str] = [f"name == '{type}'" if type else "name != 'NONE'"]
        if trace_id:
            parts.append(f"attributes['tally.trace_id'] == '{trace_id}'")
        if span_id:
            parts.append(f"attributes['tally.span_id'] == '{span_id}'")
        filter_cond = " and ".join(parts)
        resp = requests.post(
            self._gql_url,
            headers=self._headers,
            json={
                "query": _LIST_RECORDS_QUERY,
                "variables": {
                    "projectName": self._project,
                    "filterCondition": filter_cond,
                    "first": 1000,
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        records: list[Record] = []
        for edge in data["data"]["project"]["spans"]["edges"]:
            node = edge["node"]
            attrs: dict[str, object] = (
                json.loads(node["attributes"])
                if isinstance(node["attributes"], str)
                else node["attributes"]
            )
            raw_record_str = str(attrs.get("tally.record", "{}"))
            raw: dict[str, Any] = json.loads(raw_record_str)
            r = make_record(
                type=raw["type"],
                trace_id=raw["trace_id"],
                span_id=raw["span_id"],
                customer_id=raw["customer_id"],
                policy_name=raw["policy_name"],
                policy_version=raw["policy_version"],
                policy_hash=raw["policy_hash"],
                payload=raw["payload"],
            )
            if r.content_hash == raw.get("content_hash"):
                records.append(r)
        return records

    def latest_cursor(self, policy_name: str) -> datetime:
        records = self.list(type="DECISION")
        timestamps = [
            _parse_dt(str(r.payload["decided_at"]))
            for r in records
            if r.policy_name == policy_name and "decided_at" in r.payload
        ]
        return max(timestamps) if timestamps else _EPOCH

    @classmethod
    def from_env(cls) -> AXRecordStore:
        arize_project = os.environ["ARIZE_PROJECT"]
        return cls(
            api_key=os.environ["ARIZE_API_KEY"],
            space_id=os.environ["ARIZE_SPACE_ID"],
            project=os.environ.get("TALLY_PROJECT", f"{arize_project}-tally"),
        )
