from ai_platform.central_brain.event_bus.event_bus import EventBus


def test_legacy_event_bus_does_not_create_sidecar_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bus = EventBus()
    received = {}
    bus.subscribe("test.integration", lambda p: received.update(p))
    bus.publish("test.integration", {"value": 1})
    assert received["value"] == 1
    assert not (tmp_path / "data" / "event_bus" / "events.sqlite").exists()
