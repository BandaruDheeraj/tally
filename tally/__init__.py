from __future__ import annotations

from datetime import timedelta as _timedelta
from typing import Callable as _Callable

from tally.policy.declaration import BillResult as _BillResult
from tally.policy.declaration import HoldResult as _HoldResult
from tally.policy.declaration import NoChargeResult as _NoChargeResult
from tally.policy.declaration import Policy as _Policy
from tally.policy.declaration import make_policy as _make_policy
from tally.sdk.explain import explain as explain
from tally.sdk.outcome import outcome as outcome
from tally.sdk.registry import get_registry, register
from tally.sdk.verify import VerificationFailure as VerificationFailure
from tally.sdk.verify import verify as verify
from tally.worker import run as run
from tally.worker import run_once as run_once


def bill(units: int) -> _BillResult:
    return _BillResult(units=units)


def no_charge(reason: str) -> _NoChargeResult:
    return _NoChargeResult(reason=reason)


def hold(reason: str) -> _HoldResult:
    return _HoldResult(reason=reason)


def policy(
    name: str,
    span: str,
    customer: str | _Callable[..., str],
    evals: list[str],
    translate: _Callable[..., object],
    watch: list[str] | None = None,
    wait: _timedelta = _timedelta(days=7),
    meter: str | None = None,
) -> _Policy:
    p = _make_policy(name, span, customer, evals, translate,
                     watch=watch, wait=wait, meter=meter)
    register(p)
    return p


__all__ = [
    "bill", "no_charge", "hold",
    "explain", "verify", "VerificationFailure",
    "outcome",
    "policy", "get_registry",
    "run", "run_once",
]
