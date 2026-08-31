# Dali AI Gateway v1 contracts

The HTTP source is OpenAPI 3.1 in `openapi/ai-gateway-v1.json`. Realtime client
and server frames use JSON Schema 2020-12 under `schemas/` because OpenAPI does
not describe WebSocket message flow.

Compatibility rules:

- published fields are not repurposed;
- optional additive fields are preferred;
- callers ignore unknown optional server fields;
- raw provider errors and payloads never cross the boundary;
- bearer tokens, audio, image/video media, prompts, and generated content never appear in errors
  or diagnostics;
- model profile names are stable policy identifiers; provider model IDs in
responses are informational and not client-selectable.

`schemas/usage-measurement-v1.schema.json` is generated from the strict,
content-free internal measurement envelope by
`python -m scripts.export_openapi`. Its presence does not select a durable sink
or make measurements authoritative for billing or quota.
