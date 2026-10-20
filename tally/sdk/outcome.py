from __future__ import annotations

import functools
import logging
from types import TracebackType
from typing import Any, Callable

logger = logging.getLogger(__name__)


class outcome:
    """Decorator and context manager that ensures the wrapped code never raises.

    Used as: @tally.outcome("span_name", customer="attr_name")
    Or:      with tally.outcome("span_name", customer="attr_name"): ...
    """

    def __init__(self, span: str, *, customer: str) -> None:
        self._span = span

    def __call__(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.exception(
                    "outcome swallowed exception span=%s", self._span
                )
                return None
        return wrapper

    def __enter__(self) -> outcome:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None and issubclass(exc_type, Exception):
            logger.exception(
                "outcome swallowed exception span=%s", self._span
            )
            return True
        return False
