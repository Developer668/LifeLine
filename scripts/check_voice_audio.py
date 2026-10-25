"""Exercise raw call audio without placing a carrier call or sending SMS.

Uses synthetic non-speech noise and a generated question against the public
WebSocket gateway, the configured recognition/inference APIs and local speech output. API use is budgeted.
Run from the project root with: uv run --extra voice python scripts/check_voice_audio.py
It does not establish handset playback; that requires a real phone test.
"""
import asyncio,base64,json,time,uuid,math,struct,xml.etree.ElementTree as ET,audioop,hashlib
from dotenv import dotenv_values,load_dotenv
from pathlib import Path
import httpx
from websockets.asyncio.client import connect
from relay.live_audio import sentence_audio
from relay import store
load_dotenv('.env');v=dotenv_values('.env')
async def main():
 base=v['PUBLIC_BASE_URL'];call='c-local-drill-'+uuid.uuid4().hex[:12]
 auth='Basic '+base64.b64encode(('relay-hook:'+v['BANDWIDTH_CALLBACK_SECRET']).encode()).decode()
 async with httpx.AsyncClient(timeout=12) as c:
  for _ in range(30):
   try:
    if (await c.get(base+'/health')).status_code==200:break
   except httpx.HTTPError:pass
   await asyncio.sleep(.3)
  r=await c.post(base+'/bandwidth/voice',headers={'Authorization':auth},json={'eventType':'initiate','callId':call,'accountId':v['BANDWIDTH_ACCOUNT_ID'],'applicationId':v['BANDWIDTH_VOICE_APPLICATION_ID']});r.raise_for_status();attrs=ET.fromstring(r.text).find('StartStream').attrib
 audio=await asyncio.to_thread(sentence_audio,'What should I put in a basic emergency kit?')
 noise=audioop.lin2ulaw(b''.join(struct.pack('<h',int(2500*math.sin(2*math.pi*180*i/8000))) for i in range(160)),2)
 header='Basic '+base64.b64encode(('relay-hook:'+attrs['destinationPassword']).encode()).decode()
 counts={'playAudio':0,'clear':0};first=None;max_rms=0;total=0;sequence=0;started=time.monotonic()
 incident='bw-'+hashlib.sha256(call.encode()).hexdigest()[:28]
 async with connect(attrs['destination'],additional_headers={'Authorization':header},max_size=20000) as ws:
  await ws.send(json.dumps({'eventType':'start','metadata':{'callId':call,'accountId':v['BANDWIDTH_ACCOUNT_ID']}}))
  async def listen():
   nonlocal first,max_rms,total
   async for raw in ws:
    m=json.loads(raw);kind=m.get('eventType');counts[kind]=counts.get(kind,0)+1
    if kind=='playAudio':
     if first is None:first=time.monotonic()-started
     b=base64.b64decode(m['media']['payload']);total+=len(b);max_rms=max(max_rms,audioop.rms(audioop.ulaw2lin(b,2),2))
  reader=asyncio.create_task(listen())
  async def send(packet):
   nonlocal sequence
   sequence+=1;await ws.send(json.dumps({'eventType':'media','track':'inbound','sequenceNumber':str(sequence),'payload':base64.b64encode(packet).decode()}));await asyncio.sleep(.02)
  for i in range(150):await send(noise if i%30<12 else b'\xff'*160)
  during_noise=dict(counts)
  assert during_noise['playAudio']>40 and during_noise['clear']==0, during_noise
  for offset in range(0,len(audio)+16000,160):await send(audio[offset:offset+160] if offset<len(audio) else b'\xff'*160)
  deadline=time.monotonic()+14
  while time.monotonic()<deadline:
   job=store.latest(incident)
   if job and job['state'] in ('complete','failed'):break
   await asyncio.sleep(.2)
  before=counts['playAudio'];await asyncio.sleep(2)
  reply_packets=counts['playAudio']-before
  await ws.send(json.dumps({'eventType':'stop'}));await reader
 report={'test':'public websocket with non-speech noise, configured speech recognition/inference and local speech output','carrier_call_created':False,'first_audio_seconds':round(first,3) if first else None,'noise_phase':during_noise,'events':counts,'peak_packet_pcm_rms':max_rms,'audio_bytes':total,'inference_job_state':job['state'] if job else None,'inference_provider':json.loads(job['payload']).get('inference_provider','configured') if job else None,'reply_audio_packets_in_final_two_seconds':reply_packets,'seconds':round(time.monotonic()-started,2)}
 Path('reports/noisy-voice.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
 assert job and job['state']=='complete' and reply_packets>20 and max_rms>1000
asyncio.run(main())
