from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def test_index_returns_html() -> None:
    from demo.app.main import app
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_decisions_empty_initially() -> None:
    from demo.app.main import app, store
    store._records.clear()
    store._seen.clear()
    client = TestClient(app)
    response = client.get("/api/decisions")
    assert response.status_code == 200
    assert response.json() == []


async def _collect(gen: object) -> list[dict]:  # type: ignore[type-arg]
    """Collect all SSE data payloads from an async generator."""
    events: list[dict] = []  # type: ignore[type-arg]
    async for chunk in gen:  # type: ignore[union-attr]
        text = chunk if isinstance(chunk, str) else chunk.decode()
        for line in text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_plan_generator_stages_1_to_4() -> None:
    """Generator emits status, status, chunk, status before Arize poll."""
    mock_results = [{"title": "Cheap flights", "body": "From $299", "href": ""}]
    mock_eval = MagicMock()
    mock_eval.name = "deal_quality"
    mock_eval.label = "good"
    mock_eval.score = 0.85
    mock_eval.agreement = None
    mock_eval.explanation = ""
    mock_eval.evaluator_version = None
    from datetime import datetime, timezone
    mock_eval.scored_at = datetime.now(tz=timezone.utc)
    mock_eval.raw = {}

    with (
        patch("demo.app.main._search_deals", return_value=mock_results),
        patch(
            "demo.app.main._analyse_deals",
            return_value=("Great trip plan for Tokyo.", "good", 0.85),
        ),
        patch(
            "demo.app.main._send_span",
            return_value=("a" * 32, "b" * 16),
        ),
        patch("demo.app.main.AXEvalSource") as mock_cls,
        patch.dict(
            "os.environ",
            {"ARIZE_API_KEY": "k", "ARIZE_SPACE_ID": "s"},
        ),
    ):
        mock_source = MagicMock()
        mock_cls.return_value = mock_source
        mock_source.get_evals.return_value = [mock_eval]

        from demo.app.main import _plan_generator

        events = asyncio.run(_collect(_plan_generator("Tokyo", "alice")))

    types = [e["type"] for e in events]
    # First four events: searching, analysing, chunk, sending
    assert types[0] == "status"
    assert "Search" in events[0]["message"]
    assert types[1] == "status"
    assert "Analys" in events[1]["message"]
    chunk_events = [e for e in events if e["type"] == "chunk"]
    assert chunk_events, "expected at least one chunk event"
    assert chunk_events[0]["text"] == "Great trip plan for Tokyo."
    send_events = [e for e in events if e["type"] == "status" and "Arize" in e["message"]]
    assert send_events, "expected a 'Sending span to Arize' status event"


def test_plan_generator_bill_decision() -> None:
    """Generator emits BILL decision when deal_quality=good."""
    mock_results = [{"title": "Cheap flights", "body": "From $299", "href": ""}]
    mock_eval = MagicMock()
    mock_eval.name = "deal_quality"
    mock_eval.label = "good"
    mock_eval.score = 0.85
    mock_eval.agreement = None
    mock_eval.explanation = ""
    mock_eval.evaluator_version = None
    from datetime import datetime, timezone
    mock_eval.scored_at = datetime.now(tz=timezone.utc)
    mock_eval.raw = {}

    with (
        patch("demo.app.main._search_deals", return_value=mock_results),
        patch(
            "demo.app.main._analyse_deals",
            return_value=("Great trip plan.", "good", 0.85),
        ),
        patch("demo.app.main._send_span", return_value=("a" * 32, "b" * 16)),
        patch("demo.app.main.AXEvalSource") as mock_cls,
        patch.dict(
            "os.environ",
            {"ARIZE_API_KEY": "k", "ARIZE_SPACE_ID": "s"},
        ),
    ):
        mock_source = MagicMock()
        mock_cls.return_value = mock_source
        mock_source.get_evals.return_value = [mock_eval]

        from demo.app.main import _plan_generator, store
        store._records.clear()
        store._seen.clear()

        events = asyncio.run(_collect(_plan_generator("Tokyo", "alice")))

    types = [e["type"] for e in events]
    assert "eval" in types
    assert "decision" in types
    assert types[-1] == "done"

    decision_event = next(e for e in events if e["type"] == "decision")
    assert decision_event["outcome"] == "BILL"
    assert decision_event["units"] == 1

    assert len(store.list(type="DECISION")) == 1


def test_plan_generator_no_charge_decision() -> None:
    """Generator emits NO_CHARGE when deal_quality=poor."""
    mock_eval = MagicMock()
    mock_eval.name = "deal_quality"
    mock_eval.label = "poor"
    mock_eval.score = 0.3
    mock_eval.agreement = None
    mock_eval.explanation = ""
    mock_eval.evaluator_version = None
    from datetime import datetime, timezone
    mock_eval.scored_at = datetime.now(tz=timezone.utc)
    mock_eval.raw = {}

    with (
        patch("demo.app.main._search_deals", return_value=[]),
        patch(
            "demo.app.main._analyse_deals",
            return_value=("Vague plan.", "poor", 0.3),
        ),
        patch("demo.app.main._send_span", return_value=("c" * 32, "d" * 16)),
        patch("demo.app.main.AXEvalSource") as mock_cls,
        patch.dict(
            "os.environ",
            {"ARIZE_API_KEY": "k", "ARIZE_SPACE_ID": "s"},
        ),
    ):
        mock_source = MagicMock()
        mock_cls.return_value = mock_source
        mock_source.get_evals.return_value = [mock_eval]

        from demo.app.main import _plan_generator, store
        store._records.clear()
        store._seen.clear()

        events = asyncio.run(_collect(_plan_generator("Nowhere", "bob")))

    decision_event = next(e for e in events if e["type"] == "decision")
    assert decision_event["outcome"] == "NO_CHARGE"


def test_plan_generator_hold_on_eval_timeout() -> None:
    """Generator emits HOLD when Arize never returns evals."""
    with (
        patch("demo.app.main._search_deals", return_value=[]),
        patch(
            "demo.app.main._analyse_deals",
            return_value=("Plan.", "good", 0.8),
        ),
        patch("demo.app.main._send_span", return_value=("e" * 32, "f" * 16)),
        patch("demo.app.main.AXEvalSource") as mock_cls,
        patch("demo.app.main._POLL_ATTEMPTS", 2),  # override to 2 attempts for speed
        patch("demo.app.main._POLL_SLEEP", 0),      # no sleep in tests
        patch.dict(
            "os.environ",
            {"ARIZE_API_KEY": "k", "ARIZE_SPACE_ID": "s"},
        ),
    ):
        mock_source = MagicMock()
        mock_cls.return_value = mock_source
        mock_source.get_evals.return_value = []  # never returns evals

        from demo.app.main import _plan_generator

        events = asyncio.run(_collect(_plan_generator("Nowhere", "carol")))

    decision_event = next(e for e in events if e["type"] == "decision")
    assert decision_event["outcome"] == "HOLD"
    assert decision_event["reason"] == "eval_timeout"
