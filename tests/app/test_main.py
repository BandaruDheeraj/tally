from __future__ import annotations

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
