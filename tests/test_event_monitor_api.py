from fastapi.testclient import TestClient

from ai_platform.central_brain.event_bus.event_monitor_api import app


client = TestClient(app)


def test_event_monitor_health():
    r = client.get("/health")

    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_recent_events_endpoint():
    r = client.get("/events/recent")

    assert r.status_code == 200
    assert isinstance(r.json(), list)
