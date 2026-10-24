"""Small durable job/incident store, intentionally independent of GPU lifetime."""
import json
import hashlib
from contextlib import contextmanager
import os
import secrets
import sqlite3
import time
import uuid

DB = lambda: os.getenv("RELAY_DB", "relay.db")


@contextmanager
def connect():
    conn = sqlite3.connect(DB(), timeout=10, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        yield conn
    finally:
        conn.close()


def initialize():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS incidents(
            id TEXT PRIMARY KEY, phone TEXT, channel TEXT,
            created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS associations(
            sms_incident TEXT PRIMARY KEY, voice_incident TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS link_codes(
            digest TEXT PRIMARY KEY, incident TEXT NOT NULL, phone TEXT NOT NULL,
            expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS voice_responses(
            turn_key TEXT PRIMARY KEY, response TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS preferences(phone TEXT PRIMARY KEY, opted_out INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS incidents_phone_idx ON incidents(phone);
        CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY, incident TEXT NOT NULL, kind TEXT NOT NULL,
            payload TEXT NOT NULL, result TEXT, state TEXT NOT NULL,
            lease TEXT, leased_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
            created REAL NOT NULL, updated REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(state, created);
        CREATE TABLE IF NOT EXISTS events(
            id TEXT PRIMARY KEY, incident TEXT NOT NULL, kind TEXT NOT NULL,
            body TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(
            id TEXT PRIMARY KEY, incident TEXT NOT NULL, recipient TEXT,
            content TEXT NOT NULL, state TEXT NOT NULL,
            created REAL NOT NULL, provider_id TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS jobs_dedup_idx ON jobs(incident, kind, payload, state)
        WHERE state IN ('queued','leased');
        """)


def upsert_incident(incident, phone=None, channel="sms"):
    now = time.time()
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO incidents VALUES(?,?,?,?,?)",
                  (incident, phone, channel, now, now))
        c.execute("UPDATE incidents SET updated=? WHERE id=?", (now, incident))


def add_event(incident, kind, body, event_id=None):
    with connect() as c:
        row = c.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)",
                  (event_id or str(uuid.uuid4()), incident, kind, json.dumps(body), time.time()))
        return bool(row.rowcount)


def enqueue(incident, kind, payload):
    now = time.time()
    serialized = json.dumps(payload, sort_keys=True)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute("""SELECT id FROM jobs WHERE incident=? AND kind=? AND payload=?
            AND state IN ('queued','leased')""", (incident, kind, serialized)).fetchone()
        if existing:
            c.execute("COMMIT")
            return existing["id"]
        job_id = str(uuid.uuid4())
        c.execute("""INSERT INTO jobs (id,incident,kind,payload,state,created,updated)
            VALUES (?,?,?,?,?,?,?)""", (job_id, incident, kind, serialized, "queued", now, now))
        c.execute("COMMIT")
    return job_id


def claim():
    now = time.time()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE jobs SET state='failed',result='Worker lease expired after maximum attempts.',lease=NULL,leased_until=NULL,updated=? WHERE state='leased' AND leased_until < ? AND attempts >= 3", (now, now))
        row = c.execute("""SELECT * FROM jobs WHERE
            (state='queued' OR (state='leased' AND leased_until < ? AND attempts < 3))
            AND attempts < 3
            AND NOT EXISTS (SELECT 1 FROM jobs running WHERE running.incident=jobs.incident
                AND running.state='leased' AND running.leased_until >= ? AND running.id != jobs.id)
            ORDER BY created LIMIT 1""", (now, now)).fetchone()
        if not row:
            c.execute("COMMIT")
            return None
        lease = secrets.token_urlsafe(24)
        c.execute("UPDATE jobs SET state='leased',lease=?,leased_until=?,attempts=attempts+1,updated=? WHERE id=?",
                  (lease, now+90, now, row["id"]))
        c.execute("COMMIT")
        payload = json.loads(row["payload"])
        payload['history'] = history(row['incident'], before=row['created'])
        return dict(row) | {"lease": lease, "payload": payload}


def finish(job_id, lease, result=None, error=None):
    if (result is None) == (error is None):
        return None
    now = time.time()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["state"] != "leased" or not secrets.compare_digest(row["lease"] or "", lease) or row["leased_until"] < now:
            c.execute("ROLLBACK")
            return None
        state = "failed" if error is not None else "complete"
        content = (error or result or "")[:1800]
        payload=json.loads(row['payload'])
        payload.pop('image_base64',None)
        c.execute("UPDATE jobs SET state=?,result=?,payload=?,lease=NULL,leased_until=NULL,updated=? WHERE id=?",
                  (state, content, json.dumps(payload,sort_keys=True), now, job_id))
        c.execute("COMMIT")
        return {"incident": row["incident"], "kind": row["kind"], "text": content, "state": state}


def latest(incident):
    with connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE incident=? ORDER BY created DESC LIMIT 1", (incident,)).fetchone()
        return dict(row) if row else None


def incident_info(incident):
    with connect() as c:
        i = c.execute("SELECT id,channel,created,updated FROM incidents WHERE id=?", (incident,)).fetchone()
        if not i:
            return None
        return {
            "incident": dict(i),
            "jobs": [dict(x) for x in c.execute(
                "SELECT id,kind,state,result,created,updated FROM jobs WHERE incident=? ORDER BY created", (incident,))],
            "outbox": [dict(x) for x in c.execute(
                "SELECT id,state,content,created FROM outbox WHERE incident=? ORDER BY created", (incident,))]
        }


def queue_outbox(incident, recipient, content):
    ident = str(uuid.uuid4())
    with connect() as c:
        c.execute("INSERT INTO outbox VALUES(?,?,?,?,?,?,?)", (
            ident, incident, recipient, content[:1000], "prepared", time.time(), None))
    return ident


def update_outbox(ident, state, provider_id=None):
    with connect() as c:
        c.execute("UPDATE outbox SET state=?,provider_id=COALESCE(?,provider_id) WHERE id=? AND state NOT IN ('delivered','failed')", (state, provider_id, ident))


def phone_for(incident):
    with connect() as c:
        row = c.execute("SELECT phone FROM incidents WHERE id=?", (incident,)).fetchone()
        return row["phone"] if row else None


def job_for(incident, job_id):
    with connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=? AND incident=?", (job_id, incident)).fetchone()
        return dict(row) if row else None


def history(incident, before=None):
    with connect() as c:
        rows = c.execute("SELECT payload,result FROM jobs WHERE incident=? AND state='complete' AND created<? ORDER BY created DESC LIMIT 6", (incident, before or time.time())).fetchall()
    messages = []
    for row in reversed(rows):
        payload = json.loads(row['payload'])
        messages.extend([{'role':'user','content':str(payload.get('text',''))[:2000]}, {'role':'assistant','content':str(row['result'] or '')[:1800]}])
    return messages


def resolve_sms(incident):
    with connect() as c:
        row=c.execute("SELECT voice_incident FROM associations WHERE sms_incident=? AND expires>?", (incident,time.time())).fetchone()
    return row['voice_incident'] if row else incident


def create_link(incident):
    phone=phone_for(incident)
    if not phone:
        raise ValueError('Caller phone unavailable')
    code=secrets.token_hex(5).upper()
    with connect() as c:
        c.execute("DELETE FROM link_codes WHERE incident=? OR expires<?", (incident,time.time()))
        c.execute("INSERT INTO link_codes VALUES(?,?,?,?,0)", (hashlib.sha256(code.encode()).hexdigest(),incident,phone,time.time()+300))
    return code


def redeem_link(sms_incident, phone, code):
    digest=hashlib.sha256(code.upper().encode()).hexdigest()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM link_codes WHERE digest=? AND used=0 AND expires>? AND phone=?', (digest,time.time(),phone)).fetchone()
        if not row:
            c.execute('ROLLBACK'); return None
        c.execute('UPDATE link_codes SET used=1 WHERE digest=?',(digest,))
        c.execute('INSERT OR REPLACE INTO associations VALUES(?,?,?)', (sms_incident,row['incident'],time.time()+3600))
        c.execute('COMMIT')
        return row['incident']


def voice_cached(key):
    with connect() as c:
        row=c.execute('SELECT response FROM voice_responses WHERE turn_key=?',(key,)).fetchone()
    return row['response'] if row else None


def cache_voice(key,response):
    with connect() as c:
        c.execute('INSERT OR IGNORE INTO voice_responses VALUES(?,?)',(key,response))


def delivery_update(provider_id, status, outbox_id=None):
    state={'accepted':'accepted_by_provider','queued':'accepted_by_provider','sending':'accepted_by_provider','sent':'accepted_by_provider','delivered':'delivered','read':'delivered','undelivered':'failed','failed':'failed','canceled':'failed'}.get(status)
    if not state:
        return False
    with connect() as c:
        if outbox_id:
            row=c.execute("UPDATE outbox SET state=?,provider_id=? WHERE id=? AND (provider_id=? OR provider_id IS NULL) AND state NOT IN ('delivered','failed','simulated')",(state,provider_id,outbox_id,provider_id))
        else:
            row=c.execute("UPDATE outbox SET state=? WHERE provider_id=? AND state NOT IN ('delivered','failed','simulated')",(state,provider_id))
    return bool(row.rowcount)


def set_optout(phone, value):
    with connect() as c:
        c.execute('INSERT OR REPLACE INTO preferences VALUES(?,?)',(phone,int(value)))


def opted_out(phone):
    with connect() as c:
        row=c.execute('SELECT opted_out FROM preferences WHERE phone=?',(phone,)).fetchone()
    return bool(row and row['opted_out'])
