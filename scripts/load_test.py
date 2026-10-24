"""Concurrent-user queue/isolation benchmark. Simulated completions, never GPU throughput."""
import concurrent.futures
import json
import os
import statistics
import tempfile
import time
from pathlib import Path


def measure(users):
    from relay import store
    from relay.app import app
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as td:
        os.environ['RELAY_DB']=str(Path(td)/'load.db')
        os.environ['DEMO_TOKEN']='load-only-demo-token'
        os.environ['RELAY_WORKER_TOKEN']='load-only-worker-token'
        store.initialize()
        headers={'Authorization':'Bearer load-only-demo-token'}
        worker={'Authorization':'Bearer load-only-worker-token'}
        client=TestClient(app)
        def submit(i):
            t=time.perf_counter()
            r=client.post('/demo/message',headers=headers,json={'incident_id':f'load_{users}_{i:03}','text':f'fictional isolation marker {i}'})
            r.raise_for_status()
            return time.perf_counter()-t
        start=time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(users,16)) as pool:
            latencies=list(pool.map(submit,range(users)))
        def process(_):
            r=client.post('/worker/claim',headers=worker);r.raise_for_status();j=r.json()['job']
            if not j:return None
            answer='SIMULATED completion for '+j['incident']
            r=client.post('/worker/jobs/'+j['id']+'/complete',headers=worker,json={'lease':j['lease'],'result':answer});r.raise_for_status()
            return j['incident']
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            incidents=list(pool.map(process,range(users)))
        assert len(set(incidents))==users and None not in incidents
        for i in range(users):
            incident=f'load_{users}_{i:03}'
            r=client.get('/demo/incidents/'+incident,headers=headers);r.raise_for_status()
            jobs=r.json()['jobs']
            assert len(jobs)==1 and jobs[0]['state']=='complete'
            assert jobs[0]['result']=='SIMULATED completion for '+incident
        seconds=time.perf_counter()-start
        ordered=sorted(latencies)
        return {'users':users,'completed':users,'isolation_failures':0,'errors':0,'seconds':round(seconds,3),'queue_roundtrips_per_second':round(users/seconds,2),'enqueue_p50_ms':round(statistics.median(latencies)*1000,2),'enqueue_p95_ms':round(ordered[min(len(ordered)-1,int(.95*len(ordered)))]*1000,2)}

if __name__=='__main__':
    report={'mode':'in-process HTTP/SQLite benchmark with simulated inference; not GPU or carrier capacity','results':[measure(n) for n in (5,10,25,50)]}
    path=Path('reports/load.json');path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
