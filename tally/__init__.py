from tally.policy.declaration import BillResult as _BillResult
from tally.policy.declaration import HoldResult as _HoldResult
from tally.policy.declaration import NoChargeResult as _NoChargeResult


def bill(units: int) -> _BillResult:
    return _BillResult(units=units)


def no_charge(reason: str) -> _NoChargeResult:
    return _NoChargeResult(reason=reason)


def hold(reason: str) -> _HoldResult:
    return _HoldResult(reason=reason)


__all__ = ["bill", "no_charge", "hold"]
