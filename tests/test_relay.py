import os
import pathlib
import tempfile

temp = tempfile.TemporaryDirectory()
os.environ["RELAY_DB"] = str(pathlib.Path(temp.name) / "relay.db")
os.environ["DEMO_TOKEN"] = "a-local-demo-token-for-testing"
os.environ["RELAY_WORKER_TOKEN"] = "a-separate-worker-token-for-testing"
os.environ["TWILIO_LIVE_SEND"] = "false"

from fastapi.testclient import TestClient
from relay.app import app
from relay import store, providers

client = TestClient(app)
DEMO = {"Authorization": "Bearer a-local-demo-token-for-testing"}
WORKER = {"Authorization": "Bearer a-separate-worker-token-for-testing"}


def test_requires_auth():
    assert client.post("/demo/message", json={"incident_id": "case_001", "text": "hello"}).status_code == 401
    assert client.post("/worker/claim").status_code == 401
    assert client.post("/twilio/sms", data={"From": "+15555550123", "Body": "hi"}).status_code == 403


def test_isolated_multisession_and_lease():
    for i in range(20):
        r = client.post("/demo/message", headers=DEMO,
           json={"incident_id": f"case_{i:03}", "text": f"request {i}"})
        assert r.status_code == 200, r.text
    observed = set()
    for i in range(20):
        r = client.post("/worker/claim", headers=WORKER).json()
        assert r["job"]
        j = r["job"]
        observed.add(j["incident"])
        assert j["payload"]["text"].startswith("request ")
        assert client.post(f"/worker/jobs/{j['id']}/complete", headers=WORKER,
             json={"lease": "invalidtokenxyz", "result": "wrong"}).status_code == 409
        assert client.post(f"/worker/jobs/{j['id']}/complete", headers=WORKER,
             json={"lease": j["lease"], "result": "confirmed text"}).status_code == 200
        assert client.post(f"/worker/jobs/{j['id']}/complete", headers=WORKER,
             json={"lease": j["lease"], "result": "duplicate"}).status_code == 409
    assert len(observed) == 20
    for i in range(20):
        state = client.get(f"/demo/incidents/case_{i:03}", headers=DEMO).json()
        assert len(state["jobs"]) == 1
        assert state["jobs"][0]["state"] == "complete"


def test_emergency_bypasses_model():
    r = client.post("/demo/message", headers=DEMO,
        json={"incident_id": "emergency_test", "text": "My friend is not breathing"})
    assert r.json()["state"] == "immediate"
    assert "911" in r.json()["response"]
    assert store.latest("emergency_test") is None


def test_media_requires_explicit_token():
    client.post("/demo/message", headers=DEMO,
        json={"incident_id": "camera_test", "text": "Hello"})
    res = client.post("/demo/incidents/camera_test/camera-link", headers=DEMO)
    assert res.status_code == 200
    assert client.get("/camera/camera_test").status_code == 422
    assert client.get("/camera/camera_test?expires=9999999999&token=x").status_code == 403


def test_sms_disabled_by_default():
    store.upsert_incident("call-test", phone="+15555550123", channel="voice")
    result = providers.send_reference("call-test", "This is only a test.", explicit_consent=True)
    assert result["state"] == "simulated"


def test_dedup():
    store.upsert_incident("dedup", channel="demo")
    a = store.enqueue("dedup", "message", {"text": "same"})
    b = store.enqueue("dedup", "message", {"text": "same"})
    assert a == b
