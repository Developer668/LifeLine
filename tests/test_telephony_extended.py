import hashlib
import os
import re
import time
from urllib.parse import urlparse
import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator
from relay import store, providers
from relay.app import app

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('RELAY_DB',str(tmp_path/'telephony.db'))
    monkeypatch.setenv('TWILIO_AUTH_TOKEN','extended-secret')
    monkeypatch.setenv('PUBLIC_BASE_URL','http://127.0.0.1:8000')
    monkeypatch.setenv('TWILIO_LIVE_SEND','false')
    monkeypatch.setenv('RELAY_WORKER_TOKEN','extended-worker-token')
    store.initialize()
    return TestClient(app)


def signed(client,path,form):
    signature=RequestValidator('extended-secret').compute_signature('http://127.0.0.1:8000'+path,form)
    return client.post(path,data=form,headers={'X-Twilio-Signature':signature})


def test_link_requires_code_phone_expiry_and_single_use(env):
    store.upsert_incident('voice-a','+15555550100','voice')
    store.upsert_incident('sms-a','+15555550100','sms')
    code=store.create_link('voice-a')
    assert store.resolve_sms('sms-a')=='sms-a'
    assert store.redeem_link('sms-b','+15555550101',code) is None
    assert store.redeem_link('sms-a','+15555550100',code)=='voice-a'
    assert store.resolve_sms('sms-a')=='voice-a'
    assert store.redeem_link('sms-a','+15555550100',code) is None
    code2=store.create_link('voice-a')
    with store.connect() as c:
        c.execute('UPDATE link_codes SET expires=?',(time.time()-1,))
    assert store.redeem_link('sms-a','+15555550100',code2) is None


def test_signed_sms_link_flow(env):
    phone='+15555550200';call='call-CA'+'2'*32
    store.upsert_incident(call,phone,'voice');code=store.create_link(call)
    f={'MessageSid':'SM'+'1'*32,'From':phone,'Body':'LINK '+code}
    assert 'context linked' in signed(env,'/twilio/sms',f).text
    assert store.resolve_sms('sms-'+hashlib.sha256(phone.encode()).hexdigest()[:28])==call
    f.update(MessageSid='SM'+'3'*32,Body='check conditions')
    signed(env,'/twilio/sms',f)
    assert store.latest(call)['incident']==call


def test_voice_turn_retry_and_poll_are_job_scoped(env):
    form={'CallSid':'CA'+'4'*32,'From':'+15555550300','SpeechResult':'Check weather'}
    path='/twilio/voice/turn?turn=test-turn'
    first=signed(env,path,form);second=signed(env,path,form)
    assert first.text==second.text
    incident='call-'+form['CallSid']
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM jobs WHERE incident=?',(incident,)).fetchone()[0]==1
    job=store.claim();store.finish(job['id'],job['lease'],'voice answer')
    store.enqueue(incident,'message',{'text':'camera newer','channel':'camera'})
    poll=f"/twilio/voice/poll?incident={incident}&job_id={job['id']}&attempt=0"
    assert 'voice answer' in signed(env,poll,form).text
    wrong=dict(form,CallSid='CA'+'5'*32)
    assert signed(env,poll,wrong).status_code==403


def test_delivery_callback_no_terminal_downgrade(env):
    store.upsert_incident('out','+15555550400','sms')
    ident=store.queue_outbox('out','+15555550400','test')
    sid='SM'+'6'*32;store.update_outbox(ident,'accepted_by_provider',sid)
    assert signed(env,'/twilio/status',{'MessageSid':sid,'MessageStatus':'delivered'}).status_code==204
    signed(env,'/twilio/status',{'MessageSid':sid,'MessageStatus':'sent'})
    assert store.incident_info('out')['outbox'][0]['state']=='delivered'
    assert env.post('/twilio/status',data={'MessageSid':sid,'MessageStatus':'failed'}).status_code==403


def test_simulation_and_optout_have_persisted_truthful_states(env):
    store.upsert_incident('out','+15555550400','sms')
    assert providers.send_reference('out','test',True)['state']=='simulated'
    assert store.incident_info('out')['outbox'][0]['state']=='simulated'
    signed(env,'/twilio/sms',{'MessageSid':'SM'+'7'*32,'From':'+15555550400','Body':'STOP'})
    assert providers.send_reference('out','test',True)['state']=='not_authorized'


def test_exhausted_lease_recovers_queue_and_rejects_completion(env):
    store.upsert_incident('lease','+15555550400','sms')
    first=store.enqueue('lease','message',{'text':'one'})
    store.claim()
    with store.connect() as c:
        c.execute('UPDATE jobs SET attempts=3,leased_until=? WHERE id=?',(time.time()-1,first))
    second=store.enqueue('lease','message',{'text':'two'})
    assert store.claim()['id']==second
    assert store.job_for('lease',first)['state']=='failed'
    headers={'Authorization':'Bearer extended-worker-token'}
    assert env.post(f'/worker/jobs/{second}/complete',headers=headers,json={'lease':'x'*24,'result':'ok','error':'bad'}).status_code==422


def test_history_cannot_cross_incidents(env):
    for incident in ('one','two'):
        store.upsert_incident(incident)
        job=store.enqueue(incident,'message',{'text':incident})
        claimed=store.claim();assert claimed['id']==job
        store.finish(job,claimed['lease'],incident+' response')
    store.enqueue('one','message',{'text':'followup'})
    history=store.claim()['payload']['history']
    assert history==[{'role':'user','content':'one'},{'role':'assistant','content':'one response'}]


def test_frame_data_purged_after_completion(env):
    store.upsert_incident('image')
    ident=store.enqueue('image','message',{'text':'frame','image_base64':'private-image-bytes'})
    job=store.claim();store.finish(ident,job['lease'],'Observation')
    assert 'image_base64' not in store.job_for('image',ident)['payload']


def test_early_delivery_callback_binds_prepared_outbox(env):
    store.upsert_incident('early','+15555550400','sms')
    ident=store.queue_outbox('early','+15555550400','test')
    sid='SM'+'8'*32
    path='/twilio/status?outbox_id='+ident
    assert signed(env,path,{'MessageSid':sid,'MessageStatus':'delivered'}).status_code==204
    store.update_outbox(ident,'accepted_by_provider',sid)
    assert store.incident_info('early')['outbox'][0]['state']=='delivered'


def test_live_provider_acceptance_records_sid_and_callback(env,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('TWILIO_LIVE_SEND','true')
    monkeypatch.setenv('TWILIO_ACCOUNT_SID','AC'+'a'*32)
    monkeypatch.setenv('TWILIO_NUMBER','+15555550000')
    observed={}
    def create(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(sid='SM'+'9'*32,status='queued')
    monkeypatch.setattr(providers,'Client',lambda *a,**kw:SimpleNamespace(messages=SimpleNamespace(create=create)))
    store.upsert_incident('live','+15555550400','sms')
    result=providers.send_reference('live','test',True)
    assert result['state']=='accepted_by_provider'
    assert '/twilio/status?outbox_id=' in observed['status_callback']
    assert store.incident_info('live')['outbox'][0]['state']=='accepted_by_provider'


def test_concurrent_voice_reservation_does_not_repeat_send(env):
    incident='call-CA'+'a'*32
    store.upsert_incident(incident,'+15555550100','voice')
    store.add_event(incident,'voice_turn',{},event_id=incident+':reserved')
    form={'CallSid':'CA'+'a'*32,'From':'+15555550100','SpeechResult':'text me a camera link'}
    response=signed(env,'/twilio/voice/turn?turn=reserved',form)
    assert response.status_code==503
    assert store.incident_info(incident)['outbox']==[]


def test_outbound_trial_call_associates_recipient_not_service_number(env):
    from relay.app import call_incident
    form={'CallSid':'CA'+'b'*32,'From':'+15555550000','To':'+15555550100','Direction':'outbound-api'}
    incident=call_incident(form)
    assert store.phone_for(incident)==form['To']
