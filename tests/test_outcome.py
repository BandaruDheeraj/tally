from __future__ import annotations

import tally


def test_outcome_decorator_returns_normally() -> None:
    @tally.outcome("post_review_comment", customer="customer_id")
    def agent_fn(x: int) -> int:
        return x * 2

    result = agent_fn(5)
    assert result == 10


def test_outcome_decorator_catches_exceptions() -> None:
    @tally.outcome("post_review_comment", customer="customer_id")
    def failing_fn() -> int:
        raise RuntimeError("boom")

    result = failing_fn()
    assert result is None  # exception swallowed, returns None


def test_outcome_context_manager() -> None:
    results: list[int] = []
    with tally.outcome("post_review_comment", customer="customer_id"):
        results.append(42)
    assert results == [42]


def test_outcome_context_manager_catches_exceptions() -> None:
    with tally.outcome("post_review_comment", customer="customer_id"):
        raise RuntimeError("boom")
    # No exception raised out of context manager
