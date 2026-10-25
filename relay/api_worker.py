"""Gateway-owned OpenAI queue worker; the key never leaves this host."""
import asyncio
from . import store, openai_fallback


def primary():
    import os
    return os.getenv('RELAY_INFERENCE_PROVIDER', 'minicpm').lower() == 'openai'


def process(job):
    try:
        result, reference, official = openai_fallback.answer(job['payload']['text'], job['incident'], job['payload'])
        with store.connect() as c:
            c.execute("UPDATE jobs SET payload=json_set(payload,'$.inference_provider','openai') WHERE id=? AND lease=?",
                      (job['id'], job['lease']))
        store.finish(job['id'], job['lease'], result, reference_text=reference, official_context=official)
    except Exception as error:
        store.add_event(job['incident'], 'api_inference_error', {'error_type': type(error).__name__})
        store.finish(job['id'], job['lease'], error='The AI service could not complete this request. Please try again shortly.')


async def run(stop):
    while not stop.is_set():
        job = await asyncio.to_thread(store.claim)
        if job:
            await asyncio.to_thread(process, job)
        else:
            try:
                await asyncio.wait_for(stop.wait(), timeout=.2)
            except TimeoutError:
                pass
