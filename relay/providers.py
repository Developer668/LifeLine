"""Twilio integration. Simulation is explicit; acceptance never means delivery."""
import os
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.http.http_client import TwilioHttpClient
from . import store


def validate_twilio(url, form, signature):
    secret = os.getenv('TWILIO_AUTH_TOKEN')
    return bool(secret and signature and RequestValidator(secret).validate(url, dict(form), signature))


def send_reference(incident, body, explicit_consent=False):
    recipient = store.phone_for(incident)
    if not explicit_consent or not recipient or store.opted_out(recipient):
        return {'state':'not_authorized'}
    ident = store.queue_outbox(incident, recipient, body)
    if os.getenv('TWILIO_LIVE_SEND','false').lower() != 'true':
        store.update_outbox(ident,'simulated')
        return {'state':'simulated','id':ident}
    if not all(os.getenv(x) for x in ('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_NUMBER')):
        store.update_outbox(ident,'failed')
        return {'state':'failed','id':ident,'reason':'configuration_missing'}
    try:
        client=Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'], http_client=TwilioHttpClient(timeout=8))
        message=client.messages.create(from_=os.environ['TWILIO_NUMBER'], to=recipient,
            body=body[:1000], status_callback=os.getenv('PUBLIC_BASE_URL','http://127.0.0.1:8000').rstrip('/')+'/twilio/status?outbox_id='+ident)
        store.update_outbox(ident,'accepted_by_provider',message.sid)
        # Some immediate failures are returned as resources rather than exceptions.
        if getattr(message,'status',None) in ('failed','undelivered','canceled'):
            store.update_outbox(ident,'failed',message.sid)
            return {'state':'failed','id':ident,'reason':'carrier_rejected'}
        return {'state':'accepted_by_provider','id':ident}
    except Exception as exc:
        store.update_outbox(ident,'failed')
        return {'state':'failed','id':ident,'reason':'provider_acceptance_unconfirmed',
                'error_code':getattr(exc,'code',None)}
