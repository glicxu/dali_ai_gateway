# Dali AI Gateway v1 contracts

The HTTP source is OpenAPI 3.1 in `openapi/ai-gateway-v1.json`. Realtime client
and server frames use JSON Schema 2020-12 under `schemas/` because OpenAPI does
not describe WebSocket message flow.

Compatibility rules:

- published fields are not repurposed;
- optional additive fields are preferred;
- callers ignore unknown optional server fields;
- raw provider errors and payloads never cross the boundary;
- bearer tokens, audio, prompts, and generated content never appear in errors
  or diagnostics;
- model profile names are stable policy identifiers; provider model IDs in
  responses are informational and not client-selectable.

