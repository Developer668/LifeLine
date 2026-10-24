"""Recover prepared sends without replaying uncertain carrier submissions.

Run ``python -m relay.outbox`` alongside the gateway, or call background_drain()
from its lifespan. Prepared intents survive worker and gateway restarts. A send
interrupted after submission requires carrier reconciliation; it is never retried.
"""
import argparse
import asyncio
import time
from dotenv import load_dotenv
from . import providers, store


def drain_once(limit=50):
    store.recover_expired()
    store.recover_stalled_submissions()
    results=[]
    for _ in range(limit):
        result=providers.dispatch_outbox()
        if result['state']=='idle':
            break
        results.append(result)
    return results


async def background_drain(stop: asyncio.Event, interval=1.0):
    while not stop.is_set():
        try:
            await asyncio.to_thread(drain_once)
        except Exception:
            # Intents stay durable; no private content is printed.
            pass
        try:
            await asyncio.wait_for(stop.wait(),timeout=interval)
        except TimeoutError:
            pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    load_dotenv()
    store.initialize()
    # Submitting may belong to a concurrently running gateway, so do not globally
    # mutate it on startup. It is already nonretryable; report uncertainty as such.
    while True:
        try:
            results=drain_once()
            if results:
                print('Outbox states:',','.join(result['state'] for result in results))
        except Exception:
            print('Outbox unavailable; prepared intents remain persisted.')
        if args.once:
            break
        time.sleep(1)


if __name__=='__main__':
    main()
