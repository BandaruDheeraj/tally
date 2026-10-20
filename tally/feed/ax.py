from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

from tally.feed.source import (
    EvalResult,
    EvalSource,
    SpanInfo,
    _extract_customer,
    _parse_dt,
    _parse_eval_dict,
)
from tally.policy.declaration import Policy

_REST_BASE = "https://api.arize.com/v2"


def _parse_eval_attrs(
    attrs: dict[str, Any], end_time_str: str
) -> list[EvalResult]:
    """Parse eval.<name>.label / eval.<name>.score keys from a span's attributes dict."""
    # Collect all eval names
    names: set[str] = set()
    for key in attrs:
        if key.startswith("eval.") and (
            key.endswith(".label") or key.endswith(".score")
        ):
            # key format: eval.<name>.label  or  eval.<name>.score
            parts = key.split(".", 2)
            if len(parts) == 3:
                names.add(parts[1])

    try:
        scored_at = end_time_str or datetime.now(timezone.utc).isoformat()
    except Exception:
        scored_at = datetime.now(timezone.utc).isoformat()

    results: list[EvalResult] = []
    for name in sorted(names):
        label_key = f"eval.{name}.label"
        score_key = f"eval.{name}.score"
        label = attrs.get(label_key)
        score = attrs.get(score_key)
        results.append(
            _parse_eval_dict(
                {
                    "name": name,
                    "label": label,
                    "score": score,
                    "explanation": "",
                    "agreement": None,
                    "evaluator_version": None,
                    "scored_at": scored_at,
                    "raw": {label_key: label, score_key: score},
                }
            )
        )
    return results


def _parse_annotations(annotations: list[dict[str, Any]]) -> list[EvalResult]:
    """Group span annotations by name and compute agreement (used for annotation-style evals)."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for ann in annotations:
        by_name.setdefault(str(ann["name"]), []).append(ann)

    results: list[EvalResult] = []
    for name, anns in by_name.items():
        if len(anns) == 1:
            agreement: str | None = None
        elif len({a.get("label") for a in anns}) == 1:
            agreement = "unanimous"
        else:
            agreement = "split"
        ann = anns[0]
        results.append(
            _parse_eval_dict(
                {
                    "name": name,
                    "label": ann.get("label"),
                    "score": ann.get("score"),
                    "explanation": ann.get("explanation") or "",
                    "agreement": agreement,
                    "evaluator_version": ann.get("annotatorKind"),
                    "scored_at": ann["createdAt"],
                    "raw": ann,
                }
            )
        )
    return results


class AXEvalSource(EvalSource):
    """EvalSource that reads from Arize via the v2 REST API."""

    def __init__(
        self,
        api_key: str,
        space_id: str,
        project: str,
        rest_base: str = _REST_BASE,
    ) -> None:
        self._project = project
        self._rest_base = rest_base.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "x-space-id": space_id,
            "Content-Type": "application/json",
        }
        # Span cache: avoid N+1 API calls within a single run_once() cycle
        self._spans_cache: list[dict[str, Any]] | None = None
        self._cache_time: float = 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_project_id(self) -> str:
        """Look up the numeric/string project ID for self._project by name."""
        resp = requests.get(
            f"{self._rest_base}/projects",
            headers=self._headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        for proj in data.get("projects", []):
            if proj.get("name") == self._project:
                return str(proj["id"])
        raise KeyError(f"Project {self._project!r} not found in Arize space")

    def _all_spans(self) -> list[dict[str, Any]]:
        """Fetch all spans for the project; cached for 60 s."""
        now = time.monotonic()
        if self._spans_cache is not None and (now - self._cache_time) < 60.0:
            return self._spans_cache

        project_id = self._get_project_id()
        resp = requests.post(
            f"{self._rest_base}/spans",
            headers=self._headers,
            json={"project_id": project_id},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        spans: list[dict[str, Any]] = data.get("spans", [])
        self._spans_cache = spans
        self._cache_time = now
        return spans

    # ------------------------------------------------------------------
    # EvalSource interface
    # ------------------------------------------------------------------

    def list_new_spans(self, policy: Policy, cursor: datetime) -> list[SpanInfo]:
        results: list[SpanInfo] = []
        for span in self._all_spans():
            # Filter by span name
            if span.get("name") != policy.span:
                continue
            # Filter by end_time > cursor (client-side)
            end_time_str = span.get("end_time", "")
            try:
                ended_at = _parse_dt(str(end_time_str))
            except (ValueError, TypeError):
                continue
            if ended_at <= cursor:
                continue
            # Extract customer_id; skip span if attribute is missing
            attrs: dict[str, Any] = span.get("attributes") or {}
            ctx: dict[str, Any] = span.get("context") or {}
            try:
                customer_id = _extract_customer(policy.customer, attrs)
            except KeyError:
                continue
            results.append(
                SpanInfo(
                    trace_id=str(ctx.get("trace_id", "")),
                    span_id=str(ctx.get("span_id", "")),
                    customer_id=customer_id,
                    span_name=str(span.get("name", "")),
                    ended_at=ended_at,
                )
            )
        return results

    def get_evals(self, trace_id: str, span_id: str) -> list[EvalResult]:
        for span in self._all_spans():
            ctx: dict[str, Any] = span.get("context") or {}
            if (
                str(ctx.get("trace_id", "")) == trace_id
                and str(ctx.get("span_id", "")) == span_id
            ):
                attrs: dict[str, Any] = span.get("attributes") or {}
                end_time_str = span.get("end_time", "")
                return _parse_eval_attrs(attrs, str(end_time_str))
        return []

    @classmethod
    def from_env(cls) -> AXEvalSource:
        return cls(
            api_key=os.environ["ARIZE_API_KEY"],
            space_id=os.environ["ARIZE_SPACE_ID"],
            project=os.environ["ARIZE_PROJECT"],
        )
