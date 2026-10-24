from fastapi.testclient import TestClient
from relay.app import app

def test_oversized_payload_rejected_before_parse():
    c=TestClient(app)
    assert c.post('/twilio/sms',content=b'x'*400001).status_code==413
    assert c.post('/twilio/sms',content=b'x',headers={'Content-Length':'400001'}).status_code==413

def test_private_responses_not_cached_or_framed():
    r=TestClient(app).get('/health')
    assert r.headers['cache-control']=='no-store'
    assert r.headers['x-frame-options']=='DENY'
