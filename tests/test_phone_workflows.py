import base64
import re
import pytest
from fastapi.testclient import TestClient
from relay.app import app
from relay import store, coordination as co, phone_commands as pc, bandwidth_sms as sms

OWNER='+15550001111'
PRIMARY='+15550002222'
FALLBACK='+15550003333'


def setup_owner():
    store.upsert_incident('phone-owner',OWNER,'sms');sms.register('phone-owner')


def enroll(name,phone):
    reply=pc.handle('phone-owner',OWNER,f'ADD CONTACT {name} {phone}')
    code=re.search(r'JOIN ([A-F0-9]{10})',reply).group(1)
    assert 'enrolled' in pc.handle('participant',phone,'JOIN '+code)
    return code


def test_all_contact_steps_work_by_text_without_urls(monkeypatch):
    setup_owner()
    code=enroll('Alice',PRIMARY);enroll('Bob',FALLBACK)
    assert 'Alice (enrolled)' in pc.handle('phone-owner',OWNER,'CONTACTS')
    pending=pc.handle('phone-owner',OWNER,'REQUEST Alice FALLBACK Bob: Please check in with me.')
    approval=re.search(r'APPROVE ([A-F0-9]{10})',pending).group(1)
    with store.connect() as c:assert c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0
    assert 'invalid' in pc.handle('other',PRIMARY,'APPROVE '+approval)
    assert 'prepared' in pc.handle('phone-owner',OWNER,'APPROVE '+approval)
    assert 'prepared' in pc.handle('phone-owner',OWNER,'APPROVE '+approval)
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM contact_requests').fetchone()[0]==1
        box=c.execute('SELECT * FROM outbox').fetchone()
    assert 'http' not in box['content'] and 'ACK ' in box['content']
    reply_code=re.search(r'ACK ([A-F0-9]{10})',box['content']).group(1)
    assert 'invalid' in pc.handle('other',FALLBACK,'ACK '+reply_code)
    assert 'acknowledged' in pc.handle('participant',PRIMARY,'ACK '+reply_code)
    assert 'acknowledged' in pc.handle('phone-owner',OWNER,'STATUS')
    assert 'assisted' in pc.handle('participant',PRIMARY,'ASSISTED '+reply_code)
    assert 'revoked' in pc.handle('participant',PRIMARY,'REVOKE '+code)


def test_decline_triggers_only_one_approved_sms_fallback():
    setup_owner();enroll('Alice',PRIMARY);enroll('Bob',FALLBACK)
    pending=pc.handle('phone-owner',OWNER,'REQUEST Alice FALLBACK Bob: Please check in.')
    approval=re.search(r'APPROVE ([A-F0-9]{10})',pending).group(1)
    pc.handle('phone-owner',OWNER,'APPROVE '+approval)
    with store.connect() as c:body=c.execute('SELECT content FROM outbox').fetchone()[0]
    code=re.search(r'ACK ([A-F0-9]{10})',body).group(1)
    assert 'fallback' in pc.handle('participant',PRIMARY,'DECLINE '+code)
    pc.handle('participant',PRIMARY,'DECLINE '+code)
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM contact_requests').fetchone()[0]==2
        assert c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==2


def test_sms_linking_requires_proof_and_shares_followup_context(monkeypatch):
    monkeypatch.setenv('BANDWIDTH_CALLBACK_SECRET','workflow-test-secret')
    monkeypatch.setenv('BANDWIDTH_MESSAGING_APPLICATION_ID','workflow-app')
    monkeypatch.setenv('BANDWIDTH_NUMBER','+15550000131')
    headers={'Authorization':'Basic '+base64.b64encode(b'relay-hook:workflow-test-secret').decode()}
    store.upsert_incident('active-call',OWNER,'voice');sms.register('active-call')
    original=store.enqueue('active-call','message',{'text':'Check weather for 92037','channel':'voice'})
    job=store.claim();store.finish(original,job['lease'],'An official lookup result.')
    code=store.create_link('active-call');client=TestClient(app)
    def receive(text,mid,phone=OWNER):
        return client.post('/bandwidth/sms',headers=headers,json=[{'type':'message-received','message':{
            'id':mid,'applicationId':'workflow-app','owner':'+15550000131','from':phone,'text':text}}])
    assert receive('LINK '+code,'link-wrong-number',PRIMARY).status_code==204
    wrong='bw-sms-'+__import__('hashlib').sha256(PRIMARY.encode()).hexdigest()[:24]
    assert store.resolve_sms(wrong)==wrong
    assert receive('LINK '+code,'link-correct-number').status_code==204
    assert receive('Any update?','followup-test-123').status_code==204
    followup=store.claim()
    assert followup['incident']=='active-call'
    assert followup['payload']['history'][0]['content']=='Check weather for 92037'
    assert receive('UNLINK','unlink-test-123').status_code==204
    owner='bw-sms-'+__import__('hashlib').sha256(OWNER.encode()).hexdigest()[:24]
    assert store.resolve_sms(owner)==owner


def test_spoken_zip_and_code_abuse_limits():
    from relay.live_audio import normalize_transcript
    assert normalize_transcript('weather for nine two zero three seven')=='weather for 92037'
    assert normalize_transcript('one two three four five six')=='one two three four five six'
    initialize=pc.initialize;initialize()
    token=pc.issue(OWNER,'join',{'drill':True})
    for _ in range(10):assert pc.action(OWNER,'0000000000',['join']) is None
    assert pc.action(OWNER,token,['join']) is None


def test_multipart_contact_receipt_needs_all_parts():
    setup_owner();enroll('Alice',PRIMARY)
    pending=pc.handle('phone-owner',OWNER,'REQUEST Alice: Please check in.')
    code=re.search(r'APPROVE ([A-F0-9]{10})',pending).group(1)
    pc.handle('phone-owner',OWNER,'APPROVE '+code)
    with store.connect() as c:
        parent=c.execute('SELECT * FROM contact_outboxes').fetchone()
        c.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?)',('child-part','contact-test',PRIMARY,'Part two','prepared',0,None))
        c.execute('INSERT INTO contact_outboxes VALUES(?,?)',(parent['request_id'],'child-part'))
    store.update_outbox(parent['outbox_id'],'delivered','provider-1')
    co.sync_outbox(parent['outbox_id'])
    assert 'Delivery: prepared' in pc.handle('phone-owner',OWNER,'STATUS')
    store.update_outbox('child-part','delivered','provider-2');co.sync_outbox('child-part')
    assert 'Delivery: delivered' in pc.handle('phone-owner',OWNER,'STATUS')


def test_duplicate_sms_commands_do_not_duplicate_enrollment(monkeypatch):
    monkeypatch.setenv('BANDWIDTH_CALLBACK_SECRET','workflow-test-secret')
    monkeypatch.setenv('BANDWIDTH_MESSAGING_APPLICATION_ID','workflow-app')
    monkeypatch.setenv('BANDWIDTH_NUMBER','+15550000131')
    headers={'Authorization':'Basic '+base64.b64encode(b'relay-hook:workflow-test-secret').decode()}
    body=[{'type':'message-received','message':{'id':'duplicate-contact-command','applicationId':'workflow-app',
        'owner':'+15550000131','from':OWNER,'text':'ADD CONTACT Alice '+PRIMARY}}]
    client=TestClient(app)
    assert client.post('/bandwidth/sms',headers=headers,json=body).status_code==204
    assert client.post('/bandwidth/sms',headers=headers,json=body).status_code==204
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM trusted_contacts').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==1
        assert 'http' not in c.execute('SELECT content FROM outbox').fetchone()[0]
