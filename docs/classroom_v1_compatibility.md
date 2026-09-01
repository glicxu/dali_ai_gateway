# Classroom v1 Gateway Compatibility Fixtures

Status: documented; compatibility behavior remains additive and unchanged.

## Routes

- `POST /ai/v1/text/generations`
- `POST /ai/v1/audio/transcriptions`
- `POST /ai/v1/audio/speech`
- `/ai/v1/realtime/transcriptions` WebSocket
- `/ai/v1/realtime/translations` WebSocket

## Contract sources

- OpenAPI: `contracts/openapi/ai-gateway-v1.json`
- Client events: `contracts/schemas/realtime-client-event-v1.schema.json`
- Server events: `contracts/schemas/realtime-server-event-v1.schema.json`
- Examples: `contracts/examples/`

## Compatibility rules

1. Existing v1 paths and event names are preserved.
2. New v2 sequencing, rotation, usage, and failover fields are not required by
   v1 callers.
3. Mobile and browser clients continue to call product servers, never Gateway.
4. v1 profile authorization remains workload/product/capability scoped.
5. Provider payloads, credentials, prompts, and user identifiers never appear
   in v1 responses or diagnostics.

## Verification

Run the Gateway quality gate and API tests:

```powershell
python -m compileall -q app tests
python -m pytest -q
python -m scripts.export_openapi --check
```

Any v1 contract change requires an explicit versioning decision and a separate
compatibility review.
