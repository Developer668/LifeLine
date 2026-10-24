# RELAY

Phone-first emergency information and consenting trusted-contact coordination. FastAPI + durable SQLite gateway, Bandwidth live voice/SMS (legacy Twilio adapter retained), outbound MoLab worker, MiniCPM-o 4.5, NWS and USGS context, optional browser camera frames.

**An unmonitored information prototype. RELAY does not dispatch emergency services, diagnose, confirm safety, or guarantee assistance.** The immediate-risk referral works without model availability. The user flow is ordinary phone calls and direct SMS; no website or browser conversation is required. Live carrier operation requires Bandwidth configuration. See [verification status](docs/VERIFICATION.md) for measured evidence and remaining gates.

## Start locally

```sh
uv sync --group dev
uv run python scripts/setup_local.py
uv run python -m relay serve
```

The setup creates private, ignored `.env` secrets once. Existing files are preserved. The gateway listens on loopback port8000 and disables access logs so capability tokens do not enter URL logs. Configuration changes require restarting the gateway.

For an explicitly labeled CPU backup in another terminal:

```sh
RELAY_ALLOW_SIMULATION=true uv run python -m relay worker
uv run python scripts/demo.py --runs 3
```

The default worker **refuses** to substitute simulated inference when a model is missing. To use MoLab, follow [GPU setup and verification](scripts/gpu_README.md). Keep the model listening on MoLab localhost; only the worker connects outbound to this gateway. Never run a simulated worker alongside a real worker during live verification.

## Bandwidth phone setup

Install local streaming speech dependencies with `uv sync --extra voice --group dev`. Set the Bandwidth credentials, account ID, assigned number, public HTTPS callback address, and local Vosk/Piper model paths in `.env`. Run `uv run --extra voice python scripts/configure_bandwidth.py --apply` to configure the existing trial voice and messaging applications. It does not buy a number, call anyone, or send a text. Original callback snapshots are saved privately under `.runtime/`.

Incoming calls use a two-way Bandwidth WebSocket: Vosk transcribes inbound audio locally; the real MoLab worker answers; Piper sends raw telephone audio packets directly into the call. Speech detection cancels reply playback and clears the carrier buffer when the caller interrupts. No WAV file is created or downloaded. The model still reasons on completed utterances; this is an interruptible phone pipeline rather than native MiniCPM duplex audio.

To share a call with SMS, say **link my texts** and send `LINK CODE` from the calling phone. The code expires after five minutes and is single-use; the authorized association lasts one hour. `UNLINK` separates the channels. Caller ID alone never joins histories.

Trusted-contact setup and decisions work over SMS: `ADD CONTACT Name +1number`, privately share the returned `JOIN CODE` instruction with that participant, then `REQUEST Name: message`. Optional `REQUEST Name FALLBACK Other: message` pre-approves one fallback if the first participant declines. Nothing is sent until the owner returns `APPROVE CODE`. Participants reply `ACK CODE`, `DECLINE CODE`, or `ASSISTED CODE`; acknowledgement is receipt only. `REVOKE ALL` withdraws that phone's contact consent. `STATUS` reports actual delivery and participant state. This does not remove the carrier trial's verified-recipient restriction.

`CAMERA` is optional and explicitly requested. It texts a short ten-minute camera capability, then returns to the same incident; calls, information answers, references, contacts and approvals require no website. Camera access is never requested automatically.

Calls default to a hard 45-second demo cap, enforced in both BXML and the gateway. SMS sends require `BANDWIDTH_LIVE_SEND=true`. Direct answers use at most four numbered ASCII texts of 129 characters each, with a source label for official lookups. The app limits sends to one every 10.2 seconds and 24 per day; uncertain submissions are never retried automatically. `STOP` opts out and `START` opts in. A caller can request a text summary explicitly. Incoming text messages authorize a reply to that sender.

Bandwidth Build restricts calls/messages to the verified trial mobile and prepends its trial message prefix. The self-hosted speech components have no per-minute external speech API meter. See [verification status](docs/VERIFICATION.md) for actual evidence; synthetic audio transport tests are not carrier verification.

## Legacy Twilio setup

Set these values in `.env`, then restart:

```dotenv
PUBLIC_BASE_URL=https://your-public-host
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_NUMBER=your_twilio_number
TWILIO_LIVE_SEND=false
```

For the current shared-number trial flow, read [the account-specific trial findings](docs/TWILIO_TRIAL.md). Shared trial sources do not appear as owned incoming numbers; the Console trial Custom scenario is a separate configuration path. The account may reject REST number provisioning even after mobile verification.

For a number you own, configure:

| Purpose | POST webhook |
| --- | --- |
| Incoming SMS | `https://your-public-host/twilio/sms` |
| Incoming voice | `https://your-public-host/twilio/voice` |
| Delivery receipts | Set automatically per outbound send to `/twilio/status?outbox_id=…` |

Every webhook verifies the Twilio signature against the configured public URL, including query parameters. A reverse proxy must preserve paths, query parameters and request bodies. Caller ID alone never links voice and text context.

1. Call and ask your question. Trial voice uses Gather/Say; it polls only the current turn's job with a bounded wait.
2. To share voice context with SMS, ask to **link text context**. Send `LINK CODE` from the calling phone to the Twilio number within five minutes. The ten-character code is single-use; the association expires in one hour.
3. Ask a question by SMS. Retries with the same MessageSid do not create another job.
4. Text `CAMERA`, or say “text me a camera link,” for a private ten-minute visual sharing link. Open only when safe. Choose Start camera, then Analyze one frame. Stop sharing revokes the link.
5. `STOP` disables app-initiated texts to that phone; `START` enables them again.

Inbound MMS attachments are not downloaded. An MMS without text receives instructions to use the secure browser camera path. Individual JPEG frames are supported; continuous video and native meeting bots are outside this build.

Outbound texts are simulated until `TWILIO_LIVE_SEND=true`. States distinguish prepared, simulated, submitting, provider acceptance, confirmed delivery, failure, and unknown acceptance. Prepared replies recover automatically after restarts; uncertain carrier submissions require reconciliation and are never blindly repeated. Acceptance is not delivery or reading. A trial account may restrict recipient numbers or personalized content: test your actual account before presenting a real outbound reference message. Never describe simulated outbox entries as sent texts.

## HTTPS

A Cloudflare Quick Tunnel is suitable for development. The combined launcher starts the tunnel, saves the public URL, then starts the gateway:

```sh
uv run python scripts/serve_public.py
```

Alternatively, start the tunnel separately:

```sh
cloudflared tunnel --url http://127.0.0.1:8000
```

Copy the generated HTTPS URL to `PUBLIC_BASE_URL`, restart the gateway, and update Twilio webhooks. Each tunnel has a temporary URL; keep the process running. Signed Twilio webhooks, bearer-protected worker/admin routes, and scoped camera/contact capabilities authenticate at the application layer. Interactive Cloudflare Access login must not intercept Twilio callbacks or the outbound worker.

For a stable hostname, use a named Cloudflare Tunnel with [the template](deploy/cloudflared.yml.example). [Docker Compose](deploy/compose.yml) provides a persistent-volume gateway and token-based tunnel. Configure its public hostname to `http://gateway:8000` in the tunnel dashboard; the local example uses `http://127.0.0.1:8000`. Public model ports are unnecessary.

## API and contacts

Operator/demo routes require `Authorization: Bearer $DEMO_TOKEN`; worker routes require the separate `$RELAY_WORKER_TOKEN`. These credentials grant operator access and must not be distributed to callers. There is no public incident-list API.

| Route | Function |
| --- | --- |
| `POST /demo/message` | Queue `{incident_id,text}`; immediate risks return a deterministic referral |
| `GET /demo/incidents/{id}` | Inspect that incident's jobs/outbox |
| `POST /demo/incidents/{id}/camera-link` | Issue a scoped, expiring, revocable camera capability |
| `POST /demo/incidents/{id}/contacts` | Enroll `{name,phone}`, returning a private consent URL |
| `GET /demo/incidents/{id}/contacts` | Inspect contact consent/request states |
| `POST /demo/incidents/{id}/contact-requests` | Approved request to an enrolled participant |
| `POST /worker/claim` | Atomically lease one job; same-incident jobs serialize |
| `POST /worker/jobs/{id}/complete` | Complete with exactly one of result/error and current lease |
| `GET /health` | Gateway health only; never claims untested GPU/carrier readiness |

A trusted contact must open their private enrollment link and explicitly consent before requests can be sent. The enrolling user shares invitations; RELAY does not send unsolicited invitations. Contact requests require `approved:true`, `contact_id`, `message` and a unique `idempotency_key` (8–100characters). An optional `fallback_id` preapproves one other consenting contact. Declining triggers at most one fallback. An acknowledgement means receipt; assistance must be separately reported after acknowledgement. Participants can revoke consent using their enrollment link. Contact actions are operator-approved API workflows; free-form model output cannot trigger them.

## Data, isolation and failure behavior

NWS weather alerts and USGS earthquake reports include source URLs, source/retrieval timestamps, geographic relevance, and explicit stale/unavailable states. ZIP locations are approximate centroids. Empty alert results never establish safety. No generic web search is substituted for official emergency feeds.

SQLite WAL survives worker/notebook restarts. Claims carry unguessable expiring leases; late completions fail. Three exhausted attempts become a terminal failure. Only same-incident history enters an inference payload; a call poll cannot consume another call's or a camera job's answer. Image bytes are removed from completed job payloads. Camera capabilities are hashed at rest, expire after ten minutes, limit frame submission, and revoke on Stop sharing.

The model adapter accepts bounded text/image requests and parses optional audio from independent output choices. GPU audio generation is separately verified before any use. Default phone speech uses Twilio Say; model-generated audio is not served through Play in this release.

## Verify

```sh
uv run pytest -q
uv run python scripts/load_test.py
uv run python scripts/demo.py --runs 3
```

Load results distinguish HTTP/SQLite queue processing from GPU throughput. GPU-only measurements use `scripts/benchmark_gpu.py` at concurrency1,2,4. Reports under `reports/` are local evidence, not production capacity promises. Follow [the demo and release checklist](docs/DEMO.md). For the current carrier/audio blockers, see [free alternatives](docs/FREE_ALTERNATIVES.md).

## Source layout

`relay/app.py` handles phone webhooks and operator APIs; `store.py` persists jobs, identity associations and outbox state; `providers.py` handles carrier calls. `engine/` normalizes official data and validates action plans. `coordination.py` owns consent and contact state. `media.py` and `camera.html` implement optional frames. `model.py` and `worker.py` run on MoLab. Shared public contracts are recorded in [CONTRACTS.md](docs/CONTRACTS.md).
