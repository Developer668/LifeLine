"""Reproducible labeled end-to-end simulation against the real gateway HTTP API.
No carrier calls or GPU claims. Run while a worker with RELAY_SIMULATE=true is active.
"""
import argparse
import json
import os
import time
import uuid
from pathlib import Path
import httpx
from dotenv import load_dotenv


def main():
    load_dotenv()
    p=argparse.ArgumentParser()
    p.add_argument('--url',default=os.getenv('RELAY_API_URL','http://127.0.0.1:8000'))
    p.add_argument('--runs',type=int,default=3)
    p.add_argument('--output',default='reports/demo.json')
    p.add_argument('--inference',choices=['simulation','model'],default='simulation')
    p.add_argument('--gpu-evidence',type=Path)
    args=p.parse_args()
    results=[]
    with httpx.Client(base_url=args.url,headers={'Authorization':'Bearer '+os.environ['DEMO_TOKEN']},timeout=10) as c:
        for run in range(args.runs):
            incident='demo_'+uuid.uuid4().hex[:12]
            started=time.perf_counter()
            r=c.post('/demo/message',json={'incident_id':incident,'text':'This is a fictional drill. What information can RELAY help me find?'})
            r.raise_for_status()
            job_id=r.json()['job_id']
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                info=c.get('/demo/incidents/'+incident)
                info.raise_for_status()
                job=next(j for j in info.json()['jobs'] if j['id']==job_id)
                if job['state'] in ('complete','failed'):
                    break
                time.sleep(.2)
            else:
                raise RuntimeError('Demo timed out')
            if job['state']!='complete':
                raise RuntimeError('Worker failed: '+str(job['result']))
            simulated='relay simulation:' in job['result'].lower()
            if simulated != (args.inference=='simulation'):
                raise RuntimeError('Actual worker mode differs from the requested demo mode')
            results.append({'run':run+1,'incident':incident,'state':job['state'],'seconds':round(time.perf_counter()-started,3),'response':job['result'],'carrier':'not exercised'})
    evidence=json.loads(args.gpu_evidence.read_text()) if args.gpu_evidence else {}
    report={'kind':'HTTP demo with '+args.inference+' inference','runs':results,
        'gpu_verified':args.inference=='model' and evidence.get('gpu_verified') is True,
        'gpu_evidence':str(args.gpu_evidence) if args.gpu_evidence else None,'carrier_verified':False}
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
