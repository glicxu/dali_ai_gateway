# G6 Realtime Gateway Operational Readiness

Status: disabled AWS-US2 application rollout complete; shared infrastructure,
activation, and operational proof remain pending.

## Automated gates

Run from the Gateway repository:

```powershell
python -m compileall -q app tests
python -m pytest -q
python -m scripts.export_openapi --check
```

The realtime pilot must additionally pass the v2 API, circuit, and contract
tests before deployment. Tests must use synthetic audio and content-free
assertions.

Generate the standard fixture with:

```powershell
python -m scripts.generate_synthetic_wav smoke.wav --seconds 2
```

## Pre-deployment checks

- Confirm the active release contains the expected commit and contract schemas.
- Confirm provider credentials are loaded only from the deployment secret file.
- Confirm caller/profile grants and circuit settings are explicit.
- Confirm the Host reserve and Classroom admission limits are unchanged.
- Confirm DynamoDB shared admission/circuit tables use a string `state_key`
  partition key, admission TTL on `expires_at`, least-privilege IAM, and the
  required-store flags enabled.

## Realtime outage drill

1. Start a v2 session with a primary and approved fallback profile.
2. Inject a synthetic provider failure after one accepted audio chunk.
3. Verify `window.failed` reports `provider_stream` and `partial=true`.
4. Verify `provider.switched` opens the next window without replaying the
   accepted chunk.
5. Verify `usage.final` and `session.closed` are emitted on stop.
6. Verify the failed provider route circuit records the normalized failure.

No transcript, audio, token, user, or provider payload may appear in logs.

## Remaining G6 gates

- Two Gateway instances with shared admission/circuit/usage state.
- Load, soak, slow-consumer, planned-drain, and forced-termination drills.
- Dashboards and alerts for capacity, circuits, fallback, readiness, and usage.
- Deployment, rollback, credential rotation, and provider-outage runbooks.
- Named owners and approved SLO thresholds.

Deployment checkpoint (2026-09-02): release `20260902T145256Z` passed all 145
tests and the checked OpenAPI export on the ARM aws-us2 host. The active v6
generation preserves the existing Classroom and Dali Chat grants and keeps
`interpreter_server_ai.enabled=false`; liveness/readiness pass and both existing
providers report healthy. The host has no configured Phase 6 DynamoDB state
table or SQS usage queue, so required shared-store/delivery flags remain off and
multi-instance drills have not yet been executed.
