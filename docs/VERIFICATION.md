# Verification evidence · 2026-10-24

LifeLine was bootstrapped from the existing sibling RELAY starter. Its nine baseline tests passed before changes. Work now proceeds with one agent only, as recorded in AGENTS.md.

## Confirmed

- `uv run pytest -q`: **102 passed**, zero failures; one upstream FastAPI/Starlette TestClient deprecation warning.
- `uv run python -m compileall -q relay scripts` and `git diff --check`: passed.
- Real MoLab hardware: NVIDIA RTX PRO6000 Blackwell Server Edition,97887MiB, driver595.71.05. No Mac GPU inference was used.
- Real MiniCPM-o4.5 health, model listing, text completion, validated structured proposals, and image completion passed. Simulation disabled.
- Three GPU-backed HTTPS round trips passed initially in1.952,1.861,2.124seconds. Final source/reference integration runs passed in1.422,4.160,1.426seconds; the middle request shared the queue with the camera drill. These are HTTP/model tests, not telephone calls.
- A synthetic red-square JPEG traveled through the actual public camera API and GPU, returned the correct description in3.225seconds, then Stop revoked its capability (HTTP403). Physical iPhone/Android camera capture is not asserted.
- Actual NWS and USGS lookups succeeded with source timestamps. A final signed synthetic SMS HTTP fixture used actual USGS data (8 records), archived the official snapshot and prepared a reference with source time, retrieval time and event links. It completed in2.183seconds; its outbox was explicitly simulated, with no carrier send.
- Signed webhook, secure voice/SMS association, duplicate handling, source/incident isolation, consent, contact fallback, bounded lease recovery, uncertain-provider no-retry, media validation, source snapshots and malformed model outputs are covered by tests.
- Public HTTPS health returns200; private worker/incident routes reject unauthenticated access. A detached launcher keeps gateway/tunnel processes alive beyond a tool session. The hostname remains a temporary Cloudflare development hostname.

## GPU measurements

`python scripts/benchmark_gpu.py --requests 4` ran on the GPU host, with a warmup excluded. Twelve short text requests total, all successful. The requests asked for one brief sentence; these are not full conversational workloads or sustained capacity measurements.

| Concurrent requests | Successful / total | p50 | p95 |
| --- | --- | --- | --- |
|1|4 / 4|0.389s|0.417s|
|2|4 / 4|0.501s|0.596s|
|4|4 / 4|0.641s|0.665s|

The default upstream duplex profile was replaced with an official turn-mode overlay admitting four sequences per stage. The worker conservatively uses one in-flight job. Limits are not claims of actual call capacity.

## Queue/isolation measurements

`uv run python scripts/load_test.py` uses in-process HTTP/SQLite and simulated completions. All5,10,25,50 users completed, with zero errors/isolation failures. Latest batch durations were0.052,0.108,0.253,0.437seconds. These measure the queue, not the GPU or carrier.

Three backup HTTP rounds also passed with explicitly simulated responses in0.429,0.629,0.423seconds. They are distinct from the actual GPU tests above.

## Failures and remaining gates

- Upstream startup initially selected a host NVIDIA compiler incompatible with CUDA13.0 headers; then exposed missing runtime linker names. Matching compiler pins, explicit environment paths and pip-wheel library links repaired those failures.
- The pinned Omni vocoder adapter omitted the reference StepAudio package's module-alias initialization. A preserved, reproducible source patch invokes that helper before YAML construction. All three model stages subsequently initialized.
- Initial structured output used an invalid enum; it was rejected, then explicit schema/example prompting repaired the demonstrated path. Invalid proposals continue to fail closed.
- Live checks exposed plural hazard and sentence-punctuation coordinate parsing defects. Both were fixed with regression tests. Missing/unavailable official data has deterministic wording and cannot become an empty-alert claim.
- Optional MiniCPM-generated audio hit the bounded read timeout; it is **not verified** and not used for phone output. Twilio Say remains the existing carrier speech path. Local Piper is installed and verified for file-free telephone audio generation. The temporary browser conversation/preview endpoints were removed to match the phone/SMS-only product requirement.
- Twilio credentials authenticate as an active Trial account. Number provisioning returned21404; a shared trial number and recipient were supplied afterward. One approved bounded call request returnedHTTP400 for disallowed trial parameters **before creating a call**. No real carrier call, inbound SMS, or delivered outbound text is claimed. No number was purchased and no paid upgrade was performed. See TWILIO_TRIAL.md and FREE_ALTERNATIVES.md.
- Contact consent/acknowledgement/fallback is tested with simulated sends; actual contact delivery is unverified. Acknowledgement is never completed assistance.
- Physical iPhone Safari/Android Chrome capture, real cross-channel carrier linking, and three consecutive complete carrier demos remain unverified.

Local JSON evidence is under reports/: gpu-text.json, gpu-demo-final.json, gpu-weather.json, gpu-camera.json and gpu-reference.json. Never present this prototype as a monitored emergency hotline or mark the full release checklist complete while the carrier/device gates remain outstanding.

## Bandwidth phone and SMS integration

OAuth credentials authenticated successfully. Existing trial voice and messaging applications were configured with authenticated HTTPS callbacks and read back successfully. The assigned number is active and its voice configuration points to that application. No number was purchased, no outbound carrier call was placed, and no SMS was sent during configuration.

A public HTTPS/WebSocket test used synthetic carrier messages with actual local Piper speech, actual Vosk transcription and the actual GPU worker. The first round trip completed in 5.99 seconds. A subsequent two-question conversation completed in 11.92 seconds, produced 139 outgoing audio packets and four playback-clear events, and created no audio files. A local ASR/TTS check transcribed the generated emergency-kit question exactly; detected speech at 0.16 seconds in that fixture. These are synthetic-audio measurements, not measured human interruption latency. Evidence is local in reports/bandwidth-stream.json.

Tests cover callback authentication, account/application scoping, one-use stream capabilities, hard call duration, stale audio cancellation, inbound-only recognition, direct ASCII message formatting, duplicate inbound messages, opt-out, durable send claims, no uncertain retry, and terminal delivery receipts. MiniCPM sometimes imitated plain-text conversation history instead of returning JSON; a final per-turn format reminder was added and schema validation remains mandatory. Physical phone calls, acoustics, end-to-end SMS delivery and real trusted-contact delivery remain unverified. The discarded browser voice interface is not part of the product.

A signed synthetic Bandwidth SMS event traveled through the public callback, actual GPU worker and direct-text formatter. The response fit one 129-character trial text; its outbox was explicitly simulated before live replies were enabled. No carrier SMS was sent. Evidence: reports/bandwidth-sms.json. Live SMS replies are now enabled for genuine incoming texts, with the 24/day local cap and provider trial recipient restriction; actual handset receipt remains unverified.
