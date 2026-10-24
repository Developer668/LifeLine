"""Direct, plain-text Bandwidth Build SMS with durable, bounded sends."""
import hashlib
import os
import re
import textwrap
import time
import unicodedata
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response
from . import store
from .bandwidth import callback_auth


def initialize():
    with store.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS bandwidth_incidents (incident TEXT PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS bandwidth_sms_budget (id TEXT PRIMARY KEY, scheduled REAL NOT NULL)')


def register(incident):
    initialize()
    with store.connect() as c:
        c.execute('INSERT OR IGNORE INTO bandwidth_incidents VALUES(?)', (incident,))


def owns(incident):
    initialize()
    with store.connect() as c:
        return bool(c.execute('SELECT 1 FROM bandwidth_incidents WHERE incident=?', (incident,)).fetchone())


def sms_parts(text):
    text = text.translate(str.maketrans({'’': "'", '“': '"', '”': '"', '–': '-', '—': '-'}))
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    text = re.sub(r'\s+', ' ', text).strip()
    if 0 < len(text) <= 129:
        return [text]
    # A bounded response goes directly to SMS; never replace it with a website.
    parts = textwrap.wrap(text, width=123, break_long_words=False, break_on_hyphens=False)
    if len(parts) > 4 or any(len(p) > 123 for p in parts):
        parts = ['RELAY: This answer needs more detail than a short text can hold. Call RELAY for a spoken reply.',
                 'For immediate danger, call your local emergency number directly. RELAY cannot dispatch help.']
    if len(parts) <= 1:
        return parts or ['RELAY could not prepare a reply. Please try again.']
    return [f'{i + 1}/{len(parts)} {part}' for i, part in enumerate(parts)]


def access_token(client):
    response = client.post('https://api.bandwidth.com/api/v1/oauth2/token',
        auth=(os.environ['BANDWIDTH_CLIENT_ID'], os.environ['BANDWIDTH_CLIENT_SECRET']),
        data={'grant_type': 'client_credentials'})
    response.raise_for_status()
    return response.json()['access_token']


def reserve(ident):
    initialize()
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        now = time.time()
        count = c.execute('SELECT COUNT(*) FROM bandwidth_sms_budget WHERE scheduled>?', (now - 86400,)).fetchone()[0]
        if count >= 24:
            c.execute('ROLLBACK')
            return None
        last = c.execute('SELECT MAX(scheduled) FROM bandwidth_sms_budget').fetchone()[0]
        slot = max(now, (last or 0) + 10.2)
        c.execute('INSERT INTO bandwidth_sms_budget VALUES(?,?)', (ident, slot))
        c.execute('COMMIT')
    return slot


def dispatch(row):
    ident = row['id']
    if os.getenv('BANDWIDTH_LIVE_SEND', 'false').lower() != 'true':
        store.update_outbox(ident, 'simulated')
        return {'state': 'simulated', 'id': ident}
    required = ('BANDWIDTH_CLIENT_ID', 'BANDWIDTH_CLIENT_SECRET', 'BANDWIDTH_ACCOUNT_ID',
                'BANDWIDTH_NUMBER', 'BANDWIDTH_MESSAGING_APPLICATION_ID')
    if not all(os.getenv(k) for k in required):
        store.update_outbox(ident, 'failed')
        return {'state': 'failed', 'id': ident, 'reason': 'configuration_missing'}
    parts = sms_parts(row['content'])
    # Every part gets its own durable claim and receipt. Never retry an uncertain send.
    for index, part in enumerate(parts[1:], 2):
        store.queue_outbox(row['incident'], row['recipient'], part, idempotency_key=ident + '-part-' + str(index))
    slot = reserve(ident)
    if slot is None:
        store.update_outbox(ident, 'failed')
        return {'state': 'failed', 'id': ident, 'reason': 'daily_demo_limit'}
    time.sleep(max(0, slot - time.time()))
    if store.opted_out(row['recipient']):
        store.update_outbox(ident, 'failed')
        return {'state': 'failed', 'id': ident, 'reason': 'opted_out'}
    submitted = False
    try:
        with httpx.Client(timeout=8) as client:
            token = access_token(client)
            submitted = True
            response = client.post('https://api.bandwidth.com/v1/build/accounts/' + os.environ['BANDWIDTH_ACCOUNT_ID'] + '/messages',
                headers={'Authorization': 'Bearer ' + token}, json={
                    'applicationId': os.environ['BANDWIDTH_MESSAGING_APPLICATION_ID'],
                    'from': os.environ['BANDWIDTH_NUMBER'], 'to': [row['recipient']],
                    'text': parts[0], 'tag': ident})
            if 400 <= response.status_code < 500:
                store.update_outbox(ident, 'failed')
                return {'state': 'failed', 'id': ident, 'reason': 'carrier_rejected', 'http_status': response.status_code}
            response.raise_for_status()
            provider_id = response.json()['id']
            store.update_outbox(ident, 'accepted_by_provider', provider_id)
            return {'state': store.outbox_for(ident)['state'], 'id': ident}
    except Exception:
        state = 'acceptance_unknown' if submitted else 'failed'
        store.update_outbox(ident, state)
        return {'state': state, 'id': ident}


def install_bandwidth_sms(app):
    @app.post('/bandwidth/sms')
    async def callback(request: Request):
        callback_auth(request.headers)
        events = await request.json()
        if not isinstance(events, list) or len(events) > 50:
            raise HTTPException(422, 'Expected a bounded event list')
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get('message'), dict):
                raise HTTPException(422, 'Expected messaging events')
            message = event.get('message', {})
            if message.get('applicationId') != os.getenv('BANDWIDTH_MESSAGING_APPLICATION_ID'):
                raise HTTPException(403, 'Wrong messaging application')
            kind, mid = event.get('type'), str(message.get('id', ''))
            if not re.fullmatch(r'[a-zA-Z0-9_-]{5,100}', mid):
                raise HTTPException(422, 'Invalid message identifier')
            if kind == 'message-received':
                phone = str(message.get('from', ''))
                if not re.fullmatch(r'\+1\d{10}', phone) or message.get('owner') != os.getenv('BANDWIDTH_NUMBER'):
                    raise HTTPException(422, 'Invalid sender or destination')
                incident = 'bw-sms-' + hashlib.sha256(phone.encode()).hexdigest()[:24]
                store.upsert_incident(incident, phone, 'sms');register(incident)
                text = str(message.get('text', ''))[:2000].strip()
                command = text.upper()
                if command in ('STOP', 'STOPALL', 'UNSUBSCRIBE', 'CANCEL', 'END', 'QUIT', 'START', 'UNSTOP'):
                    if store.add_event(incident, 'sms_preference', {'command': command}, event_id='bw-' + mid):
                        store.set_optout(phone, command not in ('START', 'UNSTOP'))
                    continue
                if not text or store.opted_out(phone):
                    continue
                from .app import emergency_response
                immediate = emergency_response(text)
                if immediate:
                    if store.add_event(incident, 'safety_fallback', {'emergency': True}, event_id='bw-' + mid):
                        store.queue_outbox(incident, phone, immediate, idempotency_key='bw-' + mid)
                else:
                    store.accept_sms_job(incident, 'bw-' + mid, {'message_id': mid, 'length': len(text)},
                                         {'text': text, 'channel': 'sms', 'sms_consent': True, 'provider': 'bandwidth'})
            elif kind in ('message-delivered', 'message-failed', 'message-sent', 'message-sending'):
                status = {'message-delivered': 'delivered', 'message-failed': 'failed',
                          'message-sent': 'sent', 'message-sending': 'sending'}[kind]
                tag = str(message.get('tag', ''))
                row = store.outbox_for(tag)
                # Callback can race the submit response; bind only to this provider's intent.
                if row and owns(row['incident']):
                    store.delivery_update(mid, status, tag)
        return Response(status_code=204)
