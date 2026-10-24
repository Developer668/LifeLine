# Verification evidence · 2026-10-24

Build workspace: LifeLine, bootstrapped from the existing sibling RELAY starter. Baseline nine tests passed before changes. Multiple workstreams committed independently; the integration lead owns final assembly.

## Completed local and public checks

- `uv sync --group dev`: reproducible lockfile/environment installation succeeded.
- `uv run pytest -q`: **87 passed**, no failed tests; one upstream FastAPI/Starlette TestClient deprecation warning. Includes baseline, official data, model parsing/failure bounds, signed phone contracts, secure linking/history, incident isolation, camera validation/revocation, contact consent/fallback, durable outbox and SMS crash recovery.
- `uv run python -m compileall -q relay scripts`: passed.
- Camera browser JavaScript `node --check`: passed. Actual browser layout at390px: content width390px, no horizontal overflow; analysis disabled initially. Physical camera permission/capture not asserted.
- Public HTTPS `/health`:200; unauthenticated worker claim and private incident access:401. Tunnel encrypted connection established through Cloudflare. Temporary development gateway, no uptime guarantee.
- Live NWS coordinates32.8328,-117.2713: available, one alert, root updated2026-10-24T22:05:07Z, measured2.029s.
- Live USGS same point: available, eight events, generated2026-10-24T22:09:48Z, measured0.492s.
- Three consecutive real HTTP backup rounds:0.429s,0.629s,0.423s. Every response explicitly said simulation and no GPU inference. No carrier path exercised.

## Measured queue/isolation load

Command: `uv run python scripts/load_test.py`. In-process HTTP routing and persistent SQLite, concurrent submissions and eight simulated completion workers. **These are queue measurements, not GPU inference or carrier capacity.** Latest local run:

| Users | Completed | Errors / isolation failures | Full round-trip batch time | Enqueue p95 |
| --- | --- | --- | --- | --- |
|5|5|0 / 0|0.052s|22.74ms|
|10|10|0 / 0|0.108s|50.62ms|
|25|25|0 / 0|0.253s|79.39ms|
|50|50|0 / 0|0.437s|105.49ms|

Machine load and repeated runs vary. Reproduce and inspect local `reports/load.json`; do not present this as an emergency-service performance guarantee.

## Live integration gates still being evaluated

- MoLab confirms RTX PRO6000 Blackwell Server Edition,97887MiB GPU memory, driver595.71.05. Package stack and MiniCPM weights installed; actual pipeline initialization underway. Hardware observation alone does not verify inference.
- Twilio credentials verified: account active/Trial. API provisioning returned21404; a shared trial Voice number and recipient were subsequently supplied. One45-second real call is authorized and pending real GPU readiness. No actual phone call/SMS or provider delivery asserted yet. See TWILIO_TRIAL.md.
- Physical iPhone Safari and Android Chrome camera permissions/capture remain unverified. API capability and image validation tests passed; mobile HTML uses playsinline, environment-facing camera, explicit start, per-frame submission, visibility pause and stop/expiry.
- Model-generated audio playback over Twilio is not enabled. Default spoken output is Twilio Say.

No failed test is concealed by a simulation fallback. Outbound text simulation is explicit; unconfigured real inference refuses to run. Uncertain carrier submission is recorded separately and never blindly retried.
