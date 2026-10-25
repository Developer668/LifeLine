# RELAY phone-only user flow

Call or text the same Bandwidth number. Calls use live audio with interruptions; text answers arrive directly by SMS. No browser is required for the core flow. Relay introduces itself as an AI emergency information assistant, explains why official information matters, and states its limits. HELLO, HELP, or “what can you do?” also explain its purpose; urgent messages bypass this introduction.

| Task | What the person does | Guard |
| --- | --- | --- |
| Ask for information | Call and speak, or send a normal SMS | Model output validated before presentation |
| Get official alerts | Ask for weather or earthquakes and give a ZIP code | Official source, freshness and location checks |
| Keep a call's context in SMS | Say “link my texts”; text LINK followed by the spoken code | Same phone, single-use code, five-minute expiry |
| Separate context | Text UNLINK | Removes the current association |
| Text a spoken answer | Say “text me that summary” | Explicit request; carrier acceptance differs from delivery |
| Add a trusted contact | ADD CONTACT Name +1number | No invitation is sent automatically |
| Participant consent | Participant texts JOIN followed by the privately shared code | Code bound to participant's registered phone |
| Prepare a contact request | REQUEST Name: message | Contact must have consented |
| Approve a fallback | REQUEST Name FALLBACK Other: message | Different consenting participant; one fallback only |
| Authorize sending | APPROVE followed by the proposal code | Bound to owner phone; expires after five minutes |
| Confirm receipt | ACK followed by the request code | Does not mean assistance completed |
| Decline | DECLINE followed by the request code | Can trigger only the approved fallback |
| Report assistance | ASSISTED followed by the request code | Requires prior acknowledgement |
| Withdraw consent | REVOKE ALL | Revokes that phone's contact enrollment |
| See delivery or help state | STATUS | Combines all SMS parts; does not infer help from a receipt |
| Stop texts / resume | STOP / START | Rechecked immediately before a send |
| Optional visual help | CAMERA, only if requested | Ten-minute revocable capability; explicit camera permission |

Bandwidth Build limits carrier interactions to its verified trial mobile. This is a provider restriction and is separate from RELAY's contact authorization. Do not mark live contact delivery or full phone verification complete from automated simulations.
