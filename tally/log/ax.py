from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime
from typing import Any

import requests

from tally.feed.source import _EPOCH, _parse_dt
from tally.log.records import RecordType, make_record
from tally.log.store import RecordStore, Record


def _pad_trace_id(s: str) -> str:
    if not s:
        return secrets.token_hex(16)
    return s.replace("-", "").ljust(32, "0")[:32]


def _pad_span_id(s: str) -> str:
    if not s:
        return secrets.token_hex(8)
    return s.replace("-", "").ljust(16, "0")[:16]


class AXRecordStore(RecordStore):
    """RecordStore that writes via OTLP and reads via Arize AX v2 REST API."""

    def __init__(
        self,
        api_key: str,
        space_id: str,
        project: str,
        otlp_url: str = "https://otlp.arize.com/v1/traces",
        rest_base: str = "https://api.arize.com/v2",
    ) -> None:
        self._api_key = api_key
        self._space_id = space_id
        self._project = project
        self._otlp_url = otlp_url
        self._rest_base = rest_base.rstrip("/")
        self._project_id: str | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _otlp_headers(self) -> dict[str, str]:
        return {
            "api_key": self._api_key,
            "space_id": self._space_id,
            "content-type": "application/json",
        }

    @property
    def _rest_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }

    def _get_project_id(self) -> str:
        if self._project_id is not None:
            return self._project_id
        resp = requests.get(
            f"{self._rest_base}/projects",
            headers=self._rest_headers,
            timeout=30,
        )
        resp.raise_for_status()
        for project in resp.json().get("projects", []):
            if project.get("name") == self._project:
                self._project_id = str(project["id"])
                return self._project_id
        raise ValueError(f"Arize project {self._project!r} not found")

    # ------------------------------------------------------------------
    # RecordStore interface
    # ------------------------------------------------------------------

    def write(self, record: Record) -> None:
        now_ns = int(time.time() * 1_000_000_000)
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "openinference.project.name",
                                "value": {"stringValue": self._project},
                            },
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "tally"},
                            "spans": [
                                {
                                    "traceId": _pad_trace_id(record.trace_id),
                                    "spanId": _pad_span_id(record.span_id),
                                    "name": record.type,
                                    "kind": 1,
                                    "startTimeUnixNano": now_ns - 1_000_000_000,
                                    "endTimeUnixNano": now_ns,
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
        resp = requests.post(
            self._otlp_url,
            headers=self._otlp_headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()

    def list(
        self,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
        type: RecordType | None = None,
    ) -> list[Record]:
        project_id = self._get_project_id()
        resp = requests.post(
            f"{self._rest_base}/spans",
            headers=self._rest_headers,
            json={"project_id": project_id},
            timeout=30,
        )
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        spans: list[dict[str, Any]] = body.get("spans", [])

        records: list[Record] = []
        for span in spans:
            # Filter by record type (span name)
            if type is not None and span.get("name") != type:
                continue

            ctx = span.get("context", {})
            raw_tid = str(ctx.get("trace_id", ""))
            raw_sid = str(ctx.get("span_id", ""))

            if trace_id is not None and raw_tid != trace_id:
                continue
            if span_id is not None and raw_sid != span_id:
                continue

            attrs: dict[str, Any] = span.get("attributes", {})
            raw_str = str(attrs.get("tally.record", "{}"))
            try:
                raw: dict[str, Any] = json.loads(raw_str)
            except json.JSONDecodeError:
                continue

            if not raw.get("type"):
                continue

            try:
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
            except KeyError:
                continue

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
