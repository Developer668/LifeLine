"""Inspect account phones, or configure an existing number's inbound webhooks.
No number purchases, SMS sends, or phone calls are performed by this script.
"""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv, set_key
from twilio.rest import Client
from twilio.http.http_client import TwilioHttpClient
from twilio.base.exceptions import TwilioRestException


def main():
    root=Path(__file__).resolve().parents[1]
    load_dotenv(root/'.env')
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--number',default=os.getenv('TWILIO_NUMBER',''))
    p.add_argument('--apply',action='store_true',help='Configure an existing number only')
    args=p.parse_args()
    if not all(os.getenv(k) for k in ('TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN')):
        raise SystemExit('Add Twilio account credentials to .env first.')
    client=Client(os.environ['TWILIO_ACCOUNT_SID'],os.environ['TWILIO_AUTH_TOKEN'],http_client=TwilioHttpClient(timeout=12))
    try:
        account=client.api.accounts(os.environ['TWILIO_ACCOUNT_SID']).fetch()
        numbers=client.incoming_phone_numbers.list(limit=50)
        verified=client.outgoing_caller_ids.list(limit=50)
        print(json.dumps({'account_status':account.status,'account_type':account.type,'owned_numbers':[n.phone_number for n in numbers],'verified_recipients':[n.phone_number for n in verified]},indent=2))
        if not args.apply:return
        selected=[n for n in numbers if n.phone_number==args.number] if args.number else numbers
        if len(selected)!=1:
            raise SystemExit('Select exactly one existing number using --number. No number was purchased.')
        base=os.environ.get('PUBLIC_BASE_URL','').rstrip('/')
        if urlsplit(base).scheme!='https':raise SystemExit('PUBLIC_BASE_URL must use HTTPS.')
        number=selected[0]
        prior={'sid':number.sid,'voice_url':number.voice_url,'voice_method':number.voice_method,'sms_url':number.sms_url,'sms_method':number.sms_method}
        runtime=root/'.runtime';runtime.mkdir(exist_ok=True)
        backup=runtime/'twilio-webhooks-before.json'
        if not backup.exists():backup.write_text(json.dumps(prior,indent=2)+'\n')
        number.update(voice_url=base+'/twilio/voice',voice_method='POST',sms_url=base+'/twilio/sms',sms_method='POST')
        updated=client.incoming_phone_numbers(number.sid).fetch()
        assert updated.voice_url==base+'/twilio/voice' and updated.sms_url==base+'/twilio/sms'
        set_key(str(root/'.env'),'TWILIO_NUMBER',number.phone_number)
        print('Inbound webhooks configured and read back successfully. Restart the gateway to load its number. Outbound sending remains unchanged.')
    except TwilioRestException as error:
        raise SystemExit(f'Twilio request failed: HTTP {error.status}, code {error.code}. No delivery asserted.')

if __name__=='__main__':main()
