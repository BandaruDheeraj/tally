from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tally.feed.source import (
    EvalResult,
    EvalSource,
    SpanInfo,
    _extract_customer,
    _parse_dt,
    _parse_eval_dict,
)
from tally.policy.declaration import Policy


class FileEvalSource(EvalSource):
    """EvalSource backed by a JSON fixture file. Used for tests and demo."""

    def __init__(self, fixture_path: str | Path) -> None:
        with open(fixture_path, encoding="utf-8") as f:
            data: dict[str, object] = json.load(f)
        self._spans: list[dict[str, object]] = data["spans"]  # type: ignore[assignment]
        self._evals: dict[str, list[dict[str, object]]] = data["evals"]  # type: ignore[assignment]

    def list_new_spans(self, policy: Policy, cursor: datetime) -> list[SpanInfo]:
        results: list[SpanInfo] = []
        for s in self._spans:
            if s["span_name"] != policy.span:
                continue
            ended_at = _parse_dt(str(s["ended_at"]))
            if ended_at <= cursor:
                continue
            attrs: dict[str, object] = s["attributes"]  # type: ignore[assignment]
            results.append(
                SpanInfo(
                    trace_id=str(s["trace_id"]),
                    span_id=str(s["span_id"]),
                    customer_id=_extract_customer(policy.customer, attrs),
                    span_name=str(s["span_name"]),
                    ended_at=ended_at,
                )
            )
        return results

    def get_evals(self, trace_id: str, span_id: str) -> list[EvalResult]:
        key = f"{trace_id}:{span_id}"
        return [_parse_eval_dict(e) for e in self._evals.get(key, [])]
