from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tally.feed.file import FileEvalSource
from tally.log.memory import InMemoryRecordStore
from tally.policy.declaration import make_policy
from tally.worker import run_once

FIXTURE = Path(__file__).parent.parent / "feed" / "fixtures" / "feed_fixture.json"


def translate(evals: object) -> object:
    return bill(units=1)  # type: ignore[name-defined]  # noqa: F821


def test_shadow_run_produces_decisions_and_emissions_no_stripe() -> None:
    """Full shadow-mode cycle: discover → poll → decide → emit, zero Stripe calls."""
    policy = make_policy(
        "fix-landed",
        "post_review_comment",
        "customer_id",
        ["fix_landed"],
        translate,
    )

    source = FileEvalSource(FIXTURE)
    store = InMemoryRecordStore()
    cursor = datetime(2024, 1, 1, tzinfo=timezone.utc)

    with patch("requests.post") as mock_post:
        run_once(
            policies=[policy],
            source=source,
            store=store,
            cursor=cursor,
            stripe_key=None,
            mode="shadow",
        )

    mock_post.assert_not_called()

    decisions = store.list(type="DECISION")
    emissions = store.list(type="EMISSION")

    assert len(decisions) > 0, "expected at least one DECISION"
    assert len(emissions) > 0, "expected at least one shadow EMISSION"
    assert all(d.payload["decision"] in ("BILL", "NO_CHARGE", "HOLD", "ESCALATE")
               for d in decisions)
    assert all(e.payload["shadow"] is True for e in emissions)
