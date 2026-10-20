from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tally.feed.file import FileEvalSource
from tally.log.memory import InMemoryRecordStore
from tally.worker import run_once

FIXTURE = Path(__file__).parent / "feed" / "fixtures" / "feed_fixture.json"


def _make_source_and_store() -> tuple[object, InMemoryRecordStore]:
    source = FileEvalSource(FIXTURE)
    store = InMemoryRecordStore()
    return source, store


def translate(evals: object) -> object:
    return bill(units=1)  # type: ignore[name-defined]  # noqa: F821


def test_run_once_discovers_new_spans() -> None:
    """run_once with a FileEvalSource discovers spans and creates DECISION records."""
    from tally.policy.declaration import make_policy

    policy = make_policy(
        "p", "post_review_comment", "customer_id",
        ["fix_landed"], translate,
    )
    source, store = _make_source_and_store()
    cursor = datetime(2024, 1, 1, tzinfo=timezone.utc)
    run_once(
        policies=[policy],
        source=source,
        store=store,
        cursor=cursor,
        stripe_key=None,
        mode="shadow",
    )
    decisions = store.list(type="DECISION")
    assert len(decisions) > 0


def test_run_once_skips_already_decided_spans() -> None:
    """Second run_once does not re-decide already-decided spans."""
    from tally.policy.declaration import make_policy

    policy = make_policy(
        "p", "post_review_comment", "customer_id",
        ["fix_landed"], translate,
    )
    source, store = _make_source_and_store()
    cursor = datetime(2024, 1, 1, tzinfo=timezone.utc)
    run_once(policies=[policy], source=source, store=store,
             cursor=cursor, stripe_key=None, mode="shadow")
    first_count = len(store.list(type="DECISION"))

    # Second run — cursor advanced past fixture spans
    run_once(policies=[policy], source=source, store=store,
             cursor=datetime(2025, 1, 1, tzinfo=timezone.utc),
             stripe_key=None, mode="shadow")
    assert len(store.list(type="DECISION")) == first_count  # no new decisions


def test_run_once_shadow_mode_writes_emissions() -> None:
    """Shadow mode: EMISSION records written, no Stripe call."""
    from tally.policy.declaration import make_policy

    policy = make_policy(
        "p", "post_review_comment", "customer_id",
        ["fix_landed"], translate,
    )
    source, store = _make_source_and_store()
    cursor = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with patch("requests.post") as mock_post:
        run_once(policies=[policy], source=source, store=store,
                 cursor=cursor, stripe_key=None, mode="shadow")
    mock_post.assert_not_called()
    emissions = store.list(type="EMISSION")
    assert len(emissions) > 0
    assert all(e.payload["shadow"] is True for e in emissions)
