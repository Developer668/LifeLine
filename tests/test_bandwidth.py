import asyncio
import base64
import xml.etree.ElementTree as ET
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from relay import bandwidth, live_audio, store
from relay.app import app


def setup(monkeypatch):
    monkeypatch.setenv('BANDWIDTH_CALLBACK_SECRET', 'test-callback-secret')
    monkeypatch.setenv('BANDWIDTH_ACCOUNT_ID', '1234')
    monkeypatch.setenv('BANDWIDTH_VOICE_APPLICATION_ID', 'app-test')
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://example.test')
    monkeypatch.setenv('BANDWIDTH_MAX_CALL_SECONDS', '45')
    monkeypatch.setattr(live_audio, 'ready', lambda: True)
    auth = {'Authorization': 'Basic ' + base64.b64encode(b'relay-hook:test-callback-secret').decode()}
    event = {'callId': 'c-test-call-12345', 'eventType': 'initiate', 'accountId': '1234', 'applicationId': 'app-test'}
    return TestClient(app), auth, event


def test_callback_auth_replay_and_cost_bound(monkeypatch):
    client, auth, event = setup(monkeypatch)
    assert client.post('/bandwidth/voice', json=event).status_code == 401
    assert client.post('/bandwidth/voice', json={**event, 'accountId': 'wrong'}, headers=auth).status_code == 403
    reply = client.post('/bandwidth/voice', json=event, headers=auth)
    assert reply.status_code == 200
    root = ET.fromstring(reply.text)
    stream = root.find('StartStream')
    assert stream.attrib['mode'] == 'bidirectional'
    assert stream.attrib['tracks'] == 'inbound'
    assert root.find('Pause').attrib['duration'] == '45'
    assert root.find('Hangup') is not None
    assert client.post('/bandwidth/voice', json=event, headers=auth).text == reply.text
    assert client.post('/bandwidth/status', json={**event, 'eventType': 'disconnect'}, headers=auth).status_code == 204
    assert ET.fromstring(client.post('/bandwidth/voice', json=event, headers=auth).text).find('StartStream') is None


def test_outbound_answer_binds_callee_even_without_direction_field(monkeypatch):
    client, auth, event = setup(monkeypatch)
    reply = client.post('/bandwidth/voice', headers=auth, json={**event, 'eventType': 'answer',
        'from': '+15550000131', 'to': '+15550000442'})
    assert reply.status_code == 200
    import hashlib
    incident = 'bw-' + hashlib.sha256(event['callId'].encode()).hexdigest()[:28]
    assert store.phone_for(incident) == '+15550000442'


def test_phone_path_never_requests_paid_speech_features(monkeypatch):
    client, auth, event = setup(monkeypatch)
    root = ET.fromstring(client.post('/bandwidth/voice', json=event, headers=auth).text)
    assert {child.tag for child in root} == {'StartStream', 'Pause', 'Hangup'}
    monkeypatch.setattr(live_audio, 'ready', lambda: False)
    event['callId'] = 'c-local-unavailable-123'
    root = ET.fromstring(client.post('/bandwidth/voice', json=event, headers=auth).text)
    assert [child.tag for child in root] == ['Hangup']


def test_websocket_auth_metadata_and_single_use(monkeypatch):
    client, auth, event = setup(monkeypatch)
    reply = ET.fromstring(client.post('/bandwidth/voice', json=event, headers=auth).text)
    stream = reply.find('StartStream').attrib
    socket_auth = {'Authorization': 'Basic ' + base64.b64encode(('relay-hook:' + stream['destinationPassword']).encode()).decode()}
    path = '/bandwidth/stream/' + event['callId']
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path):
            pass
    with client.websocket_connect(path, headers=socket_auth) as socket:
        socket.send_json({'eventType': 'start', 'metadata': {'callId': event['callId'], 'accountId': 'wrong'}})
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path, headers=socket_auth):
            pass


def test_inbound_speech_clears_playback_without_wav(monkeypatch):
    client, auth, event = setup(monkeypatch)
    monkeypatch.setattr(live_audio, 'sentence_audio', lambda text: b'\xff' * 160)
    class ASR:
        def feed(self, audio):
            return {'speech_start': True, 'final': True, 'text': 'Someone is not breathing'}
    monkeypatch.setattr(live_audio, 'Recognizer', ASR)
    root = ET.fromstring(client.post('/bandwidth/voice', json=event, headers=auth).text)
    token = root.find('StartStream').attrib['destinationPassword']
    headers = {'Authorization': 'Basic ' + base64.b64encode(('relay-hook:' + token).encode()).decode()}
    with client.websocket_connect('/bandwidth/stream/' + event['callId'], headers=headers) as socket:
        socket.send_json({'eventType': 'start', 'metadata': {'callId': event['callId'], 'accountId': '1234'}})
        greeting = socket.receive_json()
        assert greeting['eventType'] == 'playAudio'
        assert greeting['media']['contentType'] == 'audio/pcmu'
        socket.send_json({'eventType': 'media', 'track': 'inbound', 'sequenceNumber': '1', 'payload': base64.b64encode(b'\xff' * 160).decode()})
        assert socket.receive_json() == {'eventType': 'clear'}
        assert socket.receive_json()['eventType'] == 'playAudio'
        socket.send_json({'eventType': 'stop'})
    assert store.claim() is None  # critical response bypasses the inference queue


@pytest.mark.asyncio
async def test_interruption_prevents_obsolete_audio(monkeypatch):
    sent = []
    class Socket:
        async def send_json(self, item): sent.append(item)
    monkeypatch.setattr(live_audio, 'sentence_audio', lambda text: b'\xff' * 8000)
    c = bandwidth.Conversation(Socket(), 'test-incident')
    c.task = asyncio.create_task(c.speak('A longer reply.', c.generation))
    await asyncio.sleep(.03)
    await c.interrupt()
    count = len(sent)
    await asyncio.sleep(.05)
    assert len(sent) == count
    assert sent[-1] == {'eventType': 'clear'}
    await c.speak('This is obsolete.', c.generation - 1)
    assert len(sent) == count


@pytest.mark.asyncio
async def test_noise_does_not_cancel_cold_synthesis_but_recognized_speech_does(monkeypatch):
    import time
    sent=[]
    class Socket:
        async def send_json(self,item):sent.append(item)
    def cold_voice(text):
        time.sleep(.12)
        return b'\xfe' * 8000
    monkeypatch.setattr(live_audio,'sentence_audio',cold_voice)
    c=bandwidth.Conversation(Socket(),'noise-drill')
    c.task=asyncio.create_task(c.speak('A greeting that should be audible.',c.generation))
    await asyncio.sleep(.01)
    for _ in range(4):
        await c.recognized({'speech_start':True,'final':False,'text':''})
        await c.recognized({'speech_start':False,'final':False,'text':'the'})
    await asyncio.sleep(.17)
    assert any(e['eventType']=='playAudio' for e in sent)
    assert not any(e['eventType']=='clear' for e in sent)
    assert c.task and not c.task.done()
    await asyncio.sleep(.3)
    await c.recognized({'speech_start':False,'final':False,'text':'I need help'})
    assert sent[-1]=={'eventType':'clear'}
    assert c.task is None
    count=len(sent)
    await c.recognized({'speech_start':False,'final':False,'text':'I need help now'})
    assert len(sent)==count  # only one interruption per partial utterance


@pytest.mark.asyncio
async def test_partial_recognition_does_not_cancel_inference(monkeypatch):
    sent=[]
    class Socket:
        async def send_json(self,item):sent.append(item)
    c=bandwidth.Conversation(Socket(),'thinking-drill')
    async def pending_answer():
        await asyncio.sleep(20)
    c.task=asyncio.create_task(pending_answer())
    c.phase='thinking'
    await c.recognized({'speech_start':True,'speech_confirmed':True,'final':False,'text':'some background phrase'})
    assert c.task and not c.task.done()
    assert c.generation==0 and not sent
    await c.close()


def test_local_voice_startup_failure_is_visible_and_has_no_private_details(monkeypatch):
    monkeypatch.setattr(live_audio,'ready',lambda:True)
    def failed_warmup():raise RuntimeError('private model configuration must not appear publicly')
    monkeypatch.setattr(live_audio,'warmup',failed_warmup)
    with TestClient(app) as client:
        response=client.get('/health').json()
        assert response['status']=='degraded'
        assert response['readiness']['voice_audio']['error_code']=='RuntimeError'
        assert 'private model configuration' not in str(response)


def test_repetitive_or_impossibly_fast_transcripts_are_not_sent_to_inference():
    from relay.live_audio import reliable_transcript
    assert reliable_transcript('What should I put in an emergency kit?',4)
    assert reliable_transcript('Someone is not breathing',1)
    assert reliable_transcript('Someone is not breathing. '*8,4)
    assert reliable_transcript('Help me please. '*8,3)
    assert not reliable_transcript("I don't think that's the case. "*12,10)
    assert not reliable_transcript('Okay. '*30,3)
    assert not reliable_transcript(' '.join('word'+str(i) for i in range(80)),2)
