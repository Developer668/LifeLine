"""Phone-to-queue-to-worker-to-outbox contracts with explicitly simulated inference."""
import hashlib
import re
from urllib.parse import urlsplit
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator
from relay.app import app
from relay import store
from relay.worker import run_one


def test_signed_voice_sms_association_and_single_response(monkeypatch):
    monkeypatch.setenv('TWILIO_AUTH_TOKEN', 'integration-signing-secret')
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://relay.example.test')
    monkeypatch.setenv('RELAY_WORKER_TOKEN', 'integration-worker-secret')
    monkeypatch.setenv('RELAY_ALLOW_SIMULATION', 'true')
    monkeypatch.setenv('MODEL_BASE_URL', '')
    monkeypatch.setenv('TWILIO_LIVE_SEND', 'false')
    c=TestClient(app)
    def signed(path, form):
        sig=RequestValidator('integration-signing-secret').compute_signature('https://relay.example.test'+path,form)
        return c.post(path,data=form,headers={'X-Twilio-Signature':sig})
    call={'CallSid':'CA'+'1'*32,'From':'+15555550111'}
    assert signed('/twilio/voice',call).status_code==200
    form={**call,'SpeechResult':'Tell me about this prototype'}
    turn=signed('/twilio/voice/turn?turn=initial',form)
    assert turn.status_code==200
    worker={'Authorization':'Bearer integration-worker-secret'}
    job=c.post('/worker/claim',headers=worker).json()['job']
    assert job['incident']=='call-'+call['CallSid']
    answer=run_one(job)
    assert 'simulation' in answer
    completion={'lease':job['lease'],'result':answer}
    assert c.post('/worker/jobs/'+job['id']+'/complete',headers=worker,json=completion).status_code==200
    code=store.create_link(job['incident'])
    sms={'MessageSid':'SM'+'2'*32,'From':call['From'],'Body':'LINK '+code}
    assert 'context linked' in signed('/twilio/sms',sms).text
    sms.update(MessageSid='SM'+'3'*32,Body='What can you help me find?')
    assert signed('/twilio/sms',sms).status_code==200
    assert signed('/twilio/sms',sms).status_code==200
    linked=c.post('/worker/claim',headers=worker).json()['job']
    assert linked['incident']==job['incident']
    assert linked['payload']['history'][0]['content']=='Tell me about this prototype'
    assert c.post('/worker/jobs/'+linked['id']+'/complete',headers=worker,json={'lease':linked['lease'],'result':run_one(linked)}).status_code==200
    assert c.post('/worker/jobs/'+linked['id']+'/complete',headers=worker,json={'lease':linked['lease'],'result':'duplicate'}).status_code==409
    info=store.incident_info(job['incident'])
    assert len(info['outbox'])==1 and info['outbox'][0]['state']=='simulated'
    assert len(info['jobs'])==2
    # Another phone never inherits the authorized association or prior history.
    sms.update(MessageSid='SM'+'4'*32,From='+15555550222',Body='What can you help me find?')
    assert signed('/twilio/sms',sms).status_code==200
    other=c.post('/worker/claim',headers=worker).json()['job']
    assert other['incident']!=job['incident'] and other['payload']['history']==[]


def test_background_dispatch_recovers_prepared_reply(monkeypatch):
    monkeypatch.setenv('TWILIO_LIVE_SEND','false')
    store.upsert_incident('recovered','+15555550111','sms')
    store.enqueue('recovered','message',{'text':'fictional request','channel':'sms','sms_consent':True})
    job=store.claim()
    store.finish(job['id'],job['lease'],'SIMULATED reply')
    assert store.incident_info('recovered')['outbox'][0]['state']=='prepared'
    import time
    with TestClient(app):
        for _ in range(30):
            if store.incident_info('recovered')['outbox'][0]['state']=='simulated':break
            time.sleep(.05)
        else:raise AssertionError('Background outbox did not recover prepared message')


def test_claim_exposes_validated_common_incident_event():
    from relay.contracts import IncidentEvent
    store.upsert_incident('event_drill',channel='demo')
    store.enqueue('event_drill','message',{'text':'fictional event','channel':'demo'})
    job=store.claim()
    event=IncidentEvent.model_validate(job['event'])
    assert event.incident_id==event.session_id=='event_drill'
    assert event.event_id==job['id']
    assert event.payload=={'text':'fictional event'}


def test_official_reference_and_timestamp_snapshot_survive_completion(monkeypatch):
    monkeypatch.setenv('TWILIO_LIVE_SEND','false')
    monkeypatch.setenv('RELAY_WORKER_TOKEN','reference-worker-token')
    store.upsert_incident('source_drill','+15555550111','sms')
    store.enqueue('source_drill','message',{'text':'weather','channel':'sms','sms_consent':True})
    job=store.claim()
    context={'source':'nws','state':'available','fetched_at':'2026-10-24T22:00:00Z','source_updated_at':'2026-10-24T21:58:00Z','alerts':[], 'notice':'An empty result does not establish safety.'}
    response=TestClient(app).post('/worker/jobs/'+job['id']+'/complete',headers={'Authorization':'Bearer reference-worker-token'},json={
        'lease':job['lease'],'result':'No matching alerts; this does not establish safety.',
        'reference_text':'Official NWS snapshot updated 2026-10-24T21:58:00Z. https://weather.gov',
        'official_context':context})
    assert response.status_code==200
    info=store.incident_info('source_drill')
    assert 'https://weather.gov' in info['outbox'][0]['content']
    assert info['jobs'][0]['official_context']['source_updated_at'].startswith('2026-10-24T21:58:00')
