"""Deterministic phone/SMS workflows. Never route consent through model wording."""
import hashlib
import json
import re
import secrets
import time
from fastapi import HTTPException
from . import store


def initialize():
    with store.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS phone_actions (
          digest TEXT PRIMARY KEY, phone TEXT NOT NULL, kind TEXT NOT NULL,
          data TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS phone_command_attempts (
          phone TEXT PRIMARY KEY, count INTEGER NOT NULL, window REAL NOT NULL);
        ''')


def issue(phone, kind, data, seconds=300, connection=None):
    code = secrets.token_hex(5).upper()
    values = (hashlib.sha256(code.encode()).hexdigest(), phone, kind,
              json.dumps(data), time.time() + seconds)
    sql = 'INSERT INTO phone_actions(digest,phone,kind,data,expires) VALUES(?,?,?,?,?)'
    if connection is not None:
        connection.execute(sql, values)
    else:
        initialize()
        with store.connect() as c:
            c.execute(sql, values)
    return code


def action(phone, code, kinds, consume=False):
    initialize()
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        now = time.time()
        attempt = c.execute('SELECT * FROM phone_command_attempts WHERE phone=?', (phone,)).fetchone()
        if attempt and attempt['window'] > now - 3600 and attempt['count'] >= 10:
            c.execute('ROLLBACK')
            return None
        row = c.execute('SELECT * FROM phone_actions WHERE digest=? AND phone=? AND expires>?',
                        (hashlib.sha256(code.upper().encode()).hexdigest(), phone, now)).fetchone()
        if not row or row['kind'] not in kinds or (consume and row['used']):
            count = attempt['count'] + 1 if attempt and attempt['window'] > now - 3600 else 1
            window = attempt['window'] if count > 1 else now
            c.execute('INSERT OR REPLACE INTO phone_command_attempts VALUES(?,?,?)', (phone, count, window))
            c.execute('COMMIT')
            return None
        if consume:
            c.execute('UPDATE phone_actions SET used=1 WHERE digest=?', (row['digest'],))
        c.execute('COMMIT')
        return json.loads(row['data'])


def contacts(incident):
    from .coordination import initialize as init
    init()
    with store.connect() as c:
        return [dict(r) for r in c.execute('SELECT id,name,phone,consent FROM trusted_contacts WHERE owner_incident=? ORDER BY created', (incident,))]


def handle(incident, phone, text):
    """Return direct wording for an explicit command, or None for a normal question."""
    initialize()
    from . import coordination as co
    co.initialize()
    text = text.strip()
    upper = text.upper()
    if upper == 'REVOKE ALL':
        with store.connect() as c:
            c.execute("UPDATE trusted_contacts SET consent='revoked' WHERE phone=?", (phone,))
        return 'Your trusted-contact consent is revoked. No new contact request is authorized for this phone.'
    if upper == 'HELP':
        return ('Ask a question or send a ZIP code for official alerts. Commands: CONTACTS; '
                'ADD CONTACT Name +1number; REQUEST Name: message; STATUS. '
                'Say "link my texts" during a call to share its context with SMS. STOP stops texts.')
    if upper == 'CAMERA':
        from .media import issue_short_camera_link
        return 'Optional camera (10 min): ' + issue_short_camera_link(incident)
    if upper == 'CONTACTS':
        people = contacts(incident)
        return 'Trusted contacts: ' + '; '.join(p['name'] + ' (' + p['consent'] + ')' for p in people) if people else 'No trusted contacts yet. Text ADD CONTACT Name +1number. The participant must consent before any request.'
    if upper == 'STATUS':
        with store.connect() as c:
            pending = [r['outbox_id'] for r in c.execute('SELECT m.outbox_id FROM contact_outboxes m JOIN contact_requests r ON r.id=m.request_id WHERE r.incident=?', (incident,))]
        for outbox_id in pending:
            co.sync_outbox(outbox_id)
        with store.connect() as c:
            latest = c.execute('SELECT state,delivery_state FROM contact_requests WHERE incident=? ORDER BY created DESC LIMIT 1', (incident,)).fetchone()
        if not latest:
            return 'No trusted-contact request is active. Ask a question, or text HELP for commands.'
        return 'Contact request: ' + latest['state'] + '. Delivery: ' + latest['delivery_state'] + '. Acknowledgement means receipt only.'
    match = re.fullmatch(r'ADD CONTACT\s+(.{1,40}?)\s+(\+1\d{10})', text, re.I)
    if match:
        name, target = match.groups()
        if any(p['name'].casefold() == name.casefold() for p in contacts(incident)):
            return 'That contact name is already registered. Text CONTACTS to check consent.'
        enrolled = co.enroll(incident, name, target)
        code = issue(target, 'join', {'contact_id': enrolled['id'], 'token': enrolled['enrollment_token']}, seconds=86400)
        return f'Nothing sent to {name}. Share this privately: text JOIN {code} to RELAY from the registered phone within 24 hours to consent.'
    match = re.fullmatch(r'(JOIN|REVOKE|APPROVE|ACK|DECLINE|ASSISTED)\s+([A-Fa-f0-9]{10})', text, re.I)
    if match:
        command, code = match.groups();command = command.upper()
        kind = 'join' if command in ('JOIN', 'REVOKE') else 'approve' if command == 'APPROVE' else 'reply'
        data = action(phone, code, [kind])
        if not data:
            return 'That code is invalid, expired, or belongs to another phone. Ask for a new code.'
        try:
            if kind == 'join':
                result = co.consent(data['contact_id'], data['token'], command == 'JOIN')
                return 'Contact consent: ' + result['state'] + '. To withdraw, text REVOKE followed by the same code.'
            if kind == 'approve':
                result = co.request_help(data['incident'], data['contact_id'], data.get('fallback_id'), data['message'],
                                         'phone-' + hashlib.sha256(code.upper().encode()).hexdigest(), True)
                return 'Request delivery: ' + result['delivery_state'] + '. This does not confirm the person has read it or provided help. Text STATUS for updates.'
            decision = {'ACK': 'acknowledged', 'DECLINE': 'declined', 'ASSISTED': 'assisted'}[command]
            result = co.respond(data['request_id'], data['token'], decision)
            return 'Recorded: ' + result['state'] + ('. One approved fallback request was prepared.' if result['fallback_triggered'] else '.')
        except HTTPException as error:
            return str(error.detail)
    match = re.fullmatch(r'REQUEST\s+(.{1,40}?)(?:\s+FALLBACK\s+(.{1,40}?))?\s*:\s*(.{1,240})', text, re.I)
    if match:
        name, fallback_name, message = match.groups()
        people = {p['name'].casefold(): p for p in contacts(incident) if p['consent'] == 'enrolled'}
        person = people.get(name.strip().casefold());fallback = people.get((fallback_name or '').strip().casefold())
        if not person or (fallback_name and (not fallback or fallback['id'] == person['id'])):
            return 'Choose consenting contacts with different names for primary and fallback. Text CONTACTS to check enrollment.'
        code = issue(phone, 'approve', {'incident': incident, 'contact_id': person['id'], 'fallback_id': fallback['id'] if fallback else None, 'message': message})
        return f'Nothing sent. Request for {person["name"]}: {message}. ' + (f'If declined, contact {fallback["name"]} once. ' if fallback else '') + f'Text APPROVE {code} within 5 minutes to authorize.'
    if re.match(r'^(?:ADD CONTACT|REQUEST|JOIN|REVOKE|APPROVE|ACK|DECLINE|ASSISTED)\b', upper):
        return 'Check the command format. Text HELP, ADD CONTACT Name +1number, REQUEST Name: message, or a reply command followed by its 10-character code.'
    return None
