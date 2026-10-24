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
