"""Twilio integration. Durable intent, atomic send claims, no uncertain retries."""
import os
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.http.http_client import TwilioHttpClient
from . import store


def validate_twilio(url, form, signature):
    secret = os.getenv('TWILIO_AUTH_TOKEN')
    return bool(secret and signature and RequestValidator(secret).validate(url, dict(form), signature))


def send_reference(incident, body, explicit_consent=False, idempotency_key=None):
    recipient = store.phone_for(incident)
    if not explicit_consent or not recipient or store.opted_out(recipient):
        return {'state':'not_authorized'}
    ident = store.queue_outbox(incident, recipient, body, idempotency_key=idempotency_key)
    return dispatch_outbox(ident)


def dispatch_outbox(ident=None):
    row=store.claim_outbox(ident)
    if not row:
        existing=store.outbox_for(ident) if ident else None
        return {'state':existing['state'] if existing else 'idle','id':ident}
    ident=row['id']
    # Consent may have been withdrawn after inference finished or intent creation.
    if store.opted_out(row['recipient']):
        store.update_outbox(ident,'failed')
        store.add_event(row['incident'],'outbox_blocked',{'id':ident,'reason':'opted_out'})
        return {'state':'failed','id':ident,'reason':'opted_out'}
    from . import bandwidth_sms
    if bandwidth_sms.owns(row['incident']):
        return bandwidth_sms.dispatch(row)
    if os.getenv('TWILIO_LIVE_SEND','false').lower() != 'true':
        store.update_outbox(ident,'simulated')
        return {'state':'simulated','id':ident}
    if not all(os.getenv(x) for x in ('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN','TWILIO_NUMBER')):
        store.update_outbox(ident,'failed')
        return {'state':'failed','id':ident,'reason':'configuration_missing'}
    try:
        client=Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'], http_client=TwilioHttpClient(timeout=8))
        message=client.messages.create(from_=os.environ['TWILIO_NUMBER'], to=row['recipient'],
            body=row['content'], status_callback=os.getenv('PUBLIC_BASE_URL','http://127.0.0.1:8000').rstrip('/')+'/twilio/status?outbox_id='+ident)
        store.update_outbox(ident,'accepted_by_provider',message.sid)
        if getattr(message,'status',None) in ('failed','undelivered','canceled'):
            store.update_outbox(ident,'failed',message.sid)
            return {'state':'failed','id':ident,'reason':'carrier_rejected'}
        # A callback may already have supplied a terminal status.
        return {'state':store.outbox_for(ident)['state'],'id':ident}
    except Exception as exc:
        code=getattr(exc,'code',None)
        # A Twilio 4xx explicitly rejects creation; timeout/network/5xx is uncertain.
        rejected=isinstance(getattr(exc,'status',None),int) and 400<=exc.status<500
        state='failed' if rejected else 'acceptance_unknown'
        store.update_outbox(ident,state)
        store.add_event(row['incident'],'outbox_send_error',{'id':ident,'state':state,'error_code':code})
        return {'state':state,'id':ident,'reason':'carrier_rejected' if rejected else 'provider_acceptance_unconfirmed','error_code':code}
