"""Authenticated, bounded Bandwidth bidirectional phone conversations."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import xml.etree.ElementTree as ET
from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from . import store, live_audio


def initialize():
    with store.connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS bandwidth_sessions (
            call_id TEXT PRIMARY KEY, incident TEXT NOT NULL, token_hash TEXT NOT NULL,
            expires REAL NOT NULL, connected INTEGER NOT NULL DEFAULT 0,
            stopped INTEGER NOT NULL DEFAULT 0)''')


def callback_auth(headers):
    secret = os.getenv('BANDWIDTH_CALLBACK_SECRET', '')
    expected = 'Basic ' + base64.b64encode(('relay-hook:' + secret).encode()).decode()
    if not secret or not secrets.compare_digest(headers.get('authorization', ''), expected):
        raise HTTPException(401, 'Invalid callback authentication')


def duration():
    return max(15, min(120, int(os.getenv('BANDWIDTH_MAX_CALL_SECONDS', '45'))))


def bxml(call_id, phone=None):
    initialize()
    root = ET.Element('Response')
    if not live_audio.ready():
        # Never incur provider TTS charges as a fallback. Without the local
        # pipeline, end promptly rather than silently billing a connected call.
        ET.SubElement(root, 'Hangup')
        return ET.tostring(root, encoding='unicode')
    # Deterministic per-call capability lets callback retries replay identical BXML
    # without storing plaintext credentials or rotating an in-flight stream.
    secret = os.getenv('BANDWIDTH_CALLBACK_SECRET', '')
    if not secret:
        raise HTTPException(503, 'Callback authentication is not configured')
    token = hmac.new(secret.encode(), call_id.encode(), hashlib.sha256).hexdigest()
    incident = 'bw-' + hashlib.sha256(call_id.encode()).hexdigest()[:28]
    store.upsert_incident(incident, phone=phone, channel='voice')
    from .bandwidth_sms import register
    register(incident)
    with store.connect() as c:
        existing = c.execute('SELECT * FROM bandwidth_sessions WHERE call_id=?', (call_id,)).fetchone()
        # Callback retries cannot rotate an active capability or start a second stream.
        if existing and (existing['stopped'] or existing['expires'] <= time.time()):
            ET.SubElement(root, 'Hangup')
            return ET.tostring(root, encoding='unicode')
        c.execute('INSERT OR IGNORE INTO bandwidth_sessions(call_id,incident,token_hash,expires) VALUES(?,?,?,?)',
                  (call_id, incident, hashlib.sha256(token.encode()).hexdigest(), time.time() + duration() + 15))
    base = os.getenv('PUBLIC_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
    destination = base.replace('https://', 'wss://').replace('http://', 'ws://') + '/bandwidth/stream/' + call_id
    ET.SubElement(root, 'StartStream', name='relay', mode='bidirectional', tracks='inbound',
                  destination=destination, destinationUsername='relay-hook', destinationPassword=token)
    # Provider-side limit remains in force even if our process hangs or disconnects.
    ET.SubElement(root, 'Pause', duration=str(duration()))
    ET.SubElement(root, 'Hangup')
    return ET.tostring(root, encoding='unicode')


def claim_stream(call_id, headers):
    initialize()
    try:
        value = headers.get('authorization', '')
        if not value.startswith('Basic '):
            return None
        user, token = base64.b64decode(value[6:], validate=True).decode().split(':', 1)
        if user != 'relay-hook':
            return None
    except (ValueError, UnicodeError):
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with store.connect() as c:
        row = c.execute('SELECT * FROM bandwidth_sessions WHERE call_id=?', (call_id,)).fetchone()
        if not row or row['expires'] <= time.time() or row['stopped'] or row['connected'] or not secrets.compare_digest(digest, row['token_hash']):
            return None
        changed = c.execute('UPDATE bandwidth_sessions SET connected=1 WHERE call_id=? AND connected=0 AND stopped=0', (call_id,)).rowcount
        return dict(row) if changed else None


class Conversation:
    def __init__(self, socket, incident):
        self.socket, self.incident = socket, incident
        self.generation = 0
        self.task = None
        self.send_lock = asyncio.Lock()
        self.audio_packets = 0
        self.clear_events = 0
        self.turns = 0

    async def send(self, message):
        async with self.send_lock:
            await self.socket.send_json(message)
            if message.get('eventType') == 'playAudio':
                self.audio_packets += 1
            elif message.get('eventType') == 'clear':
                self.clear_events += 1

    async def interrupt(self):
        self.generation += 1
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self.send({'eventType': 'clear'})

    async def speak(self, text, generation):
        from .presentation import spoken
        for sentence in re.findall(r'[^.!?]+[.!?]*', spoken(text)):
            if generation != self.generation:
                return
            audio = await asyncio.to_thread(live_audio.sentence_audio, sentence.strip())
            for offset in range(0, len(audio), 160):
                if generation != self.generation:
                    return
                packet = audio[offset:offset + 160]
                await self.send({'eventType': 'playAudio', 'media': {
                    'contentType': 'audio/pcmu', 'payload': base64.b64encode(packet).decode()}})
                # Don't fill the carrier buffer with an entire reply at once.
                await asyncio.sleep(len(packet) / 8000)

    async def answer(self, text, generation):
        try:
            from .app import emergency_response
            result = emergency_response(text)
            if not result:
                from .persona import introduction
                result = introduction(text)
            if not result and re.search(r'\b(?:text|send)\b.*\bcamera\b', text, re.I):
                phone = store.phone_for(self.incident)
                if phone and not store.opted_out(phone):
                    from .media import issue_short_camera_link
                    url = issue_short_camera_link(self.incident)
                    store.queue_outbox(self.incident, phone, 'Optional camera (10 min): ' + url,
                                       idempotency_key='camera-' + hashlib.sha256(url.encode()).hexdigest())
                    result = 'I prepared an optional camera link for text delivery. Open it only when safe and choose when to share a frame. You can continue talking without it.'
                else:
                    result = 'I cannot text a camera link because your caller number is unavailable or texts are stopped. You can continue asking questions by phone.'
            if not result and re.search(r'\blink\b.*\b(?:text|texts|sms|context)\b', text, re.I):
                try:
                    code = store.create_link(self.incident)
                    result = ('To share this call with text messages, text LINK followed by this code to the same number: '
                              + ', '.join(code) + '. Use the phone you are calling from, within five minutes.')
                except ValueError:
                    result = 'Your caller number is unavailable. You can still text RELAY separately, but I cannot securely link this call.'
            if not result and re.search(r'\b(?:contact|notify|message)\b.*\b(?:friend|family|mom|dad|trusted|contact)\b', text, re.I):
                result = ('Trusted contacts must consent first. Text CONTACTS to check enrollment, or ADD CONTACT followed by a name and phone number. '
                          'Then text REQUEST, the contact name, a colon, and your message. RELAY will ask you to approve before sending.')
            sms_consent = bool(re.search(r'\b(?:text|sms) (?:me|that|this|it)|\bsend (?:me )?(?:a )?text', text, re.I))
            if not result and sms_consent and re.search(r'\b(?:that|this|it|summary)\b', text, re.I):
                last = store.latest(self.incident)
                phone = store.phone_for(self.incident)
                if last and last['state'] == 'complete' and phone and os.getenv('BANDWIDTH_LIVE_SEND', 'false').lower() == 'true':
                    if store.opted_out(phone):
                        result = 'Texts are stopped for your number. Text START to RELAY to enable them again.'
                    else:
                        store.queue_outbox(self.incident, phone, last['result'], idempotency_key=last['id'] + '-voice-request')
                        result = 'I prepared that summary for text delivery. It may take a moment to arrive.'
            if not result:
                job_id = store.enqueue(self.incident, 'message', {'text': text, 'channel': 'voice',
                    'sms_consent': sms_consent, 'provider': 'bandwidth'})
                deadline = time.monotonic() + 35
                while generation == self.generation and time.monotonic() < deadline:
                    job = store.job_for(self.incident, job_id)
                    if job['state'] == 'complete':
                        result = job['result']
                        break
                    if job['state'] == 'failed':
                        break
                    await asyncio.sleep(.2)
                if not result:
                    result = 'I could not verify a reply. Please try again. If there is immediate danger, call your local emergency number directly.'
            if generation == self.generation:
                await self.speak(result, generation)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep transcripts, credentials and model internals out of logs.
            store.add_event(self.incident, 'voice_processing_error', {'available': False})

    async def turn(self, text):
        self.turns += 1
        await self.interrupt()
        self.task = asyncio.create_task(self.answer(text, self.generation))

    async def close(self):
        self.generation += 1
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


def install_bandwidth(app):
    @app.post('/bandwidth/voice')
    async def incoming(request: Request):
        callback_auth(request.headers)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, 'Expected a voice event')
        call_id = str(body.get('callId', ''))
        if not re.fullmatch(r'c-[a-zA-Z0-9_-]{5,100}', call_id):
            raise HTTPException(422, 'Invalid call identifier')
        if str(body.get('accountId')) != os.getenv('BANDWIDTH_ACCOUNT_ID'):
            raise HTTPException(403, 'Wrong account')
        expected_app = os.getenv('BANDWIDTH_VOICE_APPLICATION_ID')
        if expected_app and body.get('applicationId') != expected_app:
            raise HTTPException(403, 'Wrong voice application')
        if body.get('eventType') not in ('initiate', 'answer'):
            return Response('<Response/>', media_type='application/xml')
        phone = body.get('to') if body.get('eventType') == 'answer' or body.get('direction') == 'outbound' else body.get('from')
        if not isinstance(phone, str) or not re.fullmatch(r'\+1\d{10}', phone):
            phone = None
        return Response(bxml(call_id, phone), media_type='application/xml')

    @app.post('/bandwidth/status')
    async def status(request: Request):
        callback_auth(request.headers)
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(422, 'Expected a voice event')
        if str(body.get('accountId')) != os.getenv('BANDWIDTH_ACCOUNT_ID'):
            raise HTTPException(403, 'Wrong account')
        initialize()
        if body.get('eventType') == 'disconnect':
            with store.connect() as c:
                row = c.execute('SELECT incident FROM bandwidth_sessions WHERE call_id=?', (body.get('callId'),)).fetchone()
                c.execute('UPDATE bandwidth_sessions SET stopped=1 WHERE call_id=?', (body.get('callId'),))
            if row:
                store.add_event(row['incident'], 'carrier_disconnect', {'cause': str(body.get('cause', 'unknown'))[:80], 'error_code': body.get('errorCode')})
        return Response(status_code=204)

    @app.websocket('/bandwidth/stream/{call_id}')
    async def stream(socket: WebSocket, call_id: str):
        session = claim_stream(call_id, socket.headers)
        if not session:
            await socket.close(code=1008)
            return
        await socket.accept()
        conversation = Conversation(socket, session['incident'])
        try:
            async with asyncio.timeout(duration()):
                raw_start = await socket.receive_text()
                if len(raw_start) > 12000:
                    raise ValueError('Oversized start packet')
                start = json.loads(raw_start)
                metadata = start.get('metadata', {})
                if (start.get('eventType') != 'start' or metadata.get('callId') != call_id
                        or str(metadata.get('accountId')) != os.getenv('BANDWIDTH_ACCOUNT_ID')):
                    await socket.close(code=1008)
                    return
                recognizer = await asyncio.to_thread(live_audio.Recognizer)
                from .persona import VOICE_GREETING
                conversation.task = asyncio.create_task(conversation.speak(VOICE_GREETING, conversation.generation))
                last_sequence = 0
                total_bytes = 0
                while True:
                    raw = await socket.receive_text()
                    if len(raw) > 12000:
                        raise ValueError('Oversized media packet')
                    message = json.loads(raw)
                    if message.get('eventType') == 'stop':
                        break
                    if message.get('eventType') != 'media' or message.get('track') != 'inbound':
                        continue
                    sequence = int(message.get('sequenceNumber', 0))
                    if sequence <= last_sequence:
                        continue
                    last_sequence = sequence
                    audio = base64.b64decode(message.get('payload', ''), validate=True)
                    if not 0 < len(audio) <= 8000:
                        raise ValueError('Invalid audio size')
                    total_bytes += len(audio)
                    if total_bytes > (duration() + 2) * 8000:
                        raise ValueError('Audio duration exceeded')
                    event = await asyncio.to_thread(recognizer.feed, audio)
                    if event['speech_start']:
                        await conversation.interrupt()
                    if event['final'] and event['text']:
                        await conversation.turn(event['text'])
        except (WebSocketDisconnect, TimeoutError, ValueError):
            pass
        except Exception:
            store.add_event(session['incident'], 'voice_stream_error', {'available': False})
        finally:
            await conversation.close()
            store.add_event(session['incident'], 'phone_stream_summary', {
                'audio_packets': conversation.audio_packets, 'interruptions': conversation.clear_events,
                'recognized_turns': conversation.turns})
            with store.connect() as c:
                c.execute('UPDATE bandwidth_sessions SET stopped=1 WHERE call_id=?', (call_id,))
            try:
                await socket.close()
            except RuntimeError:
                pass
