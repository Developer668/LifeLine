"""RELAY: phone-first prototype. Not an emergency dispatch service."""
import base64
import json
import hashlib
import hmac
import os
import re
import secrets
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, model_validator
from twilio.twiml.voice_response import VoiceResponse
from twilio.twiml.messaging_response import MessagingResponse
from . import store, providers

app = FastAPI(title="RELAY phone-first demo", version="0.1.0")
store.initialize()
PUBLIC = lambda: os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

EMERGENCY = re.compile(r"\b(?:not breathing|unconscious|unresponsive|can't breathe|cannot breathe|severe bleeding|heart attack|stroke|someone is dying|immediate danger)\b", re.I)
EMERGENCY_TEXT = ("If someone is in immediate danger, call 911 in the United States "
                  "or your local emergency number now. RELAY cannot contact emergency dispatch "
                  "for you, and this service is not monitored by emergency responders.")


def require_token(request, env):
    secret = os.getenv(env, "")
    value = request.headers.get("authorization", "")
    if not secret or not secrets.compare_digest(value, "Bearer " + secret):
        raise HTTPException(401, "Unauthorized")


async def twilio_form(request):
    incoming=await request.form()
    if any(len(incoming.getlist(key)) != 1 for key in incoming):
        raise HTTPException(400,'Duplicate webhook fields')
    form = dict(incoming)
    url = PUBLIC() + request.url.path + (("?" + request.url.query) if request.url.query else "")
    if not providers.validate_twilio(url, form, request.headers.get("X-Twilio-Signature", "")):
        raise HTTPException(403, "Invalid Twilio signature")
    return form


def xml(body):
    return Response(content=str(body), media_type="application/xml")


def call_incident(form):
    sid = str(form.get("CallSid", ""))
    if not re.fullmatch(r"CA[a-fA-F0-9]{32}", sid):
        raise HTTPException(422, "Invalid CallSid")
    ident = "call-" + sid
    store.upsert_incident(ident, form.get("From"), "voice")
    return ident


def text_incident(form):
    phone = str(form.get("From", ""))
    if not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
        raise HTTPException(422, "Invalid sender")
    ident = "sms-" + hashlib.sha256(phone.encode()).hexdigest()[:28]
    store.upsert_incident(ident, phone, "sms")
    return store.resolve_sms(ident)


def next_prompt(reply, turn=None):
    response = VoiceResponse()
    g = response.gather(input="speech dtmf", action=PUBLIC()+"/twilio/voice/turn?turn="+(turn or secrets.token_urlsafe(16)),
                        method="POST", speech_timeout="auto", timeout=5)
    g.say(reply[:600])
    response.say("No response received. Goodbye.")
    return xml(response)


@app.get("/health")
def health():
    return {"status": "ok", "mode": os.getenv("RELAY_MODE", "demo"),
            "readiness": {"twilio_configured": all(os.getenv(k) for k in ('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_NUMBER')),
                          "live_send_enabled": os.getenv('TWILIO_LIVE_SEND','false').lower()=='true',
                          "gpu_verified": False, "carrier_verified": False},
            "notice": "Gateway health does not establish GPU readiness."}


class DemoMessage(BaseModel):
    incident_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{3,80}$")
    text: str = Field(min_length=1, max_length=2000)


@app.post("/demo/message")
async def demo_message(request: Request, body: DemoMessage):
    require_token(request, "DEMO_TOKEN")
    store.upsert_incident(body.incident_id, channel="demo")
    if emergency_response(body.text):
        store.add_event(body.incident_id, "safety_fallback", {"emergency": True})
        return {"state": "immediate", "response": emergency_response(body.text)}
    job_id = store.enqueue(body.incident_id, "message",
        {"text": body.text, "channel": "demo", "sms_consent": False})
    return {"state": "queued", "job_id": job_id, "incident_id": body.incident_id}


@app.get("/demo/incidents/{incident}")
def demo_status(request: Request, incident: str):
    require_token(request, "DEMO_TOKEN")
    info = store.incident_info(incident)
    if not info:
        raise HTTPException(404, "Not found")
    return info


@app.post("/worker/claim")
def worker_claim(request: Request):
    require_token(request, "RELAY_WORKER_TOKEN")
    return {"job": store.claim()}


class FinishJob(BaseModel):
    lease: str = Field(min_length=12)
    result: str | None = Field(default=None, max_length=1800)
    error: str | None = Field(default=None, max_length=1800)

    @model_validator(mode='after')
    def exactly_one(self):
        if (self.result is None) == (self.error is None):
            raise ValueError('Provide exactly one result or error')
        if not (self.result or self.error or '').strip():
            raise ValueError('Response cannot be blank')
        return self


@app.post("/worker/jobs/{job_id}/complete")
def worker_complete(request: Request, job_id: str, body: FinishJob):
    require_token(request, "RELAY_WORKER_TOKEN")
    status = store.finish(job_id, body.lease, body.result, body.error)
    if not status:
        raise HTTPException(409, "Job missing, expired or already completed")
    if status["state"] in ("complete","failed"):
        with store.connect() as c:
            job = c.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        import json
        payload = json.loads(job["payload"])
        if payload.get("sms_consent") and payload.get("channel") in ("sms", "voice"):
            delivery = providers.send_reference(status["incident"], status["text"] if status["state"]=="complete" else "RELAY could not verify an answer because the information service is unavailable. For immediate danger, call your local emergency number directly.", explicit_consent=True)
            store.add_event(status["incident"], "reference_send_attempt", delivery)
    return {"state": status["state"]}


@app.post("/twilio/sms")
async def incoming_sms(request: Request):
    f = await twilio_form(request)
    ident = text_incident(f)
    body = str(f.get("Body", ""))[:2000]
    message_sid = str(f.get("MessageSid", ""))
    if not re.fullmatch(r"(?:SM|MM)[a-fA-F0-9]{32}", message_sid):
        raise HTTPException(422, "Invalid MessageSid")
    first = store.add_event(ident, "inbound_sms", {"message_sid": message_sid, "length": len(body)}, event_id=message_sid)
    if not first:
        return xml(MessagingResponse())
    if body.strip().upper() in ('STOP','STOPALL','UNSUBSCRIBE','CANCEL','END','QUIT'):
        store.set_optout(str(f['From']),True)
        return xml(MessagingResponse())
    if body.strip().upper() in ('START','UNSTOP'):
        store.set_optout(str(f['From']),False)
        return xml(MessagingResponse())
    match = re.fullmatch(r'LINK\s+([A-Fa-f0-9]{10})',body.strip(),re.I)
    if match:
        sms_id='sms-'+hashlib.sha256(str(f['From']).encode()).hexdigest()[:28]
        target=store.redeem_link(sms_id,str(f['From']),match.group(1))
        response=MessagingResponse()
        response.message('Voice and text context linked for one hour.' if target else 'Link code invalid or expired. Ask for a new code during your call.')
        return xml(response)
    if emergency_response(body):
        response=MessagingResponse()
        response.message(emergency_response(body))
        return xml(response)
    elif re.search(r"\b(camera|video link|visual help)\b", body, re.I):
        delivery = providers.send_reference(ident, make_camera_link(ident), explicit_consent=True)
        store.add_event(ident, "camera_link_delivery", delivery)
    else:
        store.enqueue(ident, "message", {"text": body, "channel": "sms", "sms_consent": True})
    return xml(MessagingResponse())


@app.post("/twilio/voice")
async def incoming_voice(request: Request):
    form = await twilio_form(request)
    call_incident(form)
    return next_prompt("This is the RELAY prototype, not an emergency service. For immediate danger, call 911. Tell me what you need help with.")


@app.post("/twilio/voice/turn")
async def voice_turn(request: Request, turn: str | None = None):
    f = await twilio_form(request)
    incident = call_incident(f)
    spoken = str(f.get("SpeechResult", "") or f.get("Digits", ""))[:2000]
    if not spoken:
        return next_prompt("I did not hear you. Please repeat your request.")
    # TwiML Gather actions carry a server-created turn identifier, stable on retries.
    # Legacy direct calls get content-derived deduplication, never a second send.
    turn_key=incident+':'+(turn or hashlib.sha256(spoken.encode()).hexdigest())
    cached=store.voice_cached(turn_key)
    if cached:
        return Response(content=cached,media_type='application/xml')
    def remember(response):
        store.cache_voice(turn_key,response.body.decode())
        return response
    if not store.add_event(incident, "voice_turn", {"text_length": len(spoken)},event_id=turn_key):
        cached=store.voice_cached(turn_key)
        if cached:
            return Response(content=cached,media_type='application/xml')
        raise HTTPException(503,'This turn is already being processed',headers={'Retry-After':'1'})
    if emergency_response(spoken):
        v = VoiceResponse()
        v.say(emergency_response(spoken))
        v.hangup()
        return remember(xml(v))
    if re.search(r"\b(link|connect)\b.*\b(text|sms|context|phone)\b|\blink code\b",spoken,re.I):
        try:
            code=store.create_link(incident)
        except ValueError:
            return remember(next_prompt('Your caller number is unavailable, so I cannot securely link text context.'))
        return remember(next_prompt('To link this call with text, send LINK followed by '+ ' '.join(code) + ' to this number within five minutes. The code works once from your calling number.'))
    if re.search(r"\b(text me|send me)\b.*\b(camera|video|visual)\b", spoken, re.I):
        delivery = providers.send_reference(incident, make_camera_link(incident), explicit_consent=True)
        store.add_event(incident, "camera_link_delivery", delivery)
        if delivery["state"] == "accepted_by_provider":
            return remember(next_prompt("The carrier accepted your camera link text. Delivery is not confirmed. Please open it only when safe. What else do you need?"))
        return remember(next_prompt("I generated a camera link, but this phone account cannot deliver a personalized text right now. What else do you need?"))
    job_id=store.enqueue(incident, "message", {"text": spoken, "channel": "voice",
                   "sms_consent": bool(re.search(r"\b(text me|send (me )?a text|sms me)\b", spoken, re.I))})
    v = VoiceResponse()
    v.say("I'm checking that. Please stay on the line.")
    v.pause(length=2)
    v.redirect(PUBLIC()+"/twilio/voice/poll?incident="+incident+"&job_id="+job_id+"&attempt=0", method="POST")
    return remember(xml(v))


@app.post("/twilio/voice/poll")
async def voice_poll(request: Request, incident: str, job_id: str, attempt: int = 0):
    f = await twilio_form(request)
    if call_incident(f) != incident:
        raise HTTPException(403, "Wrong call")
    current = store.job_for(incident,job_id)
    if not current:
        raise HTTPException(404,"Job not found for this call")
    if current and current["state"] == "complete":
        return next_prompt(current["result"] or "I cannot verify that yet.")
    if current and current["state"] == "failed":
        return next_prompt("The information service is temporarily unavailable. I cannot verify an answer.")
    if attempt >= 2:
        v = VoiceResponse()
        v.say("I am unable to verify that quickly. Please call local emergency services if there is immediate danger. Goodbye.")
        v.hangup()
        return xml(v)
    v = VoiceResponse()
    v.pause(length=3)
    v.redirect(PUBLIC()+f"/twilio/voice/poll?incident={incident}&job_id={job_id}&attempt={attempt+1}", method="POST")
    return xml(v)


@app.post('/twilio/status')
async def delivery_status(request: Request, outbox_id: str | None = None):
    form=await twilio_form(request)
    sid=str(form.get('MessageSid',''))
    if not re.fullmatch(r'(?:SM|MM)[a-fA-F0-9]{32}',sid):
        raise HTTPException(422,'Invalid MessageSid')
    store.delivery_update(sid,str(form.get('MessageStatus','')),outbox_id)
    return Response(status_code=204)


def emergency_response(text):
    try:
        from .engine import policy_response
        return policy_response(text)
    except ImportError:
        return EMERGENCY_TEXT if EMERGENCY.search(text) else None


def camera_signature(incident, expires):
    secret = os.getenv("RELAY_LINK_SECRET", os.getenv("DEMO_TOKEN", ""))
    if len(secret) < 16:
        raise HTTPException(503, "Camera links not configured")
    return hmac.new(secret.encode(), f"{incident}:{expires}".encode(), hashlib.sha256).hexdigest()


def verify_camera(incident, expires, token):
    if expires < int(time.time()) or expires > int(time.time()) + 1800:
        raise HTTPException(403, "Expired camera token")
    if not secrets.compare_digest(camera_signature(incident, expires), token):
        raise HTTPException(403, "Bad camera token")
    if not store.incident_info(incident):
        raise HTTPException(404, "Incident not found")


def make_camera_link(incident):
    expires = int(time.time()) + 600
    return PUBLIC() + f"/camera/{incident}?expires={expires}&token={camera_signature(incident, expires)}"


@app.post("/demo/incidents/{incident}/camera-link")
def camera_link(request: Request, incident: str):
    require_token(request, "DEMO_TOKEN")
    if not store.incident_info(incident):
        raise HTTPException(404, "Incident not found")
    expires = int(time.time())+600
    token = camera_signature(incident, expires)
    return {"url": PUBLIC()+f"/camera/{incident}?expires={expires}&token={token}",
            "expires_at": expires, "requires_user_camera_permission": True}


@app.get("/camera/{incident}", response_class=HTMLResponse)
def camera_page(incident: str, expires: int, token: str):
    verify_camera(incident, expires, token)
    return HTMLResponse("""<!doctype html><html><meta name=viewport content="width=device-width,initial-scale=1">
    <title>RELAY camera sharing</title>
    <body style="font:16px system-ui;max-width:540px;margin:32px auto;padding:16px">
    <h2>RELAY visual assistance</h2><p>Only start sharing when it's safe. Your camera is off until you grant access.</p>
    <video id="preview" autoplay muted playsinline style="width:100%"></video>
    <p><button id="start">Start camera</button> <button id="send" disabled>Analyze one frame</button>
    <button id="stop" disabled>Stop</button></p><p id="status"></p>
    <script>
      const video=document.querySelector('#preview'),s=document.querySelector('#status');
      let stream;
      document.querySelector('#start').onclick=async()=>{
        try{stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'},audio:false});
          video.srcObject=stream;s.textContent='Camera active';document.querySelector('#send').disabled=false;
          document.querySelector('#stop').disabled=false;}catch(e){s.textContent='Camera permission unavailable';}
      };
      document.querySelector('#stop').onclick=()=>{
        if(stream)stream.getTracks().forEach(t=>t.stop());video.srcObject=null;
        document.querySelector('#send').disabled=true;document.querySelector('#stop').disabled=true;s.textContent='Stopped';
      };
      document.querySelector('#send').onclick=async()=>{
        const c=document.createElement('canvas');c.width=640;c.height=480;
        c.getContext('2d').drawImage(video,0,0,c.width,c.height);
        const data=c.toDataURL('image/jpeg',.55).split(',')[1];
        s.textContent='Sending one frame...';
        try{const r=await fetch(location.pathname+'/frame'+location.search,{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({image_base64:data})});
          s.textContent=r.ok?'Frame queued for review. Return to your call.':'Could not send frame';
        }catch(e){s.textContent='Network error';}
      };
    </script></body></html>""")


class CameraFrame(BaseModel):
    image_base64: str = Field(min_length=12, max_length=300000)


@app.post("/camera/{incident}/frame")
def camera_frame(incident: str, expires: int, token: str, body: CameraFrame):
    verify_camera(incident, expires, token)
    try:
        raw = base64.b64decode(body.image_base64, validate=True)
        if len(raw) > 200_000 or not raw.startswith(b"\xff\xd8"):
            raise ValueError("invalid JPEG")
    except Exception:
        raise HTTPException(422, "Invalid or oversized frame")
    job = store.enqueue(incident, "message", {
        "text": "Describe the visible scene cautiously and mention uncertainties. Never declare a location safe.",
        "channel": "camera", "sms_consent": False, "image_base64": body.image_base64})
    return {"state": "queued", "job_id": job, "notice": "Single-frame analysis, not a live video call"}
