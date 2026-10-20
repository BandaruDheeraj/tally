from __future__ import annotations

from datetime import datetime

from tally.feed.source import _EPOCH, _parse_dt
from tally.log.records import Record, RecordType
from tally.log.store import RecordStore


class InMemoryRecordStore(RecordStore):
    def __init__(self) -> None:
        self._records: list[Record] = []
        self._seen: set[str] = set()

    def write(self, record: Record) -> None:
        if record.content_hash in self._seen:
            return
        self._seen.add(record.content_hash)
        self._records.append(record)

    def list(
        self,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
        type: RecordType | None = None,
    ) -> list[Record]:
        return [
            r for r in self._records
            if (trace_id is None or r.trace_id == trace_id)
            and (span_id is None or r.span_id == span_id)
            and (type is None or r.type == type)
        ]

    def latest_cursor(self, policy_name: str) -> datetime:
        timestamps = [
            _parse_dt(str(r.payload["decided_at"]))
            for r in self._records
            if r.type == "DECISION"
            and r.policy_name == policy_name
            and "decided_at" in r.payload
        ]
        return max(timestamps) if timestamps else _EPOCH
