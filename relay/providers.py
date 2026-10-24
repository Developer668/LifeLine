"""Phone provider integration. Outbound sending OFF by default."""
import os
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from . import store


def validate_twilio(url, form, signature):
    secret = os.getenv("TWILIO_AUTH_TOKEN")
    if not secret or not signature:
        return False
    return RequestValidator(secret).validate(url, dict(form), signature)


def send_reference(incident, body, explicit_consent=False):
    recipient = store.phone_for(incident)
    if not explicit_consent or not recipient:
        return {"state": "not_authorized"}
    ident = store.queue_outbox(incident, recipient, body)
    if os.getenv("TWILIO_LIVE_SEND", "false").lower() != "true":
        return {"state": "simulated", "id": ident}
    required = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_NUMBER")
    if not all(os.getenv(x) for x in required):
        store.update_outbox(ident, "failed_configuration")
        return {"state": "failed_configuration", "id": ident}
    try:
        message = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]).messages.create(
            from_=os.environ["TWILIO_NUMBER"], to=recipient, body=body[:1000])
        store.update_outbox(ident, "accepted_by_provider", message.sid)
        return {"state": "accepted_by_provider", "id": ident}
    except Exception:
        store.update_outbox(ident, "carrier_rejected")
        return {"state": "carrier_rejected", "id": ident}
