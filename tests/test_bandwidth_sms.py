import base64
import pytest
from fastapi.testclient import TestClient
from relay.app import app
from relay import store, providers, bandwidth_sms


def settings(monkeypatch):
    for key,value in {'BANDWIDTH_CALLBACK_SECRET':'callback-test','BANDWIDTH_ACCOUNT_ID':'1234',
        'BANDWIDTH_MESSAGING_APPLICATION_ID':'sms-app','BANDWIDTH_NUMBER':'+15550000131',
        'BANDWIDTH_CLIENT_ID':'test-client','BANDWIDTH_CLIENT_SECRET':'test-secret'}.items():
        monkeypatch.setenv(key,value)
    return {'Authorization':'Basic '+base64.b64encode(b'relay-hook:callback-test').decode()}


def incoming(text='What belongs in a basic kit?',mid='message-test-123'):
    return [{'type':'message-received','message':{'id':mid,'applicationId':'sms-app',
        'owner':'+15550000131','from':'+15550000442','to':['+15550000131'],'text':text}}]


def test_sms_format_direct_ascii_and_bounded_parts():
    parts=bandwidth_sms.sms_parts('A clear sentence with “quotes” and useful detail. '*8)
    assert 1<len(parts)<=4
    for part in parts:
        assert len(part)<=129 and part.isascii()
        assert bandwidth_sms.sms_parts(part)==[part] # no recursive multipart expansion
        assert 'http' not in part
    assert len(bandwidth_sms.sms_parts('x'*2000))<=2


def test_incoming_sms_dedup_and_stop(monkeypatch):
    headers=settings(monkeypatch);client=TestClient(app)
    assert client.post('/bandwidth/sms',json=incoming()).status_code==401
    assert client.post('/bandwidth/sms',json=incoming(),headers=headers).status_code==204
    assert client.post('/bandwidth/sms',json=incoming(),headers=headers).status_code==204
    job=store.claim();assert job['payload']['provider']=='bandwidth'
    store.finish(job['id'],job['lease'],'Keep water, food and a flashlight ready.',reference_text='Long report with a website',official_context={'source':'nws'})
    box=store.outbox_for('job-'+job['id'])
    assert box['content']=='Keep water, food and a flashlight ready. Source: NWS.'
    assert store.claim() is None
    assert client.post('/bandwidth/sms',json=incoming('STOP','message-stop-123'),headers=headers).status_code==204
    assert providers.dispatch_outbox(box['id'])['reason']=='opted_out'
    assert client.post('/bandwidth/sms',json=incoming('another question','message-more-123'),headers=headers).status_code==204
    assert store.claim() is None


def test_uncertain_sms_submission_is_never_retried(monkeypatch):
    import httpx
    settings(monkeypatch);monkeypatch.setenv('BANDWIDTH_LIVE_SEND','true')
    store.upsert_incident('sms-drill','+15550000442','sms');bandwidth_sms.register('sms-drill')
    ident=store.queue_outbox('sms-drill','+15550000442','A short drill reply.')
    sent=[]
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            sent.append(kwargs['json']);raise httpx.ReadTimeout('simulated uncertain receipt')
    monkeypatch.setattr(bandwidth_sms,'access_token',lambda client:'test-token')
    monkeypatch.setattr(bandwidth_sms.httpx,'Client',Client)
    first=providers.dispatch_outbox(ident)
    assert first['state']=='acceptance_unknown'
    assert providers.dispatch_outbox(ident)['state']=='acceptance_unknown'
    assert len(sent)==1


def test_delivery_receipts_are_bound_to_bandwidth_intents(monkeypatch):
    headers=settings(monkeypatch);client=TestClient(app)
    store.upsert_incident('receipt-drill','+15550000442','sms');bandwidth_sms.register('receipt-drill')
    ident=store.queue_outbox('receipt-drill','+15550000442','Drill')
    body=[{'type':'message-delivered','message':{'id':'receipt-provider-123','applicationId':'sms-app','tag':ident}}]
    assert client.post('/bandwidth/sms',json=body,headers=headers).status_code==204
    assert store.outbox_for(ident)['state']=='delivered'
    body[0]['type']='message-sent'
    client.post('/bandwidth/sms',json=body,headers=headers)
    assert store.outbox_for(ident)['state']=='delivered'


def test_invalid_credentials_mark_readiness_degraded_without_spending_sms_budget(monkeypatch):
    import httpx
    settings(monkeypatch);monkeypatch.setenv('BANDWIDTH_LIVE_SEND','true')
    store.upsert_incident('auth-drill','+15550000442','sms');bandwidth_sms.register('auth-drill')
    ident=store.queue_outbox('auth-drill','+15550000442','A detailed fictional drill response. '*8)
    original_client=httpx.Client
    urls=[]
    def reject(request):
        urls.append(str(request.url))
        return httpx.Response(401,json={'error':'invalid_client','error_description':'Provided client credentials are invalid'})
    monkeypatch.setattr(bandwidth_sms.httpx,'Client',lambda **kwargs:original_client(transport=httpx.MockTransport(reject),**kwargs))
    result=providers.dispatch_outbox(ident)
    assert result['reason']=='authentication_unavailable'
    assert len(urls)==1 and urls[0].endswith('/oauth2/token')
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM bandwidth_sms_budget').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==1
    readiness=TestClient(app).get('/health').json()
    assert readiness['status']=='degraded'
    assert readiness['readiness']['bandwidth_auth']['error_code']=='invalid_client'
    assert 'test-secret' not in str(readiness)
    # A later successful probe clears the stale failure without sending an SMS.
    with original_client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'access_token':'ephemeral-test-token'}))) as client:
        assert bandwidth_sms.access_token(client)=='ephemeral-test-token'
    assert TestClient(app).get('/health').json()['status']=='ok'
