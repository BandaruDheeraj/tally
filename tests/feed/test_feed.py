from __future__ import annotations

from datetime import timedelta

from tally.feed.source import poll_interval


def test_poll_interval_n0_is_60s() -> None:
    assert poll_interval(0) == timedelta(seconds=60)


def test_poll_interval_n1_is_120s() -> None:
    assert poll_interval(1) == timedelta(seconds=120)


def test_poll_interval_n2_is_240s() -> None:
    assert poll_interval(2) == timedelta(seconds=240)


def test_poll_interval_cap_at_30min() -> None:
    assert poll_interval(100) == timedelta(minutes=30)


def test_poll_interval_custom_base_and_cap() -> None:
    result = poll_interval(3, base=timedelta(seconds=10), cap=timedelta(seconds=50))
    assert result == timedelta(seconds=50)  # 10 * 2^3 = 80, capped at 50
