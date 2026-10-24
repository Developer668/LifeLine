# Free alternatives reviewed · 2026-10-24

## Best match for the actual phone demo: Bandwidth Build

The current [official Build guide](https://dev.bandwidth.com/docs/voice/programmable-voice/build-free-trial/) offers a pre-provisioned U.S. number for voice and messaging, 3,000 trial credits, and custom voice applications without a credit card. It supports inbound/outbound calls and speech-related workflows. Calls/messages are restricted to the account's verified mobile; this is a trial, not permanently free carrier service.

[Build SMS documentation](https://dev.bandwidth.com/docs/messaging/build-free-trial/) permits custom message text, with constraints: 30 days, verified recipient only, ASCII, 129 characters of app text plus the trial prefix, 6 sends/minute, 200/day, no MMS. A short secure reference link could carry RELAY's full sourced answer while respecting the SMS length limit. Live messaging to multiple trusted contacts remains outside this free trial.

[Sign up](https://www.bandwidth.com/build-sign-up/). Account creation, phone verification and API credentials are required before integration can be live-tested. No account was created, no terms accepted, and no charge or alternative-provider call was made during this research. Existing Twilio credentials do not work with Bandwidth.

## Failed model audio: local Piper TTS

[Piper](https://github.com/OHF-Voice/piper1-gpl) is an open-source local speech engine. It can replace the optional MiniCPM speech-generation stage while keeping MiniCPM as the reasoning/vision model. There is no external speech API meter; it runs on the existing host. Voice-model licenses and deployment resources still apply. This is a researched replacement, not an installed or benchmarked component in this build. Bandwidth's own speech output is another option within its trial credits.

## Ongoing browser calls/chat: self-hosted LiveKit

[LiveKit](https://github.com/livekit/livekit) supplies open-source WebRTC transport, including audio/video and data. Running on existing hardware avoids a carrier API subscription, but hosting/network resources still cost something. Browser calls and in-app messages would replace dialing a normal phone number and sending cellular SMS. A PSTN/SMS bridge would still need a carrier provider. This is an architectural option, not an implemented replacement in the current gateway.

## Keep the parts that already work

MiniCPM text and image inference on MoLab, official NWS/USGS adapters, outbound leased jobs, source snapshots, secure camera frames, and contact consent/state transitions are implemented. Current missing live gates are carrier calls/SMS, physical device capture verification, and optional model-generated audio. A new provider does not by itself prove those gates.
