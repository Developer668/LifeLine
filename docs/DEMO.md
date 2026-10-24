# Reproducible demo and release gates

Use fictional situations only. A deterministic risk referral is a prototype feature, not evidence of medical accuracy or emergency response.

## Three consecutive backup runs

Start the gateway, then an explicitly simulated worker. Run `uv run python scripts/demo.py --runs 3`. Each result must say simulation and must be associated with its own incident. Inspect `reports/demo.json`. This verifies the HTTP queue/worker round trip, not a carrier call or GPU.

## Live GPU gate

1. Start a new MoLab session; record actual `nvidia-smi` output.
2. Install using the pinned GPU scripts and keep model port localhost only.
3. Verify health/model listing/text completion, then one JPEG and optional audio.
4. Run measured benchmarks at1,2,4 concurrency. Record successful requests, errors and measured latencies. Do not generalize model stage sequence settings to real call capacity.
5. Start the outbound worker with gateway URL and worker token in environment variables, not saved notebook code. Turn off all simulated workers.
6. Submit a fictional operator message; inspect the real response and worker completion. Repeat three times.

## Twilio gate (requires supplied account)

1. Set account/token/number and public HTTPS base; restart gateway.
2. Configure incoming SMS and voice POST URLs.
3. With the user's chosen verified test phone, make one inbound call and send one inbound SMS. Confirm no cross-user memory.
4. Ask for link code during call, redeem it by text from the same phone, then ask a follow-up. Check shared context only after redemption.
5. Explicitly approve the actual test reference text and recipient. Enable live sending, test your account's personalized content restrictions, and inspect provider acceptance/error and delivery callbacks. Do not equate acceptance with delivery.
6. If the account rejects reference text, show the labeled generated outbox as a fallback and record that real outbound SMS did not pass.
7. Repeat the complete supported scenario three times. Real camera permission/device testing on iPhone Safari and Android Chrome remains separate from browser/API contract tests.

## Failure checks

- Wrong/missing webhook signature ->403; missing operator/worker auth ->401.
- Forged/expired/revoked camera link ->403; wrong-incident observation ->404.
- Duplicate SMS/voice turn ->one side effect.
- Worker lease expiry -> bounded reclaim; stale lease cannot finish.
- Missing model never silently simulates; provider errors do not claim text delivery.
- NWS/USGS failure states explicitly unavailable; no invented alerts.
- Unconsented contact or unapproved request ->no send; decline ->one approved fallback; acknowledged != assisted.

The release is not fully verified until actual GPU, carrier, device and consecutive complete-run gates all pass. Record blocked gates precisely instead of checking them off.
