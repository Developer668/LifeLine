"""Small durable job/incident store, intentionally independent of GPU lifetime."""
import json
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
        return dict(row) | {"lease": lease, "payload": json.loads(row["payload"])}


def finish(job_id, lease, result=None, error=None):
    now = time.time()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["state"] != "leased" or not secrets.compare_digest(row["lease"] or "", lease) or row["leased_until"] < now:
            c.execute("ROLLBACK")
            return None
        state = "failed" if error is not None else "complete"
        content = (error or result or "")[:1800]
        c.execute("UPDATE jobs SET state=?,result=?,lease=NULL,leased_until=NULL,updated=? WHERE id=?",
                  (state, content, now, job_id))
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
            ident, incident, recipient, content[:1000], "simulated", time.time(), None))
    return ident


def update_outbox(ident, state, provider_id=None):
    with connect() as c:
        c.execute("UPDATE outbox SET state=?,provider_id=? WHERE id=?", (state, provider_id, ident))


def phone_for(incident):
    with connect() as c:
        row = c.execute("SELECT phone FROM incidents WHERE id=?", (incident,)).fetchone()
        return row["phone"] if row else None
