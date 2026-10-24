import concurrent.futures
import time
from types import SimpleNamespace
import httpx
import pytest
from relay import store, providers, outbox

@pytest.fixture(autouse=True)
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('RELAY_DB',str(tmp_path/'outbox.db'))
    monkeypatch.setenv('TWILIO_LIVE_SEND','false')
    store.initialize()
    store.upsert_incident('user','+15555550001','sms')


def complete(text='answer'):
    job_id=store.enqueue('user','message',{'text':'question','channel':'sms','sms_consent':True})
    job=store.claim()
    return store.finish(job_id,job['lease'],text),job_id


def test_completion_and_send_intent_are_atomic_and_recoverable():
    result,job_id=complete()
    assert result['outbox_id']=='job-'+job_id
    assert store.outbox_for(result['outbox_id'])['state']=='prepared'
    # Simulate a gateway crash before its synchronous send attempt.
    assert outbox.drain_once()==[{'state':'simulated','id':result['outbox_id']}]
    assert providers.send_reference('user','different ignored body',True,idempotency_key=job_id)['state']=='simulated'
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM outbox').fetchone()[0]==1
    assert store.outbox_for(result['outbox_id'])['content']=='answer'


def test_intent_insert_failure_rolls_back_job_completion(monkeypatch):
    job_id=store.enqueue('user','message',{'text':'question','channel':'sms','sms_consent':True})
    job=store.claim()
    def broken(*args):
        raise RuntimeError('disk failed')
    monkeypatch.setattr(store,'_prepare_job_outbox',broken)
    with pytest.raises(RuntimeError):
        store.finish(job_id,job['lease'],'answer')
    assert store.job_for('user',job_id)['state']=='leased'


def test_expired_last_lease_purges_frame_and_prepares_failure():
    job_id=store.enqueue('user','message',{'text':'question','image_base64':'private','channel':'sms','sms_consent':True})
    store.claim()
    with store.connect() as c:
        c.execute('UPDATE jobs SET attempts=3,leased_until=?',(time.time()-1,))
    assert store.recover_expired()==1
    assert 'image_base64' not in store.job_for('user',job_id)['payload']
    row=store.outbox_for('job-'+job_id)
    assert row['state']=='prepared'
    assert row['content']==store.FAILURE_TEXT
    assert store.recover_expired()==0


def test_withdrawn_consent_blocks_prepared_send():
    result,_=complete()
    store.set_optout('+15555550001',True)
    assert providers.dispatch_outbox(result['outbox_id'])['reason']=='opted_out'
    assert store.outbox_for(result['outbox_id'])['state']=='failed'


def test_timeout_never_retries_acceptance_unknown(monkeypatch):
    result,_=complete();calls=[]
    monkeypatch.setenv('TWILIO_LIVE_SEND','true')
    for key in ('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_NUMBER'):
        monkeypatch.setenv(key,'test')
    def timeout(**kwargs):
        calls.append(kwargs)
        raise httpx.ReadTimeout('unknown')
    monkeypatch.setattr(providers,'Client',lambda *a,**kw:SimpleNamespace(messages=SimpleNamespace(create=timeout)))
    assert providers.dispatch_outbox(result['outbox_id'])['state']=='acceptance_unknown'
    assert providers.dispatch_outbox(result['outbox_id'])['state']=='acceptance_unknown'
    assert outbox.drain_once()==[]
    assert len(calls)==1
    assert store.delivery_update('SM'+'a'*32,'delivered',result['outbox_id'])
    assert store.outbox_for(result['outbox_id'])['state']=='delivered'


def test_atomic_dispatch_claim_and_stalled_submission():
    result,_=complete()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows=list(pool.map(lambda _:store.claim_outbox(result['outbox_id']),range(8)))
    assert sum(row is not None for row in rows)==1
    with store.connect() as c:
        c.execute('UPDATE outbox_submissions SET started=?',(time.time()-61,))
    assert store.recover_stalled_submissions()==1
    assert providers.dispatch_outbox(result['outbox_id'])['state']=='acceptance_unknown'
