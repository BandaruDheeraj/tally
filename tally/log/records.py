from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

RecordType = Literal["POLICY", "MODE", "DECISION", "EMISSION", "RULING"]


@dataclass(frozen=True)
class Record:
    type: RecordType
    trace_id: str
    span_id: str
    customer_id: str
    policy_name: str
    policy_version: str
    policy_hash: str
    payload: dict[str, object]
    content_hash: str  # SHA-256(json.dumps(all fields except content_hash, sort_keys=True))


def compute_content_hash(
    *,
    type: RecordType,
    trace_id: str,
    span_id: str,
    customer_id: str,
    policy_name: str,
    policy_version: str,
    policy_hash: str,
    payload: dict[str, object],
) -> str:
    data: dict[str, object] = {
        "type": type,
        "trace_id": trace_id,
        "span_id": span_id,
        "customer_id": customer_id,
        "policy_name": policy_name,
        "policy_version": policy_version,
        "policy_hash": policy_hash,
        "payload": payload,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def make_record(
    *,
    type: RecordType,
    trace_id: str,
    span_id: str,
    customer_id: str,
    policy_name: str,
    policy_version: str,
    policy_hash: str,
    payload: dict[str, object],
) -> Record:
    content_hash = compute_content_hash(
        type=type,
        trace_id=trace_id,
        span_id=span_id,
        customer_id=customer_id,
        policy_name=policy_name,
        policy_version=policy_version,
        policy_hash=policy_hash,
        payload=payload,
    )
    return Record(
        type=type,
        trace_id=trace_id,
        span_id=span_id,
        customer_id=customer_id,
        policy_name=policy_name,
        policy_version=policy_version,
        policy_hash=policy_hash,
        payload=payload,
        content_hash=content_hash,
    )
