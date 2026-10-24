import time
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from relay import store
from relay import coordination as co

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('RELAY_DB',str(tmp_path/'contacts.db'))
    monkeypatch.setenv('DEMO_TOKEN','coordination-test-secret')
    monkeypatch.setenv('TWILIO_LIVE_SEND','false')
    store.initialize();co.initialize()
    store.upsert_incident('owner_001',channel='demo')
    store.upsert_incident('other_002',channel='demo')
    return None

def enrolled(name='Primary'):
    person=co.enroll('owner_001',name,'+15555550123')
    co.consent(person['id'],person['enrollment_token'],True)
    return person

def test_enrollment_requires_participant_consent(setup):
    p=co.enroll('owner_001','Pending','+15555550123')
    with pytest.raises(HTTPException) as e:
        co.request_help('owner_001',p['id'],None,'Please check in','unique-key-1',True)
    assert e.value.status_code==409
    with pytest.raises(HTTPException):co.consent(p['id'],'forged-token',True)
    assert co.consent(p['id'],p['enrollment_token'],True)['state']=='enrolled'

def test_no_approval_no_send_and_owner_isolation(setup):
    p=enrolled()
    with pytest.raises(HTTPException):co.request_help('owner_001',p['id'],None,'Help','unique-key-1',False)
    with pytest.raises(HTTPException):co.request_help('other_002',p['id'],None,'Help','unique-key-1',True)
    with store.connect() as c:assert c.execute('SELECT count(*) FROM outbox').fetchone()[0]==0

def test_ack_does_not_mean_assistance_and_send_idempotent(setup):
    p=enrolled()
    r=co.request_help('owner_001',p['id'],None,'Please check in','unique-key-1',True)
    assert r['delivery_state']=='simulated'
    again=co.request_help('owner_001',p['id'],None,'Please check in','unique-key-1',True)
    assert again['id']==r['id']
    with store.connect() as c:assert c.execute('SELECT count(*) FROM outbox').fetchone()[0]==1
    with pytest.raises(HTTPException):co.respond(r['id'],r['response_token'],'assisted')
    assert co.respond(r['id'],r['response_token'],'acknowledged')['state']=='acknowledged'
    assert co.respond(r['id'],r['response_token'],'assisted')['state']=='assisted'

def test_decline_one_preapproved_fallback(setup):
    p=enrolled();f=enrolled('Fallback')
    r=co.request_help('owner_001',p['id'],f['id'],'Please check in','unique-key-1',True)
    response=co.respond(r['id'],r['response_token'],'declined')
    assert response['fallback_triggered'] and response['fallback']['delivery_state']=='simulated'
    assert not co.respond(r['id'],r['response_token'],'declined')['fallback_triggered']
    with store.connect() as c:assert c.execute('SELECT count(*) FROM contact_requests').fetchone()[0]==2
    child=response['fallback']
    assert 'response_token' not in child
    with store.connect() as c:
        event=c.execute("SELECT body FROM events WHERE kind='trusted_contact_response' LIMIT 1").fetchone()['body']
    assert 'response_token' not in event
    assert child['delivery_state']=='simulated'

def test_revocation_and_expiry(setup):
    p=enrolled()
    r=co.request_help('owner_001',p['id'],None,'Please check in','unique-key-1',True)
    co.consent(p['id'],p['enrollment_token'],False)
    with pytest.raises(HTTPException):co.respond(r['id'],r['response_token'],'acknowledged')
    with store.connect() as c:c.execute('UPDATE trusted_contacts SET token_expires=0 WHERE id=?',(p['id'],))
    with pytest.raises(HTTPException):co.consent(p['id'],p['enrollment_token'],True)
    assert co.consent(p['id'],p['enrollment_token'],False)['state']=='revoked'

def test_routes_auth_and_participant_forgery(setup):
    app=FastAPI();co.install_coordination(app);client=TestClient(app)
    assert client.post('/demo/incidents/owner_001/contacts',json={'name':'Test','phone':'+15555550123'}).status_code==401
    p=enrolled();r=co.request_help('owner_001',p['id'],None,'Please check in','unique-key-1',True)
    assert client.get('/contacts/requests/'+r['id']+'?token=forged').status_code==403
    page=client.get('/contacts/requests/'+r['id']+'?token='+r['response_token'])
    assert page.status_code==200 and page.headers['referrer-policy']=='no-referrer'
    assert client.post('/contacts/requests/'+r['id']+'/respond',json={'token':r['response_token'],'decision':'acknowledged'}).json()['state']=='acknowledged'


def test_http_consent_revocation_after_enrollment_expiry(setup):
    app=FastAPI();co.install_coordination(app);client=TestClient(app)
    p=enrolled()
    with store.connect() as c:c.execute('UPDATE trusted_contacts SET token_expires=0 WHERE id=?',(p['id'],))
    assert client.get('/contacts/'+p['id']+'/consent?token='+p['enrollment_token']).status_code==200
    assert client.post('/contacts/'+p['id']+'/consent',json={'token':p['enrollment_token'],'accept':True}).status_code==403
    assert client.post('/contacts/'+p['id']+'/consent',json={'token':p['enrollment_token'],'accept':False}).json()['state']=='revoked'
