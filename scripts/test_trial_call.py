"""One explicitly authorized, short trial call using RELAY's actual webhook.
Never sends SMS or falls back to a canned Twilio demonstration.
"""
import argparse
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from twilio.rest import Client
from twilio.http.http_client import TwilioHttpClient
from twilio.base.exceptions import TwilioRestException


def main():
    root=Path(__file__).resolve().parents[1]
    load_dotenv(root/'.env')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--place',action='store_true',help='Place the already-authorized call once')
    args=parser.parse_args()
    evidence=root/'.runtime/trial-call.json'
    if evidence.exists():
        data=json.loads(evidence.read_text())
        if data.get('state') in ('submitting','acceptance_unknown'):
            raise SystemExit('Prior call acceptance is uncertain; inspect Twilio logs before any retry.')
        if data.get('sid'):
            c=Client(os.environ['TWILIO_ACCOUNT_SID'],os.environ['TWILIO_AUTH_TOKEN'],http_client=TwilioHttpClient(timeout=10))
            call=c.calls(data['sid']).fetch()
            print(json.dumps({'sid':call.sid,'status':call.status,'duration':call.duration,'notice':'No second call placed.'}))
            return
    if not args.place:
        print('No call placed. Use --place only after the recipient approves the short test.')
        return
    for name in ['TWILIO_NUMBER','RELAY_TEST_RECIPIENT','PUBLIC_BASE_URL']:
        if not os.getenv(name):raise SystemExit('Missing '+name)
    c=Client(os.environ['TWILIO_ACCOUNT_SID'],os.environ['TWILIO_AUTH_TOKEN'],http_client=TwilioHttpClient(timeout=10))
    if c.api.accounts(os.environ['TWILIO_ACCOUNT_SID']).fetch().type!='Trial':
        raise SystemExit('Refusing this free-trial-only test on a paid account.')
    evidence.parent.mkdir(exist_ok=True)
    evidence.write_text(json.dumps({'state':'submitting','time_limit_seconds':45})+'\n')
    try:
        call=c.calls.create(to=os.environ['RELAY_TEST_RECIPIENT'],from_=os.environ['TWILIO_NUMBER'],
            url=os.environ['PUBLIC_BASE_URL'].rstrip('/')+'/twilio/voice',method='POST',time_limit=45,timeout=15)
        data={'sid':call.sid,'status':call.status,'time_limit_seconds':45,'webhook':'RELAY','real_call_accepted':True}
        evidence.write_text(json.dumps(data,indent=2)+'\n')
        print(json.dumps(data))
    except TwilioRestException as e:
        state='rejected' if 400 <= e.status < 500 else 'acceptance_unknown'
        evidence.write_text(json.dumps({'state':state,'status':e.status,'code':e.code})+'\n')
        print(json.dumps({'accepted':False,'state':state,'status':e.status,'code':e.code,'message':e.msg}))
        raise SystemExit(1)
    except Exception:
        evidence.write_text(json.dumps({'state':'acceptance_unknown'})+'\n')
        raise SystemExit('Call acceptance is uncertain. Inspect Twilio logs before any retry.')

if __name__=='__main__':main()
