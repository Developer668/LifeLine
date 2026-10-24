# Actual trial status and constraints

Checked2026-10-24 against this account's authenticated API and current official docs.

Credentials authenticate; account is active/Trial. The account's incoming-number list is empty. The legacy outgoing-caller-ID list is also empty; the new product-specific verified-recipient flow is separate, so the legacy list does not prove that personal verification was not completed.

The user's Console supplied a shared Voice trial source and a recipient. They are stored only in ignored `.env`. This shared trial source is not returned as an owned incoming number and cannot be configured through `IncomingPhoneNumbers.update`.

A number-create request was rejected with HTTP400/code21404 and the account-specific message “Trial accounts must verify a phone number before purchasing a Twilio number.” The current [official21404 documentation](https://www.twilio.com/docs/api/errors/21404) additionally says REST number purchases require an upgraded account. Completing mobile verification is not evidence that the REST provisioning restriction has disappeared. Do not repeatedly try to buy numbers or upgrade the account.

Use [Try out Voice](https://www.twilio.com/docs/usage/trials/try-out-voice), Inbound, the verified recipient and a Custom scenario to configure the trial interaction. Its stock speech-recognition template is a Twilio demo, not RELAY. Actual support for RELAY's public webhook must be verified against the account; no carrier integration is claimed merely because a supplied curl command exists.

The [trial overview](https://www.twilio.com/docs/usage/trials) documents product free units rather than a dollar balance, shared trial numbers, geographic/recipient restrictions, and predefined outgoing SMS content. A0.0 balance does not mean free units are exhausted. Arbitrary reference texts remain labeled simulated; live sends stay disabled.

Custom trial Voice TwiML supports Gather/Say/Redirect/Pause with restrictions. The documented redirect/action chain is bounded at10 hops and webhook fetches at5seconds. RELAY queues inference and returns TwiML promptly; its poll loop uses fewer redirects and longer Pause durations. Actual call acceptance and callback execution remain separate verification gates.

A single45-second call to the supplied recipient was approved in chat. `scripts/test_trial_call.py --place` uses RELAY's real webhook, saves the accepted CallSid locally to prevent a second call, and does not substitute the stock template if custom instructions are rejected. It neither sends texts nor upgrades/purchases numbers.
