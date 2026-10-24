"""Outbound-only worker. Run beside the model on MoLab; never publish its port."""
import argparse
import concurrent.futures
import json
import os
import re
import signal
import threading
import time
import httpx
from .model import ModelAdapter

API = os.getenv('RELAY_API_URL', 'http://127.0.0.1:8000').rstrip('/')
TOKEN = os.getenv('RELAY_WORKER_TOKEN', '')
MAX_WORKERS = max(1, min(4, int(os.getenv('RELAY_WORKER_CONCURRENCY', '2'))))


def infer(payload):
    return ModelAdapter().propose(payload).spoken_response


def fetch_official_context(text):
    from .engine import build_context
    return build_context(text)


LOCATION = re.compile(r'(?<![\d.])(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)(?!\d|\.\d)|\b\d{5}\b')


def context_query(payload):
    from .engine import plan
    text = str(payload.get('text', ''))[:2000]
    history = [item['content'][:1000] for item in payload.get('history', [])[-8:]
               if isinstance(item, dict) and item.get('role') == 'user' and isinstance(item.get('content'), str)]
    current_location = LOCATION.search(text)
    location = current_location.group(0) if current_location else None
    prior_intent = None
    for previous in reversed(history):
        if location is None:
            matches = list(LOCATION.finditer(previous))
            if matches:
                location = matches[-1].group(0)
        action = plan(previous).next_action
        if prior_intent is None and action in ('get_weather_alerts', 'get_earthquakes'):
            prior_intent = action
    current_intent = plan(text).next_action
    # A bare location or explicit follow-up continues the last verified lookup.
    # Unrelated requests never silently inherit an emergency-data intent.
    stripped = LOCATION.sub('', text).strip(' ,.?')
    followup = bool(current_location and not stripped) or bool(re.fullmatch(
        r'(?:any |an? )?(?:updates?|now|again|what about (?:there|now)|check again|and now|how about now)[?.! ]*', text.strip(), re.I))
    if current_intent == 'respond' and prior_intent and followup:
        text += '\nRequested official lookup: ' + ('weather alerts' if prior_intent == 'get_weather_alerts' else 'earthquakes')
    if not current_location and location:
        text += '\nPreviously supplied location: ' + location
    return text


def run_answer(job):
    from .engine import policy_response
    if job['kind'] != 'message':
        raise ValueError('Unsupported request type')
    payload = job['payload']
    fallback = policy_response(str(payload.get('text', '')))
    if fallback:
        return fallback, None, None
    context = fetch_official_context(context_query(payload))
    answer = infer({**payload, 'context': context})
    official, reference = None, None
    if context:
        from .engine.data import DataResult
        official = DataResult.model_validate_json(context).model_dump(mode='json')
        if official['state'] == 'location_required':
            answer = 'Please share a ZIP code or latitude and longitude so I can check official ' + official['source'].upper() + ' information.'
        elif official['state'] == 'unavailable':
            answer = 'I could not verify official ' + official['source'].upper() + ' information right now. This does not establish that conditions are safe.'
        elif official['state'] == 'stale':
            answer = 'The official source is stale. ' + answer[:1700]
        urls = list(dict.fromkeys(a['source_url'] for a in official['alerts']))[:2]
        updated = official.get('source_updated_at') or (official['alerts'][0]['source_time'] if official['alerts'] else None)
        reference = (answer[:650] + '\nOfficial ' + official['source'].upper() + ': ' + official['state']
            + '\nSource updated: ' + (updated or 'not supplied')
            + '\nRetrieved: ' + official['fetched_at'] + ('\n' + '\n'.join(urls) if urls else ''))[:1000]
    return answer, reference, official


def run_one(job):
    # Compatibility for local/manual callers that need only the spoken wording.
    return run_answer(job)[0]


def process(client, job):
    started = time.monotonic()
    try:
        result, reference, official = run_answer(job)
        completion = {'lease': job['lease'], 'result': result}
        if reference:
            completion.update(reference_text=reference, official_context=official)
    except Exception:
        # No private messages, model responses, or exception strings in logs.
        completion = {'lease': job['lease'], 'error': 'Model processing unavailable. No answer could be verified.'}
    outcome = 'completion_unconfirmed'
    for attempt in range(2):
        try:
            response = client.post(f"/worker/jobs/{job['id']}/complete", json=completion, timeout=10)
            if response.status_code == 409:
                # Lease expiry or prior acceptance; never send a different outcome.
                outcome = 'lease_conflict_or_already_completed'
                break
            response.raise_for_status()
            outcome = 'failed' if 'error' in completion else 'complete'
            break
        except (httpx.HTTPError, ValueError):
            if not attempt:
                time.sleep(.3)
    print(json.dumps({'event': 'worker_job', 'job_id': job['id'], 'outcome': outcome,
                      'elapsed_seconds': round(time.monotonic() - started, 3)}), flush=True)
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Probe model health and listing, then perform text inference')
    args = parser.parse_args(argv)
    if args.check:
        adapter = ModelAdapter()
        print(json.dumps(adapter.probe()))
        result = adapter.generate({'text': 'Introduce RELAY in one sentence.'})
        print(json.dumps({'inference_ok': bool(result.text), 'simulated': result.simulated}))
        return
    token = os.getenv('RELAY_WORKER_TOKEN', '')
    api = os.getenv('RELAY_API_URL', 'http://127.0.0.1:8000').rstrip('/')
    concurrency = max(1, min(4, int(os.getenv('RELAY_WORKER_CONCURRENCY', '2'))))
    if not token:
        raise SystemExit('Set RELAY_WORKER_TOKEN first')
    if not os.getenv('MODEL_BASE_URL') and os.getenv('RELAY_ALLOW_SIMULATION', 'false').lower() != 'true':
        raise SystemExit('Configure MODEL_BASE_URL or explicitly enable RELAY_ALLOW_SIMULATION=true')
    stopping = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())
    with httpx.Client(base_url=api, headers={'Authorization': 'Bearer ' + token}, timeout=10) as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            running = set()
            print(json.dumps({'event': 'worker_started', 'max_in_flight': concurrency,
                'mode': 'model_endpoint' if os.getenv('MODEL_BASE_URL') else 'SIMULATION'}), flush=True)
            while not stopping.is_set():
                running = {f for f in running if not f.done()}
                if len(running) >= concurrency:
                    stopping.wait(.15)
                    continue
                try:
                    response = client.post('/worker/claim')
                    response.raise_for_status()
                    job = response.json().get('job')
                    if job:
                        running.add(pool.submit(process, client, job))
                    else:
                        stopping.wait(.5)
                except (httpx.HTTPError, ValueError):
                    stopping.wait(2)


if __name__ == '__main__':
    main()
