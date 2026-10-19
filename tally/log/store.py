from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from tally.log.records import Record, RecordType


class RecordStore(ABC):
    @abstractmethod
    def write(self, record: Record) -> None: ...

    @abstractmethod
    def list(
        self,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
        type: RecordType | None = None,
    ) -> list[Record]: ...

    @abstractmethod
    def latest_cursor(self, policy_name: str) -> datetime: ...
