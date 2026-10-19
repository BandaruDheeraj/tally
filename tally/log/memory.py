from __future__ import annotations

from datetime import datetime, timezone

from tally.log.records import Record, RecordType
from tally.log.store import RecordStore

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


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
        result = self._records
        if trace_id is not None:
            result = [r for r in result if r.trace_id == trace_id]
        if span_id is not None:
            result = [r for r in result if r.span_id == span_id]
        if type is not None:
            result = [r for r in result if r.type == type]
        return result

    def latest_cursor(self, policy_name: str) -> datetime:
        decisions = [
            r for r in self._records
            if r.type == "DECISION" and r.policy_name == policy_name
        ]
        if not decisions:
            return _EPOCH
        timestamps = [
            _parse_dt(str(r.payload["decided_at"]))
            for r in decisions
            if "decided_at" in r.payload
        ]
        return max(timestamps) if timestamps else _EPOCH
