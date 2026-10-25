"""RELAY: phone-first prototype. Not an emergency dispatch service."""
import json
import hashlib
import os
import re
import secrets
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from twilio.twiml.voice_response import VoiceResponse
from twilio.twiml.messaging_response import MessagingResponse
from . import store, providers

from contextlib import asynccontextmanager
import asyncio

@asynccontextmanager
async def lifespan(app):
    from .outbox import background_drain
    from . import live_audio
    if live_audio.ready():
        store.set_service_health('voice_audio', 'warming')
        try:
            await asyncio.to_thread(live_audio.warmup)
            store.set_service_health('voice_audio', 'healthy')
        except Exception as error:
            store.set_service_health('voice_audio', 'failed', error_code=type(error).__name__)
    stop = asyncio.Event()
    task = asyncio.create_task(background_drain(stop))
    from . import api_worker
    inference = asyncio.create_task(api_worker.run(stop)) if api_worker.primary() else None
    try:
        yield
    finally:
        stop.set()
        await task
        if inference:
            await inference

app = FastAPI(title="RELAY phone-first demo", version="0.2.0", lifespan=lifespan)
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
    phone = form.get("To") if str(form.get("Direction", "")).startswith("outbound") else form.get("From")
    store.upsert_incident(ident, phone, "voice")
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
    carrier_auth = store.service_health('bandwidth_auth')
    voice_audio = store.service_health('voice_audio')
    api_inference = store.service_health('openai_inference')
    api_transcription = store.service_health('openai_transcription')
    api_failed = os.getenv('RELAY_INFERENCE_PROVIDER') == 'openai' and any(s['state'] in ('failed', 'unavailable') for s in (api_inference,api_transcription))
    return {"status": "degraded" if carrier_auth['state'] in ('failed', 'unavailable') or voice_audio['state'] == 'failed' or api_failed else "ok", "mode": os.getenv("RELAY_MODE", "demo"),
            "readiness": {"twilio_configured": all(os.getenv(k) for k in ('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_NUMBER')),
                          "live_send_enabled": os.getenv('TWILIO_LIVE_SEND','false').lower()=='true',
                          "bandwidth_configured": all(os.getenv(k) for k in ('BANDWIDTH_CLIENT_ID', 'BANDWIDTH_CLIENT_SECRET', 'BANDWIDTH_ACCOUNT_ID', 'BANDWIDTH_VOICE_APPLICATION_ID', 'BANDWIDTH_CALLBACK_SECRET')),
                          "bandwidth_sms_enabled": os.getenv('BANDWIDTH_LIVE_SEND','false').lower()=='true',
                          "bandwidth_auth": carrier_auth,
                          "voice_audio": voice_audio,
                          "inference_provider": os.getenv('RELAY_INFERENCE_PROVIDER', 'minicpm'),
                          "speech_recognition": os.getenv('RELAY_ASR_ENGINE', 'vosk'),
                          "openai_inference": api_inference,
                          "openai_transcription": api_transcription,
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


@app.get('/demo/openai-budget')
def openai_budget(request: Request):
    require_token(request, 'DEMO_TOKEN')
    from . import openai_fallback
    values = openai_fallback.budget()
    return {'enabled': bool(openai_fallback.enabled()), 'scope': 'RELAY lifetime OpenAI usage only',
            'cap_usd': values['cap_micro']/1_000_000,
            'accounted_usd': values['accounted_micro']/1_000_000,
            'remaining_usd': values['remaining_micro']/1_000_000,
            'transcription_model': openai_fallback.ASR_MODEL, 'answer_model': openai_fallback.TEXT_MODEL}


@app.post("/worker/claim")
def worker_claim(request: Request):
    require_token(request, "RELAY_WORKER_TOKEN")
    from .api_worker import primary
    if primary():
        return {'job': None}
    return {"job": store.claim()}


from .engine.data import DataResult

class FinishJob(BaseModel):
    lease: str = Field(min_length=12)
    result: str | None = Field(default=None, max_length=1800)
    error: str | None = Field(default=None, max_length=1800)

    reference_text: str | None = Field(default=None, min_length=1, max_length=1000)
    official_context: DataResult | None = None

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
    status = store.finish(job_id, body.lease, body.result, body.error,
        reference_text=body.reference_text,
        official_context=body.official_context.model_dump(mode='json') if body.official_context else None)
    if not status:
        raise HTTPException(409, "Job missing, expired or already completed")
    if status["state"] in ("complete","failed"):
        with store.connect() as c:
            job = c.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        import json
        payload = json.loads(job["payload"])
        if payload.get("sms_consent") and payload.get("channel") in ("sms", "voice"):
            delivery = providers.send_reference(status["incident"], status["text"] if status["state"]=="complete" else "RELAY could not verify an answer because the information service is unavailable. For immediate danger, call your local emergency number directly.", explicit_consent=True, idempotency_key=job_id)
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
    command = body.strip().upper()
    special = (command in ('STOP','STOPALL','UNSUBSCRIBE','CANCEL','END','QUIT','START','UNSTOP')
        or re.fullmatch(r'LINK\s+([A-Fa-f0-9]{10})', body.strip(), re.I)
        or emergency_response(body)
        or re.search(r'\b(camera|video link|visual help)\b', body, re.I)
        or not body.strip())
    if not special:
        store.accept_sms_job(ident, message_sid,
            {'message_sid': message_sid, 'length': len(body)},
            {'text': body, 'channel': 'sms', 'sms_consent': True})
        return xml(MessagingResponse())
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
    elif not body.strip() and str(f.get('NumMedia', '0')) != '0':
        response = MessagingResponse()
        response.message('To share an image securely, text CAMERA for a private browser link. MMS attachment analysis is not enabled.')
        return xml(response)
    elif not body.strip():
        response = MessagingResponse()
        response.message('Please describe what information you need, or text CAMERA for visual assistance.')
        return xml(response)
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
    if attempt >= 3:
        v = VoiceResponse()
        v.say("I am unable to verify that quickly. Please call local emergency services if there is immediate danger. Goodbye.")
        v.hangup()
        return xml(v)
    v = VoiceResponse()
    v.pause(length=10)
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


# Optional visual assistance shares the incident but uses revocable capabilities.
from .media import issue_camera_link, install_media
from .coordination import install_coordination
make_camera_link = issue_camera_link
install_media(app)
install_coordination(app)
from .middleware import RequestBoundary
app.add_middleware(RequestBoundary)

from .bandwidth import install_bandwidth
install_bandwidth(app)
from .bandwidth_sms import install_bandwidth_sms
install_bandwidth_sms(app)
