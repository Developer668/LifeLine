# Frozen integration contracts
Preserve starter store API and existing routes. All additions backward compatible.
- Gateway lives in relay/app.py; store in relay/store.py; providers in relay/providers.py. Telephony agent owns these three files.
- Inference agent owns relay/worker.py, relay/model.py, scripts/gpu*, scripts/benchmark*, tests/test_model.py; no gateway/store edits.
- Engine agent owns relay/engine/, relay/coordination.py, tests/test_engine.py, tests/test_coordination.py; no gateway/store edits.
- Integration lead owns relay/media.py, relay/contracts.py, tests/integration*, scripts/demo*, docs, deployment.
- Engine entrypoint: build_context(text: str) -> str; policy_response(text: str) -> str | None; plan(text: str) -> validated ActionPlan. Engine I/O bounded and fail closed. Import from relay.engine.
- Worker claims POST /worker/claim returning {job: {id, incident, kind, lease, payload}}. Completes with {lease, result: string} or {lease,error: string}. Payload keys text/channel/sms_consent/image_base64/context/history. Gateway enriches history on claim.
- Shared model proposal contract in relay/contracts.py: spoken_response, reference_message {required,source,requires_consent}, next_action enum respond/get_weather_alerts/get_earthquakes/request_camera/contact_trusted, requires_user_confirmation. Model cannot perform external actions.
- Simulation always explicitly labeled. No real delivery claims before provider acceptance. All new tests use isolated temporary SQLite files and monkeypatch env.
- Camera helper will expose install_media(app) from relay/media.py; lead will replace starter camera route bodies after telephony done. Keep existing helpers until integration.

Completion additions: optional reference_text (<=1000 chars) and validated official_context (DataResult) preserve sourced reference wording separately from spoken output. These are persisted atomically with the outbox intent; source snapshots are visible only through authenticated incident inspection. Existing result/error completion shapes remain accepted.
