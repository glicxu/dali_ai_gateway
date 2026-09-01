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

Realtime translation callers may select any non-empty subset of
`source_transcript`, `target_transcript`, and `translated_audio` in the optional
v1 `session.start.outputs` field. Omitting it preserves target transcript plus
translated audio. Source transcript text is emitted with `transcript.*` events;
translated text remains `translation.*`.

The versioned v2 realtime schemas add monotonic input sequence numbers,
window identifiers, accepted-input watermarks, normalized translated-audio
events, and explicit provider-switch/terminal events. The supported pilot
policies are `single`, `windowed_failover`, and the test-only
`windowed_alternate`; concurrent comparison is out of scope. Automatic routing
is Gateway-owned and occurs when opening the next approved 60/90/120-second
window; accepted audio is not replayed.

`examples/realtime-translation-v2-session-start.json` is the canonical
windowed-failover request for the Dali Chat interpretation pilot. It demonstrates
an OpenAI primary, Gemini fallback, and explicit source/target transcript plus
translated-audio outputs.
