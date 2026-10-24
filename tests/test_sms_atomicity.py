import concurrent.futures
import json
import pytest
from relay import store

@pytest.fixture(autouse=True)
def env(tmp_path,monkeypatch):
    monkeypatch.setenv('RELAY_DB',str(tmp_path/'sms.db'))
    store.initialize()
    store.upsert_incident('sms-user','+15555550001','sms')


def accept(event='SM'+'a'*32,incident='sms-user'):
    return store.accept_sms_job(incident,event,{'message_sid':event,'length':5},
                                {'text':'hello','channel':'sms','sms_consent':True})


def test_retry_after_completion_never_enqueues_second_job():
    first,job_id=accept();assert first
    job=store.claim();store.finish(job_id,job['lease'],'answer')
    assert accept()==(False,job_id)
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
        event=c.execute('SELECT body FROM events').fetchone()
        assert json.loads(event['body'])['job_id']==job_id


def test_insert_failure_rolls_back_job_and_allows_retry(monkeypatch):
    original=store._enqueue_job
    def broken(*args):
        original(*args)
        raise RuntimeError('interruption before event insertion')
    monkeypatch.setattr(store,'_enqueue_job',broken)
    with pytest.raises(RuntimeError):
        accept()
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM events').fetchone()[0]==0
    monkeypatch.setattr(store,'_enqueue_job',original)
    assert accept()[0]


def test_simultaneous_duplicates_create_one_turn():
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        outcomes=list(pool.map(lambda _:accept(),range(10)))
    assert sum(new for new,_ in outcomes)==1
    assert len({job for _,job in outcomes})==1


def test_distinct_messages_with_same_text_remain_separate_turns():
    assert accept('SM'+'a'*32)[1]!=accept('SM'+'b'*32)[1]


def test_duplicate_event_cannot_disclose_other_incident_job():
    accept()
    store.upsert_incident('other','+15555550002','sms')
    assert accept(incident='other')==(False,None)
