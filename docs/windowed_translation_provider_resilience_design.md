# Windowed Translation Comparison and Provider Resilience

Status: Approved; pilot implementation deployed to AWS-US2; full resilience
contract and operational gates remain in progress
Audience: Dali AI Gateway, Interpreter, product-service, security, and
operations owners  
Related: `multi_product_gateway_design.md`

Implementation readiness: local implementation may begin after the contract
fixtures and fake-provider test harness are added. Production enablement
remains gated by the multi-product implementation plan's G4–G6 exit criteria.

## 1. Purpose

This document defines a reusable Gateway capability for comparing live speech
translation providers and for using one provider as a bounded-window fallback
for another. The initial providers are Gemini Live Translate and OpenAI
Realtime Translate; the design is provider-neutral.

The design intentionally does **not** promise seamless mid-utterance
failover. A translation stream is divided into short, independently reported
windows (normally 60–120 seconds). After a provider has accepted content,
failover is automatic, but the Gateway changes providers only when opening the
next window. A failure may end the current window early; the Gateway then
opens the fallback window without requiring a client reconnect.

Interpreter and other product services remain responsible for user consent,
language selection, presentation, durable transcripts, and product workflow.
The Gateway owns provider transport, routing, normalization, admission, and
content-free operational measurements.

## 2. Goals

1. Preserve source and target transcript streams and translated audio without
   hiding provider-specific output.
2. Route production traffic as active-primary/standby-secondary without
   continuously paying for duplicate requests.
4. Fail over at a bounded window boundary with explicit transition metadata.
5. Keep provider API keys, raw audio, transcripts, and prompts inside the
   existing Gateway privacy boundary.
6. Reuse the same normalized event contract for Gemini, OpenAI, and future
   providers.

## 3. Non-goals

- Perfect continuity when a provider fails in the middle of an utterance.
- Gateway-owned prompts, terminology, language policy, or durable transcripts.
- Client-side provider credentials or direct mobile-to-provider connections.
- Automatic selection of a “better” translation based on subjective quality.
- Running both providers concurrently for one session.

## 4. Operating modes

### 4.1 Windowed failover mode (active-passive)

Only the selected primary provider receives a window. Gateway opens the
secondary provider for the next window when the primary is unavailable or
fails the configured health/latency policy.

```json
{
  "policy": "windowed_failover",
  "window_seconds": 90,
  "primary": "gemini_live_translate",
  "fallback": "openai_realtime_translate"
}
```

The policy must be configured by the product workload or Gateway operator;
end users cannot supply provider names or credentials.

### 4.2 Single-provider mode

Existing behavior remains available. This is the default when no comparison or
failover policy is requested.

## 5. Window lifecycle

Each window has a Gateway-generated `window_id` and monotonic sequence. A
normal lifecycle is:

1. `window.opening` — authenticate, authorize, admit, and establish the
   provider session.
2. `window.open` — stream source audio and normalized provider events.
3. `window.closing` — stop accepting new audio at the boundary and flush the
   provider session according to its protocol.
4. `window.closed` — emit final transcript/audio markers and usage metadata.
5. Open the next window using the selected route.

The initial Interpreter window is 90 seconds. The Gateway accepts only
operator-approved values of 60, 90, or 120 seconds and rejects other values.
A provider failure does not cause audio replay. The Gateway closes the failed
window, emits a gap/transition marker, and automatically starts the next
window on the fallback. The product does not reconnect or select the fallback
itself.

## 6. Normalized event contract

The existing realtime contract should be extended additively. Every event
contains `session_id`, `window_id`, `lane_id`, `sequence`, and `provider_ref`.
`provider_ref` is an abstract configured reference and may include a safe
provider/model label, but never credentials.

The contract must support:

- `window.open`, `window.closed`, and `window.failed`;
- `input_transcript.delta` and `input_transcript.completed`;
- `output_transcript.delta` and `output_transcript.completed`;
- `output_audio.delta` and `output_audio.completed`;
- `provider.switched` with `from_provider`, `to_provider`, and a safe reason;
- `usage` containing counts/duration only; and
- `error` containing a stable retryability category, not provider payloads.

Example transition event:

```json
{
  "type": "provider.switched",
  "session_id": "…",
  "window_id": "w0003",
  "sequence": 417,
  "from_provider": "gemini_live_translate",
  "to_provider": "openai_realtime_translate",
  "reason": "provider_unavailable",
  "boundary_timestamp_ms": 180000
}
```

Raw provider event names and payloads must not leak through this contract.

## 7. Routing and failover rules

- A failover switch requires the secondary to pass readiness and capability
  checks for the requested language pair, audio format, and output modalities.
- Use a circuit breaker per provider/profile: closed, open after repeated
  failures, and half-open after a cooldown probe.
- Do not switch solely because one translation is linguistically different;
  quality remains a product evaluation decision.
- Do not switch repeatedly within one window. Apply a cooldown and cap the
  number of switches per session.
- If neither provider is available, close the window with a retryable,
  content-free error and preserve already-delivered events.

For a failure detected after a window has opened, the Gateway may stop the
current provider session immediately, mark that window partial, and
automatically start fallback audio as the next window. It must report the
interruption explicitly. Accepted audio is never silently replayed, and the
Gateway must not imply uninterrupted sentence continuity.

## 8. Provider adapters

Adapters translate the normalized contract to each provider protocol:

- Gemini Live Translate: source audio in; input transcript, translated text,
  and translated audio out.
- OpenAI Realtime Translate: dedicated translation session; continuous source
  audio in; input/output transcript deltas and translated audio out.

Provider adapters must remain stateless outside the active process session.
They may retain only the transient bytes required to forward audio and parse
events. Provider-specific retries, close/flush behavior, and sample-rate
conversion stay inside the adapter.

## 9. Product integration

Interpreter should request a Gateway policy and render normalized lanes. It
should not know provider URLs, API keys, WebSocket event names, or circuit
breaker state. Suggested controls are:

- Provider: configured primary/fallback route (provider names remain server-side).
- Policy: Single provider or Windowed failover.
- Window length: operator/product-approved values only.
- Audio: original, Gemini target, OpenAI target, or muted.
- Text: source, target, both, and provider labels.

For durable evaluation, Interpreter may send content-free result identifiers
and user ratings to its own product service. Gateway must not store the audio,
transcripts, translations, prompts, or ratings.

## 10. Security and privacy

- Only authenticated Dali product services may invoke these routes.
- Provider credentials remain environment-managed secrets in Gateway.
- Never log audio, transcript text, translation text, prompts, bearer tokens,
  user identifiers, or provider request bodies.
- Comparison mode requires an explicit product entitlement because it doubles
  provider processing and may expose two outputs to the user.
- Usage records contain only provider/profile, duration, token/audio units,
  outcome, and failover counts.
- Window buffers are bounded in memory and discarded when forwarded, closed,
  or failed.

## 11. Observability

Track content-free metrics by product, profile, and provider:

- window opens, closes, failures, and switch count;
- first-output and end-of-window latency;
- connection duration and bytes/duration processed;
- provider error category and circuit state;
- fallback success rate.

Do not use transcript similarity or user content in Gateway telemetry. Product
services may perform quality evaluation under their own retention and consent
rules.

## 12. Rollout and testing

1. Add the normalized window/event schema and contract fixtures.
2. Implement adapter-neutral window orchestration with a fake provider.
3. Add Gemini and OpenAI adapter conformance tests using recorded synthetic
   audio fixtures that contain no personal data.
4. Test provider timeout, disconnect, malformed event, circuit-open, and
   capability-mismatch cases.
5. Enable Windowed failover for Interpreter with a conservative 90-second
   window and explicit operator metrics.
7. Retain the existing single-provider Classroom behavior unchanged.

Required Gateway quality gates remain:

```powershell
python -m compileall -q app tests
python -m pytest -q
python -m scripts.export_openapi --check
```

## 13. Decisions and remaining configuration

Decisions fixed for implementation:

- Initial Interpreter window: 90 seconds.
- Allowed window values: 60, 90, or 120 seconds only.
- Failover is automatic and Gateway-owned; client reconnect is not required.
- No replay of accepted audio; a failed window is marked partial and the next
  window starts on the fallback.

Configuration still required per workload/region:

- Primary and fallback provider for each approved language pair.
- Maximum switches and maximum session duration.
- Capability/quality evidence for each enabled fallback pair.

These decisions should be recorded in the Gateway implementation plan and,
where they change an existing locked boundary, in an ADR before production
rollout.
