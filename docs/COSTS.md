# RELAY carrier services and usage controls

Checked 2026-10-24 against the running implementation and official Bandwidth documentation.

| Component | Current implementation | Provider usage |
| --- | --- | --- |
| Phone number and regular calls | Bandwidth number and Voice API | Carrier charges / trial credits |
| Live call audio bridge | One bidirectional StartStream, inbound track only | Separate media-streaming usage |
| Cellular texts | Bandwidth Build SMS | Carrier charges / trial credits per text |
| Speech recognition and voice activity | Local Vosk | No external speech API meter |
| Spoken replies and interruption handling | Local Piper and gateway | No Bandwidth TTS meter |
| Reasoning and vision | MiniCPM-o 4.5 on existing MoLab GPU | Uses existing compute allocation |
| Queue, memory and contact workflows | FastAPI/SQLite on existing Mac | Existing machine resources |
| Official data | NWS / USGS public endpoints | No paid data provider configured |

No Bandwidth transcription, recording, conferencing, answering-machine detection, voice AI, call verification or emergency-dispatch service is invoked. The last provider SpeakSentence fallback was removed; if local audio models are unavailable, the call ends promptly rather than adding paid TTS or waiting silently. Tests enforce the BXML allowlist StartStream/Pause/Hangup and unavailable-path Hangup only.

The current HTTP/WebSocket connection needs a bidirectional media stream to connect telephone audio to our local models. Bandwidth lists this separately at $0.004/minute in its public pricing. This is a published reference price, not a measurement of this Build account's credit deductions or final invoice. Eliminating that feature would require a different transport, such as self-hosted SIP/RTP, and separate networking and real-phone validation. It has not been substituted into the working route.

Usage protections remain: 45-second default call limit, local speech/model processing, at most four short ASCII texts per answer, duplicate suppression, no automatic retries after uncertain carrier acceptance, one SMS every 10.2 seconds, and 24 SMS attempts/day. The greeting now fits one trial text; HELP fits two. No account upgrade, credit purchase, autorecharge change or carrier test send was performed for this cost pass.

Bandwidth adds the mandatory `Bandwidth Free Trial Message - ` prefix after submission. There is no supported trial parameter to suppress it. Build permits 129 characters of application text plus this prefix. The full messaging account removes the trial prefix but involves a different commercial account setup and sender registration; no such change was made.

Sources: [public pricing](https://www.bandwidth.com/pricing/), [media streaming](https://www.bandwidth.com/products/media-streaming/), [Build SMS rules](https://dev.bandwidth.com/docs/messaging/build-free-trial/).
