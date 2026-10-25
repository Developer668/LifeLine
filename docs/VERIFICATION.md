# Verification evidence · 2026-10-24

LifeLine was bootstrapped from the existing sibling RELAY starter. Its nine baseline tests passed before changes. Work now proceeds with one agent only, as recorded in AGENTS.md.

## Confirmed

- `uv run pytest -q`: **124 passed**, zero failures; one upstream FastAPI/Starlette TestClient deprecation warning.
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

## Original-plan phone workflow completion pass

The Bandwidth adapter now supports one-time, phone-bound voice/SMS association using the existing secure link mechanism. Normal follow-up questions inherit only the explicitly linked incident. Tests verify mismatched-phone rejection and unlink behavior. Individually spoken five-digit ZIP codes are normalized before official lookups.

Trusted-contact enrollment, participant consent, owner approval, acknowledgement, decline, one approved fallback, reported assistance, revocation and status work through SMS commands. Model-generated text cannot authorize these actions. Bandwidth contact requests contain SMS reply codes, not web response links. Multipart delivery is reported as delivered only after every part is delivered. Duplicate command callbacks do not duplicate enrollment. Optional CAMERA sends a short capability only when requested; its redirect rechecks expiry and stop/revocation.

One previously authorized bounded carrier call was attempted via Bandwidth. HTTP 402 explicitly rejected creation because the previously approved target is not the mobile verified on this Bandwidth trial. No actual call was created; the call script records submission intent before attempting and will not automatically retry. A verified-mobile handset interaction remains necessary for carrier verification. The recipient's new number must be supplied/authorized before a different outbound target is used. Build restrictions also prevent live trusted-contact delivery to other numbers; the guarded state machine can be verified independently without claiming a live send.

A rate-limit regression check initially caught a missing SQLite COMMIT on failed phone-code attempts; explicit transaction completion fixed it. All current workflow tests pass. Native MiniCPM duplex audio, physical iPhone/Android camera capture and three full successful carrier demo runs remain unverified.

The latest deployed two-question raw-audio/GPU check passed in 11.94 seconds with 133 outgoing audio packets and four clear events. It is explicitly synthetic carrier transport, with real ASR/TTS/GPU and no audio files. An outbound answer event without a direction field now binds the callee, with a regression test.

## Relay identity and handset evidence

The user reports successful calling and texting from their handset. Gateway records confirm one non-fixture Bandwidth call connection and two delivered SMS outbox items. These establish real carrier interaction; they do not establish public access from arbitrary numbers or three consecutive scripted demo runs. The previously approved outbound test target remains rejected by Bandwidth's verified-mobile trial restriction.

Relay now has one explicitly AI identity across calls and SMS. The 48-word call greeting explains official alerts, approved-contact requests, why fast clear information matters, and the inability to dispatch emergency help. Short SMS greetings use two trial-size messages. HELP and explicit purpose questions explain the same role. Critical-risk messages bypass onboarding. Ordinary questions keep a direct conversational answer without repeated introductions.

118 tests pass. Public worker checks returned the purpose response in 1.24 seconds and a validated real GPU answer in 2.31 seconds. No carrier call or SMS was initiated by those checks. Local evidence: reports/persona.json.

## Carrier-cost pass

Removed the remaining Bandwidth SpeakSentence fallback. The working phone path invokes only call control, one bidirectional audio stream and cellular SMS; speech recognition, synthesis and reasoning run on our existing machines. The trial prefix is provider-added and mandatory. Single-message onboarding now fits one 129-character trial text, with HELP in two. 120 tests pass, including a paid-feature BXML allowlist and unavailable-model Hangup path. No call, text, billing upgrade or credit purchase was made during this pass. See COSTS.md for limits and published media-stream pricing, which is separate from basic calling.

## Bandwidth credential outage and recovery

A later outage was traced to Bandwidth OAuth HTTP 401 `invalid_client` with the previous credentials. The public gateway and real GPU worker remained healthy; a fresh model request completed while carrier authentication was failing. The user supplied a replacement secret for the same client. Authentication then succeeded, both voice/messaging callback configurations were reapplied and read back, and the assigned number's inbound routing remained enabled and bound to the expected application.

After restarting the gateway with the replacement configuration, public health reported healthy Bandwidth authentication, an authenticated empty SMS callback returned 204, and a new actual GPU job completed. No real call or SMS was sent by those recovery checks, and old failed/uncertain messages were not automatically replayed.

Readiness now records safe provider-authentication status (without credentials), reports degraded service on a detected authentication failure, and clears it after a successful probe. SMS authentication happens before multipart work and local budget reservation, so invalid credentials do not consume that allowance. The regression suite now passes 121 tests.

## Silent voice-call regression and correction

Two real user calls connected but produced zero outbound audio packets. One had 17 playback clears and four recognized turns; inference completed for some turns while every spoken response was cancelled. The gateway was incorrectly treating raw audio-energy edges (including background noise) as confirmed interruptions. A no-input WebSocket check independently demonstrated that local speech generation and outbound packet creation worked, isolating the cancellation path.

Barge-in now requires recognizable partial speech (or a clear short control word), once per utterance. Raw noise and low-information single-word partials no longer cancel pending synthesis or inference. Final recognized utterances continue through the queue and actual GPU worker. A bare stop/wait cancels speech without launching another inference request. Greeting synthesis failures are now observed and recorded, and a closed WebSocket no longer causes a secondary cleanup traceback. Telemetry records synthesized sentences, output failures, audio packets, VAD edges and synthesis time without recording caller audio.

122 tests pass, including a regression where repeated noise edges arrive during cold synthesis. A fresh public WebSocket drill with generated non-speech noise, real local ASR/TTS and the real GPU worker produced first audio in 1.04 seconds, 114 speech packets with zero clears during the noise phase, a completed inference job and 86 reply packets in the final two-second window. Peak decoded packet RMS was 11945; total output 38400 PCMU bytes. No carrier call, SMS or audio file was created. Local evidence: reports/noisy-voice.json. Reproduce with scripts/check_voice_audio.py. A new physical handset playback check was requested; synthetic packets alone do not confirm that audio was heard on the phone.

A physical retry after the first cancellation fix made the greeting audible but still did not yield replies. Its records showed 72 output packets, four completed/attempted recognition turns and eight clears; partial transcripts were garbled and inference was cancelled before playback. The second correction separates playback state from pending inference: partial recognition cannot cancel a model job, and interruption is applied only to active audible playback. Local Whisper base.en with WebRTC voice activity replaces Vosk as the default phone recognizer. Utterances remain in memory, are bounded to ten seconds, and use no provider transcription. Models warm before startup completes, with readiness exposing a startup failure safely.

The revised public audio test passed in 10.30 seconds: first audio 0.368 seconds, 145 packets/no clears during generated noise, a completed inference job and 92 answer packets in the final two-second window. Output was 63869 PCMU bytes, peak decoded packet RMS 11318. No carrier call or recording was created by this check. A second physical handset check was requested after these changes. Reproduce local model setup with scripts/setup_voice.py. The suite now passes 124 tests, including pending-inference protection and private-error-safe warmup failure readiness.
