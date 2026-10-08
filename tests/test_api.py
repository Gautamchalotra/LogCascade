from fastapi.testclient import TestClient

from api.main import create_app
from src.common.schemas import Alert


class FakeEngine:
    alerts = []

    def ingest_lines(self, lines):
        return [Alert(1.0, "n1", "k", 3.0, 1.0, "warning")] if any("boom" in l for l in lines) else []

    def status(self):
        return {"last_score": 0.5, "last_threshold": 1.0}


def test_503_without_engine():
    c = TestClient(create_app())      # lifespan fails to load artifacts -> engine None
    assert c.get("/health").json()["engine_loaded"] is False
    assert c.post("/ingest", json={"lines": ["x"]}).status_code == 503


def test_ingest_with_engine():
    with TestClient(create_app(FakeEngine())) as c:
        r = c.post("/ingest", json={"lines": ["boom"]})
        assert r.status_code == 200 and r.json()["alerts"][0]["component"] == "n1"
        assert c.post("/ingest", json={"lines": []}).status_code == 422
