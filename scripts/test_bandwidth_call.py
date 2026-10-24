"""One explicitly authorized, bounded carrier test; no automatic call retries."""
import argparse
import json
import os
from pathlib import Path
import re
import time
from dotenv import load_dotenv
import httpx
from relay.bandwidth_sms import access_token


def main():
    load_dotenv()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--place-one-call',action='store_true')
    args=parser.parse_args()
    target=os.getenv('RELAY_TEST_RECIPIENT','')
    if not re.fullmatch(r'\+1\d{10}',target):raise SystemExit('Set the approved test recipient first.')
    if not args.place_one_call:
        print('Prepared one test call to the configured approved recipient; not submitted. Connected time capped at 45 seconds.')
        return
    if int(os.getenv('BANDWIDTH_MAX_CALL_SECONDS','45'))>45:raise SystemExit('Reduce the call cap to 45 seconds first.')
    marker=Path('.runtime/bandwidth-approved-call.json');marker.parent.mkdir(exist_ok=True)
    # The marker is written BEFORE submission. An interrupted/uncertain request
    # must be reconciled with the provider, never repeated automatically.
    try:
        fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:raise SystemExit('The one-call test was already attempted. No second call was placed.')
    os.write(fd,json.dumps({'state':'submission_started','time':time.time()}).encode());os.close(fd)
    base=os.environ['PUBLIC_BASE_URL'].rstrip('/')
    account=os.environ['BANDWIDTH_ACCOUNT_ID']
    api='https://voice.bandwidth.com/api/v2/accounts/'+account+'/calls'
    with httpx.Client(timeout=12) as client:
        token=access_token(client);headers={'Authorization':'Bearer '+token}
        body={'applicationId':os.environ['BANDWIDTH_VOICE_APPLICATION_ID'], 'from':os.environ['BANDWIDTH_NUMBER'],
              'to':target,'answerUrl':base+'/bandwidth/voice','answerMethod':'POST',
              'disconnectUrl':base+'/bandwidth/status','disconnectMethod':'POST',
              'username':'relay-hook','password':os.environ['BANDWIDTH_CALLBACK_SECRET'],
              'callTimeout':15,'tag':'relay-bounded-approved-test'}
        response=client.post(api,headers=headers,json=body)
        if not response.is_success:
            try:detail=response.json()
            except ValueError:detail={'message':'Non-JSON rejection'}
            report={'state':'rejected','http_status':response.status_code,'details':detail}
            marker.write_text(json.dumps(report));print(json.dumps(report));return
        call=response.json();call_id=call['callId']
        marker.write_text(json.dumps({'state':'created','call_id':call_id,'time':time.time()}))
        print('One approved trial call created. Watching its bounded outcome.',flush=True)
        started=time.monotonic();last=None
        while time.monotonic()-started<43:
            result=client.get(api+'/'+call_id,headers=headers)
            if result.is_success:
                info=result.json();state=info.get('state')
                if state!=last:print('Carrier state: '+str(state),flush=True);last=state
                if state=='disconnected':
                    report={'state':state,'cause':info.get('disconnectCause'),'seconds':round(time.monotonic()-started,2),'call_id':call_id}
                    marker.write_text(json.dumps(report));print(json.dumps(report));return
            time.sleep(2)
        # Independent cleanup if provider callbacks or call instructions failed.
        result=client.post(api+'/'+call_id,headers=headers,json={'state':'completed'})
        marker.write_text(json.dumps({'state':'termination_requested','call_id':call_id,'http_status':result.status_code}))
        print('Bounded test ended; termination requested. No call retry will occur.')

if __name__=='__main__':main()
