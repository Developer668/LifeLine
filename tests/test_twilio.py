import os
from urllib.parse import urlencode
from twilio.request_validator import RequestValidator
from fastapi.testclient import TestClient
from relay.app import app

client = TestClient(app)
BASE = "http://127.0.0.1:8000"
TOKEN = "test-twilio-secret"
WORKER = {"Authorization": "Bearer " + os.environ.get("RELAY_WORKER_TOKEN", "a-separate-worker-token-for-testing")}
os.environ["TWILIO_AUTH_TOKEN"] = TOKEN


def post_signed(path, form):
    url = BASE + path
    signature = RequestValidator(TOKEN).compute_signature(url, form)
    return client.post(path, data=form, headers={"X-Twilio-Signature": signature})


def test_voice_accepts_valid_twilio():
    sid = "CA" + "a" * 32
    resp = post_signed("/twilio/voice", {"CallSid": sid, "From": "+15555550111"})
    assert resp.status_code == 200, resp.text
    assert "<Gather" in resp.text and "RELAY" in resp.text
    turn = post_signed("/twilio/voice/turn", {
        "CallSid": sid, "From": "+15555550111", "SpeechResult": "Check weather near 92037"})
    assert turn.status_code == 200, turn.text
    assert "<Redirect" in turn.text


def test_sms_accepts_and_deduplicates():
    f = {"MessageSid": "SM" + "b"*32, "From": "+15555550122", "Body": "Is 92037 under a weather warning?"}
    r1 = post_signed("/twilio/sms", f)
    r2 = post_signed("/twilio/sms", f)
    assert r1.status_code == r2.status_code == 200
    assert "<Response" in r1.text


def test_twilio_rejects_forgery():
    resp = client.post("/twilio/voice", data={"CallSid":"CA"+"c"*32},
                       headers={"X-Twilio-Signature":"bogus"})
    assert resp.status_code == 403
