from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable


@dataclass(frozen=True)
class BillResult:
    units: int


@dataclass(frozen=True)
class NoChargeResult:
    reason: str


@dataclass(frozen=True)
class HoldResult:
    reason: str


@dataclass(frozen=True)
class Policy:
    name: str
    span: str
    customer: str | Callable[[dict[str, object]], str]
    evals: list[str]
    watch: list[str]
    translate: str  # source text of the translate function
    version: str  # SHA-256 hex
    meter: str
    wait: timedelta


def compute_version(
    name: str,
    span: str,
    evals: list[str],
    watch: list[str],
    translate_source: str,
) -> str:
    """Return SHA-256 hex of the canonical policy declaration fields."""
    payload = json.dumps(
        {
            "name": name,
            "span": span,
            "evals": evals,
            "watch": watch,
            "translate": translate_source,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def make_policy(
    name: str,
    span: str,
    customer: str | Callable[[dict[str, object]], str],
    evals: list[str],
    translate: Callable[..., object],
    watch: list[str] | None = None,
    wait: timedelta = timedelta(days=7),
    meter: str | None = None,
) -> Policy:
    """Build a Policy from a translate callable, extracting source and computing version."""
    _watch = watch if watch is not None else []
    source = inspect.getsource(translate)
    version = compute_version(name, span, evals, _watch, source)
    return Policy(
        name=name,
        span=span,
        customer=customer,
        evals=evals,
        watch=_watch,
        translate=source,
        version=version,
        meter=meter if meter is not None else name,
        wait=wait,
    )
