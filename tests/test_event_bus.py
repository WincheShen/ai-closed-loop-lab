from ai_platform.central_brain.event_bus.event_bus import EventBus


def test_event_bus_publish_and_subscribe():
    bus = EventBus()

    received = {}

    def handler(payload):
        received["data"] = payload

    bus.subscribe("test.event", handler)

    payload = {"x": 1}

    bus.publish("test.event", payload)

    assert received["data"] == payload
