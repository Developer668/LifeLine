"""Explicitly approved trusted-contact requests. Never dispatches emergency services.
Enrollment links must be shared by the enrolling user; the service does not send invitations.
Participant acknowledgements mean receipt, never completed assistance.
"""
import hashlib
import os
import re
import secrets
import time
import uuid
from typing import Literal
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from . import providers, store


def initialize():
    with store.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS trusted_contacts (
          id TEXT PRIMARY KEY, owner_incident TEXT NOT NULL, name TEXT NOT NULL,
          phone TEXT NOT NULL, consent TEXT NOT NULL, token_hash TEXT NOT NULL,
          token_expires REAL NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS contact_requests (
          id TEXT PRIMARY KEY, incident TEXT NOT NULL, contact_id TEXT NOT NULL,
          fallback_id TEXT, state TEXT NOT NULL, message TEXT NOT NULL,
          token_hash TEXT NOT NULL, token_expires REAL NOT NULL,
          fallback_used INTEGER NOT NULL DEFAULT 0, parent_id TEXT UNIQUE,
          delivery_state TEXT NOT NULL, created REAL NOT NULL,
          idempotency_key TEXT NOT NULL, UNIQUE(incident,idempotency_key));
        ''')


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def require_admin(request):
    expected = os.getenv('DEMO_TOKEN','')
    if not expected or not secrets.compare_digest(request.headers.get('authorization',''),'Bearer '+expected):
        raise HTTPException(401,'Unauthorized')


def public():
    return os.getenv('PUBLIC_BASE_URL','http://127.0.0.1:8000').rstrip('/')


def enroll(incident, name, phone):
    if not store.incident_info(incident):
        raise HTTPException(404,'Incident not found')
    if not re.fullmatch(r'\+[1-9]\d{7,14}',phone):
        raise HTTPException(422,'Use an international phone number')
    ident, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
    expires = time.time()+86400
    with store.connect() as c:
        c.execute('INSERT INTO trusted_contacts VALUES(?,?,?,?,?,?,?,?)',
            (ident,incident,name[:80],phone,'pending',digest(token),expires,time.time()))
    return {'id':ident,'state':'pending','enrollment_token':token,'expires_at':expires,
        'consent_url':public()+f'/contacts/{ident}/consent?token={token}','notice':'Share this enrollment token privately with the participant. No invitation has been sent.'}


def consent(ident, token, accept):
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        contact = c.execute('SELECT * FROM trusted_contacts WHERE id=?',(ident,)).fetchone()
        if not contact or not secrets.compare_digest(contact['token_hash'],digest(token)) or contact['token_expires'] < time.time():
            raise HTTPException(403,'Invalid or expired enrollment token')
        state = 'enrolled' if accept else 'revoked'
        c.execute('UPDATE trusted_contacts SET consent=? WHERE id=?',(state,ident))
        c.execute('COMMIT')
    return {'state':state}


def _create(incident, contact_id, fallback_id, message, key, parent_id=None):
    """Commit before provider I/O. Indeterminate sends are never retried blindly."""
    ident, token = str(uuid.uuid4()), secrets.token_urlsafe(32)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        existing = c.execute('SELECT id,state,delivery_state FROM contact_requests WHERE incident=? AND idempotency_key=?',(incident,key)).fetchone()
        if existing:
            c.execute('COMMIT')
            return dict(existing)
        contact = c.execute('SELECT * FROM trusted_contacts WHERE id=? AND owner_incident=? AND consent=\'enrolled\'',(contact_id,incident)).fetchone()
        if not contact:
            raise HTTPException(409,'Contact has not consented for this incident')
        if fallback_id:
            fallback = c.execute('SELECT * FROM trusted_contacts WHERE id=? AND owner_incident=? AND consent=\'enrolled\'',(fallback_id,incident)).fetchone()
            if not fallback or fallback_id == contact_id:
                raise HTTPException(409,'Fallback must be a separate enrolled participant')
        c.execute('INSERT INTO contact_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident,incident,contact_id,fallback_id,'pending',message,digest(token),time.time()+3600,0,parent_id,'sending',time.time(),key))
        c.execute('COMMIT')
    recipient_incident = 'contact-'+contact_id
    store.upsert_incident(recipient_incident,phone=contact['phone'],channel='trusted_contact')
    # The token is a bearer secret scoped to one request and expires in an hour.
    body = ('RELAY trusted-contact request: '+message[:400]+f'\nRespond: {public()}/contacts/requests/{ident}?token={token}\nAcknowledgement means receipt only. No emergency dispatch.')
    try:
        delivery = providers.send_reference(recipient_incident,body,explicit_consent=True)
        delivery_state = delivery.get('state','unknown')
    except Exception:
        delivery_state = 'unknown'
    with store.connect() as c:
        c.execute('UPDATE contact_requests SET delivery_state=? WHERE id=?',(delivery_state,ident))
    store.add_event(incident,'trusted_contact_requested',{'request_id':ident,'delivery_state':delivery_state})
    return {'id':ident,'state':'pending','delivery_state':delivery_state,'response_token':token,'expires_at':time.time()+3600}


def request_help(incident,contact_id,fallback_id,message,key,approved):
    if not approved:
        raise HTTPException(409,'Explicit approval is required')
    if not store.incident_info(incident):
        raise HTTPException(404,'Incident not found')
    return _create(incident,contact_id,fallback_id,message,key)


def respond(ident,token,decision):
    if decision not in ('acknowledged','declined','assisted'):
        raise HTTPException(422,'Invalid decision')
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM contact_requests WHERE id=?',(ident,)).fetchone()
        if not row or row['token_expires'] < time.time() or not secrets.compare_digest(row['token_hash'],digest(token)):
            raise HTTPException(403,'Invalid or expired response token')
        contact = c.execute('SELECT consent FROM trusted_contacts WHERE id=?',(row['contact_id'],)).fetchone()
        if not contact or contact['consent'] != 'enrolled':
            raise HTTPException(403,'Participant consent revoked')
        state = row['state']
        if state == decision:
            c.execute('COMMIT')
            return {'state':state,'fallback_triggered':False}
        allowed = state == 'pending' and decision in ('acknowledged','declined') or state == 'acknowledged' and decision == 'assisted'
        if not allowed:
            raise HTTPException(409,'Invalid response transition; acknowledge before marking assistance complete')
        fallback = decision == 'declined' and row['fallback_id'] and not row['fallback_used']
        c.execute('UPDATE contact_requests SET state=?,fallback_used=? WHERE id=?',(decision,1 if fallback else row['fallback_used'],ident))
        c.execute('COMMIT')
    delivery = None
    if fallback:
        try:
            delivery = _create(row['incident'],row['fallback_id'],None,row['message'],'fallback-'+ident,parent_id=ident)
        except HTTPException:
            delivery = {'state':'blocked','delivery_state':'not_authorized'}
    store.add_event(row['incident'],'trusted_contact_response',{'request_id':ident,'decision':decision,'fallback':delivery})
    return {'state':decision,'fallback_triggered':bool(fallback),'fallback':delivery,'notice':'Acknowledgement is receipt only; assistance is separately reported by the participant.'}


class Enrollment(BaseModel):
    name: str = Field(min_length=1,max_length=80)
    phone: str = Field(min_length=9,max_length=16)

class Consent(BaseModel):
    token: str = Field(min_length=20,max_length=100)
    accept: bool

class HelpRequest(BaseModel):
    contact_id: str
    fallback_id: str | None = None
    message: str = Field(min_length=1,max_length=400)
    idempotency_key: str = Field(min_length=8,max_length=100)
    approved: bool = False

class ParticipantResponse(BaseModel):
    token: str = Field(min_length=20,max_length=100)
    decision: Literal['acknowledged','declined','assisted']


def install_coordination(app):
    initialize()

    @app.post('/demo/incidents/{incident}/contacts')
    def enroll_route(incident:str,body:Enrollment,request:Request):
        require_admin(request)
        return enroll(incident,body.name,body.phone)

    @app.get('/contacts/{ident}/consent')
    def enrollment_page(ident:str,token:str):
        from fastapi.responses import HTMLResponse
        with store.connect() as c:
            contact = c.execute('SELECT token_hash,token_expires FROM trusted_contacts WHERE id=?',(ident,)).fetchone()
        if not contact or contact['token_expires'] < time.time() or not secrets.compare_digest(contact['token_hash'],digest(token)):
            raise HTTPException(403,'Invalid or expired enrollment token')
        return HTMLResponse('''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RELAY contact consent</title><body><h1>Choose whether to be a trusted contact</h1><p>With your consent, RELAY can send you check-in requests approved by the person who invited you. You are not an emergency responder. Agreeing does not promise assistance. This invitation expires after 24 hours. Keep this private link to revoke consent within that time.</p><button data-accept="true">I consent to receive requests</button> <button data-accept="false">Decline or revoke consent</button><p id="status" role="status"></p><script>for(const b of document.querySelectorAll('button'))b.onclick=async()=>{try{let r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:new URLSearchParams(location.search).get('token'),accept:b.dataset.accept==='true'})});let d=await r.json();document.querySelector('#status').textContent=r.ok?d.state:(d.detail||'Unable to save consent');}catch(e){document.querySelector('#status').textContent='Network unavailable. Please try again.'}};</script></body></html>''',headers={'Referrer-Policy':'no-referrer','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.post('/contacts/{ident}/consent')
    def consent_route(ident:str,body:Consent):
        return consent(ident,body.token,body.accept)

    @app.post('/demo/incidents/{incident}/contact-requests')
    def request_route(incident:str,body:HelpRequest,request:Request):
        require_admin(request)
        return request_help(incident,body.contact_id,body.fallback_id,body.message,body.idempotency_key,body.approved)

    @app.get('/demo/incidents/{incident}/contacts')
    def list_route(incident:str,request:Request):
        require_admin(request)
        with store.connect() as c:
            contacts = [dict(r) for r in c.execute('SELECT id,name,consent FROM trusted_contacts WHERE owner_incident=?',(incident,))]
            requests = [dict(r) for r in c.execute('SELECT id,contact_id,fallback_id,state,delivery_state,fallback_used FROM contact_requests WHERE incident=?',(incident,))]
        return {'contacts':contacts,'requests':requests}

    @app.post('/contacts/requests/{ident}/respond')
    def response_route(ident:str,body:ParticipantResponse):
        return respond(ident,body.token,body.decision)

    @app.get('/contacts/requests/{ident}')
    def participant_page(ident:str,token:str):
        from fastapi.responses import HTMLResponse
        # Validate before rendering; never place private message in HTML.
        with store.connect() as c:
            row = c.execute('SELECT token_hash,token_expires FROM contact_requests WHERE id=?',(ident,)).fetchone()
        if not row or row['token_expires'] < time.time() or not secrets.compare_digest(row['token_hash'],digest(token)):
            raise HTTPException(403,'Invalid or expired token')
        return HTMLResponse('''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RELAY contact response</title><body><h1>RELAY trusted-contact request</h1><p>Acknowledging confirms receipt only. Mark assistance complete only after helping. RELAY does not dispatch emergency services.</p><button data-decision="acknowledged">Acknowledge receipt</button> <button data-decision="declined">Decline</button> <button data-decision="assisted">I have provided assistance</button><p id="status" role="status"></p><script>for(const b of document.querySelectorAll('button'))b.onclick=async()=>{try{let r=await fetch(location.pathname+'/respond',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:new URLSearchParams(location.search).get('token'),decision:b.dataset.decision})});let d=await r.json();document.querySelector('#status').textContent=r.ok?d.state:(d.detail||'Unable to respond');}catch(e){document.querySelector('#status').textContent='Network unavailable. Please try again.'}};</script></body></html>''',headers={'Referrer-Policy':'no-referrer','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
