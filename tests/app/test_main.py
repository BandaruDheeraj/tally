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
        # Make the first get_evals call block forever so we only see stages 1-4
        mock_source = MagicMock()
        mock_cls.return_value = mock_source
        mock_source.get_evals.return_value = []  # no evals → loop runs out

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
