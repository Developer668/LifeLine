# RELAY: Phone-first multimodal coordination prototype

Primary interface: a telephone number. Inbound SMS and cellular calls are handled via
Twilio's signed webhook API. An optional, user-triggered HTTPS camera link captures an
explicitly selected frame on iPhone Safari or Android Chrome. This is not a native
FaceTime/Google Meet integration and the camera path is not yet continuous video.

**RELAY is an unmonitored hackathon prototype, not emergency response infrastructure.
It never dispatches police, firefighters, ambulances or 911. Do not publish it as a
replacement for official emergency services.**

## Architecture (adapted from NeuroLoop)

Twilio phone/SMS → HTTPS FastAPI gateway → SQLite/WAL durable queue → outbound
MoLab worker → MiniCPM-o 4.5 vLLM-Omni OpenAI-compatible server (same GPU container)
→ job result → TwiML voice reply or consented SMS outbox.

The persistent gateway and inference worker are separate processes. This recreates the
NeuroLoop NotebookWorker approach: remote workers claim jobs over outbound HTTPS and
complete them under a lease. The notebook does not need an open inbound network port.
The model is called on localhost from inside MoLab. Worker concurrency defaults to 2;
the single-GPU upstream vLLM-Omni default has max_num_seqs=4, and real capacity must
be determined by load tests, not by counting queued jobs.

## 1. Local bootstrap (free, without Twilio or GPU)

Open Terminal on your Mac:

    cd ~/Desktop/Programming/Hackathons/RELAY
    uv sync --group dev

Create a local .env using .env.example and fill in long unique values for
DEMO_TOKEN and RELAY_WORKER_TOKEN. Never commit it.

Start the API (terminal 1):

    set -a; source .env; set +a
    uv run uvicorn relay.app:app --host 127.0.0.1 --port 8000

Start the simulated outbound worker (terminal 2):

    set -a; source .env; set +a
    uv run python -m relay.worker

Send a demo request using the demo bearer token (terminal 3):

    curl -sS http://127.0.0.1:8000/demo/message \
      -H "Authorization: Bearer YOUR_DEMO_TOKEN" \
      -H 'Content-Type: application/json' \
      -d '{"incident_id":"trial_1","text":"Is there a weather alert for 92037?"}'

Read the result:

    curl -sS http://127.0.0.1:8000/demo/incidents/trial_1 \
      -H "Authorization: Bearer YOUR_DEMO_TOKEN"

Tests:

    uv run python -m pytest -q

No GPU/model endpoint is loaded unless MODEL_BASE_URL is set. Without it,
worker replies are visibly labeled "RELAY simulation."

## 2. MoLab RTX PRO 6000 Blackwell

The prior NeuroLoop MoLab URL responded HTTP 410 (sandbox terminated). Create a new
MoLab notebook and attach the 96GB NVIDIA GPU. Pair with your agent through the
new notebook's marimo pair prompt, not with a terminated notebook address.

Upstream supported serving route (vLLM-Omni >=0.21.0, Linux, GPU):

    # In a vLLM-Omni environment in the vllm-omni repo:
    vllm serve openbmb/MiniCPM-o-4_5 --omni \
        --trust-remote-code \
        --chat-template vllm_omni/transformers_utils/chat_templates/minicpmo45_native.jinja \
        --chat-template-content-format openai \
        --host 127.0.0.1 --port 8099

Use the official recipe for the appropriate dependency installation:
https://github.com/vllm-project/vllm-omni/blob/main/recipes/OpenBMB/MiniCPM-o-4_5.md

Check the local server:

    curl -sS http://127.0.0.1:8099/health

Start relay.worker on the same GPU machine with its package accessible and:
RELAY_API_URL=https://YOUR_RELAY_PUBLIC_GATEWAY
RELAY_WORKER_TOKEN=same as gateway
MODEL_BASE_URL=http://127.0.0.1:8099
MODEL_NAME=openbmb/MiniCPM-o-4_5
RELAY_WORKER_CONCURRENCY=2

Use the existing pair-connected marimo process to install package dependencies
and start background processes; the notebook is the control plane, NOT the public
webhook endpoint. MoLab sessions are time-limited; their shutdown is NOT a production
availability guarantee. No persistent GPU process is running until this step succeeds.

## 3. Public gateway (for Twilio and MoLab)

The Mac gateway requires a stable publicly reachable HTTPS hostname. Cloudflare
Tunnel is an option if you have configured a tunnel and DNS record. Only expose the
authenticated FastAPI gateway, never the raw GPU model port.

Set PUBLIC_BASE_URL=https://YOUR_PUBLIC_DOMAIN (no trailing slash).
Configure the Twilio number with POST webhooks:
  - Voice: /twilio/voice
  - SMS:   /twilio/sms

Set TWILIO_AUTH_TOKEN so Twilio signature validation is enforced. Confirm inbound
number capabilities and trial rules in your own Twilio Console first.

Trial Voice: speech input uses TwiML Gather and reply uses Say. There is NO Twilio
Media Stream, no native full-duplex audio, and only a bounded poll/redirect loop.
Try this with your own verified test number.

Trial SMS: dynamic outgoing text is not guaranteed and is prohibited on new
free trial template restrictions. TWILIO_LIVE_SEND=false is the default. Setting
it true is an EXPLICIT paid/capability-bearing operation and should only be done
after verifying the account and carrier registration. The outbox distinguishes
simulated, accepted_by_provider and carrier_rejected. "Accepted" does not
mean delivered/read.

## 4. Mobile camera, both platforms

An authorized operator issues a signed ten-minute link with:
POST /demo/incidents/INCIDENT_ID/camera-link
using the demo bearer token. RELAY can send this through SMS when custom text is
permitted. Opening the link requests browser camera permission (NOT automatic).
Press "Analyze one frame" to send a compressed JPEG to the same incident.

Only a single captured frame is supported in this starter. Live video calling,
continuous sampling, interactive visual speech and native FaceTime or Meet
bots are NOT yet implemented. Add those after the phone/SMS core is stable.

## 5. Emergency information

The worker recognizes a US ZIP code and performs an approximate ZIP-centroid lookup,
then queries NWS active weather alerts. Missing or failing sources never mean safe
conditions; local evacuation orders require separate local-authority data.
The selected location is a rough centroid, NOT a reliable caller GPS location.

## 6. Security and capacity notes

- Twilio-signed calls and SMS only; unauthenticated webhook requests are rejected.
- Worker token is independent from demo token; no secrets in Git.
- One leased job per incident; retries and lease expirations are bounded.
- Dynamic outbound contact sending is off by default.
- Camera tokens expire after ten minutes. Frames are temporarily stored as jobs in
  the local SQLite demo database; use a strict deletion/retention policy before
  involving real people.
- SQLite WAL is for a small single-host prototype. Replace with a networked durable
  queue/database for distributed always-on application servers.
- Callback timeouts, Twilio trial restrictions and MoLab notebook shutdowns prevent
  this version from being suitable for real emergencies.
- Simulated concurrency tests do NOT prove 96GB GPU throughput.
- Contact coordination/dispatch, MMS ingestion and continuous video are not shipped.
