import asyncio
import concurrent.futures
import json
import pytest
import httpx
from relay import openai_fallback as fallback, store


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','unit-test-key')
    monkeypatch.setenv('RELAY_OPENAI_FALLBACK','true')
    monkeypatch.setenv('RELAY_OPENAI_BUDGET_USD','5')
    fallback.initialize()


def test_lifetime_cap_is_atomic_and_cannot_exceed_five_dollars(enabled,monkeypatch):
    monkeypatch.setenv('RELAY_OPENAI_BUDGET_USD','500')
    def claim(_):
        try:return fallback.reserve('drill',100_000)
        except fallback.FallbackUnavailable:return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        claims=list(pool.map(claim,range(65)))
    assert sum(c is not None for c in claims)==50
    assert fallback.budget()=={'cap_micro':5_000_000,'accounted_micro':5_000_000,'remaining_micro':0}
    fallback.settle(next(c for c in claims if c),200)
    assert fallback.budget()['remaining_micro']==99_800


def test_disabled_fallback_never_reserves_or_calls(monkeypatch):
    monkeypatch.setenv('RELAY_OPENAI_FALLBACK','false')
    with pytest.raises(fallback.FallbackUnavailable):fallback.reserve('answer',100)
    assert fallback.budget()['accounted_micro']==0


def test_unknown_network_charge_keeps_reservation(enabled,monkeypatch):
    from relay import engine
    monkeypatch.setattr(engine,'build_context',lambda text:'')
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):raise httpx.ReadTimeout('unknown receipt')
    monkeypatch.setattr(fallback.httpx,'Client',Client)
    store.upsert_incident('budget-drill')
    with pytest.raises(fallback.FallbackUnavailable):fallback.answer('A fictional kit question','budget-drill')
    assert fallback.budget()['accounted_micro']>=1000


def test_transcription_is_bounded_and_accounted(enabled,monkeypatch):
    original=httpx.Client
    calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(200,json={'text':'What belongs in a kit?','usage':{
            'type':'tokens','input_token_details':{'audio_tokens':25,'text_tokens':40},'output_tokens':8}})
    monkeypatch.setattr(fallback.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    assert fallback.transcribe(b'\0'*16000)=='What belongs in a kit?'
    assert fallback.budget()['accounted_micro']==165
    assert len(calls)==1 and calls[0].url.host=='api.openai.com'
    with pytest.raises(fallback.FallbackUnavailable):fallback.transcribe(b'\0'*160001)
    assert len(calls)==1


@pytest.mark.asyncio
async def test_healthy_primary_does_not_use_openai(enabled,monkeypatch):
    from relay import bandwidth
    store.upsert_incident('primary-drill')
    class Socket:pass
    c=bandwidth.Conversation(Socket(),'primary-drill');spoken=[]
    async def speak(text,generation):spoken.append(text)
    monkeypatch.setattr(c,'speak',speak)
    monkeypatch.setattr(store,'enqueue',lambda *a,**k:'primary-job')
    monkeypatch.setattr(store,'job_for',lambda *a,**k:{'state':'complete','result':'A working primary reply.'})
    monkeypatch.setattr(fallback,'answer',lambda *a:(_ for _ in ()).throw(AssertionError('Paid fallback should not run')))
    await c.answer('How should I organize supplies?',c.generation)
    assert spoken==['A working primary reply.']


@pytest.mark.asyncio
async def test_failed_primary_uses_fallback_and_records_selected_answer(enabled,monkeypatch):
    from relay import bandwidth
    store.upsert_incident('failed-drill');calls=[];spoken=[]
    class Socket:pass
    c=bandwidth.Conversation(Socket(),'failed-drill')
    async def speak(text,generation):spoken.append(text)
    monkeypatch.setattr(c,'speak',speak)
    monkeypatch.setattr(store,'enqueue',lambda *a,**k:'failed-job')
    monkeypatch.setattr(store,'job_for',lambda *a,**k:{'state':'failed','result':'Unavailable'})
    monkeypatch.setattr(store,'record_fallback',lambda *a:calls.append(a) or True)
    monkeypatch.setattr(fallback,'answer',lambda *a:('A verified fallback reply.',None,None))
    await c.answer('How should I organize supplies?',c.generation)
    assert spoken==['A verified fallback reply.']
    assert calls[0][0]=='failed-job'


def test_selected_fallback_preserves_history_and_invalidates_primary_lease():
    store.upsert_incident('history-drill')
    ident=store.enqueue('history-drill','message',{'text':'Where is my kit?','channel':'voice'})
    job=store.claim()
    assert store.record_fallback(ident,'Your fallback answer.')
    assert store.finish(ident,job['lease'],'Late primary reply.') is None
    assert store.history('history-drill')[-1]['content']=='Your fallback answer.'
    assert not store.record_fallback(ident,'A duplicate fallback.')


def test_primary_audio_success_does_not_pay_for_transcription(enabled,monkeypatch):
    pytest.importorskip('numpy')
    from relay import live_audio
    class Segment:
        text='What should I put in a kit?';no_speech_prob=.01;avg_logprob=-.1;compression_ratio=1
    class Model:
        def transcribe(self,*a,**k):return iter([Segment()]),None
    r=object.__new__(live_audio.WhisperRecognizer)
    r.model=Model();r.last_decode={};r.local_failures=0;r.fallback_attempts=0
    monkeypatch.setattr(fallback,'transcribe',lambda *a:(_ for _ in ()).throw(AssertionError('Healthy local speech must remain free')))
    assert r.decode(b'\0'*64000)=='What should I put in a kit?'
    assert fallback.budget()['accounted_micro']==0


def test_api_primary_worker_completes_queue_and_keeps_provider_origin(enabled,monkeypatch):
    from relay import api_worker
    monkeypatch.setenv('RELAY_INFERENCE_PROVIDER','openai')
    monkeypatch.setattr(fallback,'answer',lambda *a:('A concise API answer.',None,None))
    store.upsert_incident('api-primary-drill')
    ident=store.enqueue('api-primary-drill','message',{'text':'A kit question','channel':'voice'})
    job=store.claim();api_worker.process(job)
    result=store.job_for('api-primary-drill',ident)
    assert result['state']=='complete'
    assert result['result']=='A concise API answer.'
    assert json.loads(result['payload'])['inference_provider']=='openai'


def test_molab_cannot_claim_jobs_in_explicit_api_primary_mode(enabled,monkeypatch):
    from relay.app import app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('RELAY_INFERENCE_PROVIDER','openai')
    monkeypatch.setenv('RELAY_WORKER_TOKEN','worker-test-key')
    store.upsert_incident('exclusive-drill')
    store.enqueue('exclusive-drill','message',{'text':'A kit question','channel':'voice'})
    client=TestClient(app)
    assert client.post('/worker/claim',headers={'Authorization':'Bearer worker-test-key'}).json()=={'job':None}
    assert store.latest('exclusive-drill')['state']=='queued'


def test_budget_inspection_requires_operator_token(enabled,monkeypatch):
    from relay.app import app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('DEMO_TOKEN','budget-inspection-key')
    client=TestClient(app)
    assert client.get('/demo/openai-budget').status_code==401
    data=client.get('/demo/openai-budget',headers={'Authorization':'Bearer budget-inspection-key'}).json()
    assert data['cap_usd']==5 and data['accounted_usd']==0
    assert 'unit-test-key' not in str(data)
