from __future__ import annotations

import json
import os
from datetime import datetime
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

_LIST_SPANS_QUERY = """
query ListSpans($projectName: String!, $filterCondition: String!, $first: Int!) {
    project(name: $projectName) {
        spans(first: $first, filterCondition: $filterCondition) {
            edges { node {
                context { traceId spanId }
                name attributes endTime
            }}
        }
    }
}
"""

_GET_EVALS_QUERY = """
query GetEvals($projectName: String!, $filterCondition: String!) {
    project(name: $projectName) {
        spans(first: 1, filterCondition: $filterCondition) {
            edges { node {
                spanAnnotations {
                    name label score explanation annotatorKind createdAt
                }
            }}
        }
    }
}
"""


def _parse_annotations(annotations: list[dict[str, Any]]) -> list[EvalResult]:
    """Group AX span annotations by name and compute agreement."""
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
    """EvalSource that reads from Arize AX (Phoenix) via GraphQL."""

    def __init__(
        self,
        api_key: str,
        space_id: str,
        project: str,
        base_url: str = "https://app.arizeai.com",
    ) -> None:
        self._project = project
        self._gql_url = f"{base_url.rstrip('/')}/graphql"
        self._headers = {
            "x-api-key": api_key,
            "x-space-id": space_id,
            "content-type": "application/json",
        }

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(
            self._gql_url,
            headers=self._headers,
            json={"query": query, "variables": variables},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def list_new_spans(self, policy: Policy, cursor: datetime) -> list[SpanInfo]:
        filter_cond = f"name == '{policy.span}' and end_time > '{cursor.isoformat()}'"
        data = self._graphql(
            _LIST_SPANS_QUERY,
            {"projectName": self._project, "filterCondition": filter_cond, "first": 100},
        )
        results: list[SpanInfo] = []
        for edge in data["data"]["project"]["spans"]["edges"]:
            node = edge["node"]
            attrs: dict[str, object] = (
                json.loads(node["attributes"])
                if isinstance(node["attributes"], str)
                else node["attributes"]
            )
            results.append(
                SpanInfo(
                    trace_id=str(node["context"]["traceId"]),
                    span_id=str(node["context"]["spanId"]),
                    customer_id=_extract_customer(policy.customer, attrs),
                    span_name=str(node["name"]),
                    ended_at=_parse_dt(str(node["endTime"])),
                )
            )
        return results

    def get_evals(self, trace_id: str, span_id: str) -> list[EvalResult]:
        filter_cond = (
            f"context.trace_id == '{trace_id}' and context.span_id == '{span_id}'"
        )
        data = self._graphql(
            _GET_EVALS_QUERY,
            {"projectName": self._project, "filterCondition": filter_cond},
        )
        edges = data["data"]["project"]["spans"]["edges"]
        if not edges:
            return []
        return _parse_annotations(edges[0]["node"]["spanAnnotations"])

    @classmethod
    def from_env(cls) -> AXEvalSource:
        return cls(
            api_key=os.environ["ARIZE_API_KEY"],
            space_id=os.environ["ARIZE_SPACE_ID"],
            project=os.environ["ARIZE_PROJECT"],
        )
