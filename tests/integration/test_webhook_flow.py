from fastapi.testclient import TestClient

from webhook_listener.server import app


client = TestClient(app)


def test_webhook_trade_creates_record_and_event():
    r = client.post(
        "/webhook/trade",
        data={
            "text": "今天关注AI板块",
            "source": "test",
        },
    )

    assert r.status_code == 200

    data = r.json()

    assert "record_id" in data
    assert data["source"] == "test"
