# Dali AI Gateway Multi-Product Design

Status: Proposed architecture design  
Audience: AI Gateway, product-service, Platform, security, and operations owners  
Scope: Evolve the restricted Classroom Gateway into a private, product-neutral
AI data plane that can support Interpreter and other Dali products without
absorbing their product logic or durable content.

## 1. Purpose

The current Gateway proves the core service boundary: authenticated Dali
services can request text generation, batch transcription, realtime
transcription, and realtime translation through provider-neutral profiles.

This design defines the additional contracts and controls required for multiple
products. It deliberately does not make the Gateway a workflow engine. Product
services continue to decide what the AI should do, why it should do it, and how
the result affects a user or product account.

This document does not authorize:

- an Interpreter/Host runtime change or migration;
- production DNS, credentials, databases, or provider purchases;
- a provider/model selection for any product;
- general-user traffic; or
- removal of an existing direct-provider rollback.

Interpreter migration requires a separate product integration design and
explicit approval under the existing Host protection boundary.

## 2. Architectural Principle

The boundary is:

```text
Product service
|- owns the user and product-account decision
|- builds prompts/instructions and terminology
|- orchestrates workflow, lanes, retries, and durable results
|- calls an abstract Gateway capability/profile
|
`-> Dali AI Gateway
    |- authenticates the product workload
    |- authorizes product/profile/capability use
    |- admits work within capacity policy
    |- translates the generic contract to a provider protocol
    |- normalizes provider output, errors, and usage
    `- discards all request/response content after delivery
```

The Gateway may receive a system instruction, translation instruction,
terminology prompt, or text/audio input because a provider needs it. It treats
that information as transient request content. It must not store, version,
select, enrich, reuse, analyze, or log it.

## 3. Current Baseline

The implemented v1 baseline includes:

- authenticated `POST /ai/v1/text/generations`;
- authenticated `POST /ai/v1/audio/transcriptions`;
- authenticated realtime transcription and translation WebSockets;
- OpenAI, Gemini, and Ollama adapters;
- server-configured provider/model profiles;
- caller-to-product authorization;
- process-local per-caller/capability admission;
- bounded batch and realtime audio input;
- normalized safe errors;
- OpenAPI 3.1 and JSON Schema 2020-12 contracts; and
- privacy, provider-protocol, contract, admission, and API tests.

The baseline is suitable for the restricted Classroom pilot. It is not yet a
shared-production service because it lacks explicit caller-to-profile grants,
reliable readiness, multi-instance admission, provider failover/circuit state,
translated-audio/TTS contracts, finalized realtime usage, and shared operations
controls.

## 4. Goals

1. Support independently authenticated Dali product services without allowing
   one caller to use another product's profiles or reserve.
2. Provide generic text, transcription, translation, translated-audio, and
   speech-synthesis transport capabilities required by approved products.
3. Keep provider/model selection server-controlled and product-visible only
   through stable abstract profiles.
4. Keep prompts, instructions, terminology, workflow, product state, and all
   durable AI content product-owned.
5. Normalize realtime sequencing, rollover, retryable errors, and content-free
   usage without exposing raw provider protocols.
6. Enforce capacity across multiple Gateway instances, including independently
   configurable product/caller pools and a documented Host reserve.
7. Support controlled provider fallback without duplicating user-visible work
   or hiding unsafe semantic changes.
8. Integrate with Platform workload identity and content-free usage contracts
   without putting AI content in Platform.
9. Preserve the released Classroom v1 behavior during additive evolution.

## 5. Non-Goals

The Gateway does not own or persist:

- system prompts, developer prompts, translation instructions, or prompt
  templates;
- terminology dictionaries, context notes, names, or subject vocabulary;
- transcript, translation, summary, study, meeting, finance, or Bible content;
- product workflow state or user-visible retry state;
- product accounts, roles, plans, tiers, entitlements, or feature decisions;
- class/session/course/organization lifecycle;
- target-language selection or multi-language orchestration;
- conversation history or retrieval/vector data;
- output approval, moderation decisions owned by a product, or durable audit of
  user content; or
- mobile/browser authentication.

Mobile and browser clients never call the Gateway directly. A product service
must authorize its user and product operation before invoking Gateway.

The Gateway does not make Host depend on it. Capacity reservation and contract
support may be implemented before any separately approved Host migration.

Agent runtimes may use Gateway as their inference backend, but MCP discovery,
credentials, tool execution, approval, agent loops, prompts, and conversation
state remain outside Gateway. That boundary is defined in
[`agent_runtime_mcp_boundary_design.md`](agent_runtime_mcp_boundary_design.md).

## 6. Ownership Boundary

| Concern | Product service | AI Gateway |
|---|---|---|
| User access | Authenticate user; verify product account, plan/tier, role, consent, and entitlement | Authenticate only the calling workload and authorize its registered product/profile grants |
| Prompting | Build and version all prompts, instructions, context, terminology, and output schemas | Validate size/type, forward transiently, discard after delivery |
| Workflow | Decide when to transcribe, translate, summarize, synthesize, retry, revise, or persist | Execute one declared capability request/session |
| Provider policy | Approve acceptable profile behavior and product rollout | Map profile to provider/model route, transport options, fallback policy, and kill switch |
| Realtime lanes | Select target languages and orchestrate source/translation/TTS lanes | Operate one generic declared lane per Gateway session |
| Content | Own all durable input and output | Hold only bounded transient in-flight content |
| Errors | Decide user-visible recovery and durable retry | Normalize provider/transport error category and retryability |
| Usage | Durably associate measurement with the product operation and relay when required | Calculate provider-derived, content-free measurement and stable event identity |
| Capacity | Select product priority within approved policy | Enforce caller/product/capability/provider pools and reserves |
| Observability | Product outcome and QoS without sensitive content | Transport/provider latency, counts, capacity, error class, and delivery state without content or user IDs |

## 7. Content and Prompt Handling

### 7.1 Transient request content

The following fields are allowed only when required by the declared capability:

- `system_instruction` or generic `instructions`;
- input text;
- response schema/format hints;
- source and target language;
- terminology prompt or keyword list;
- audio bytes/chunks and declared media format; and
- provider-neutral voice/profile selection.

These fields exist only in request objects, bounded process memory, and the
encrypted provider connection. They are removed when the request finishes,
fails, times out, is cleared, or disconnects.

They must never enter:

- profile configuration;
- database rows or durable queues;
- normal logs, traces, metrics, error details, or audit events;
- exception serialization;
- Platform usage events; or
- subsequent requests unless the product service sends them again.

### 7.2 Product-owned prompt versioning

If a product needs prompt IDs, revisions, experiments, localization, or rollback,
the product service owns those records. It sends the resolved prompt text
transiently to Gateway. Gateway does not accept a product prompt ID and fetch
the corresponding prompt from storage.

Gateway may accept a content-free `policy_revision` or experiment label only if
needed for safe measurement and allowlisted by contract. It must not be usable
to reconstruct the prompt or user content.

### 7.3 Content-safe diagnostics

Diagnostics may contain only allowlisted values such as:

- caller/workload ID;
- product ID;
- abstract profile and capability;
- request/session event ID;
- provider route ID where operationally required;
- byte/token/duration counts;
- timing buckets;
- normalized error code; and
- admission/fallback/usage-delivery outcome.

They exclude Dali account IDs and all request/response content. Request IDs must
be random operational identifiers, not encoded user or product-content IDs.

## 8. Workload and Authorization Model

### 8.1 Registered workload

Every product server has one or more independently revocable workload
identities. Separate workloads are used when permissions differ, for example:

- realtime media transport;
- batch text/transcription;
- evaluation; and
- internal billing/operations jobs.

A workload record defines:

- stable `workload_id`;
- owning product(s);
- allowed capabilities and exact profiles;
- allowed environments;
- concurrency/cost class;
- credential/token status and rotation metadata; and
- rollout/kill-switch state.

The initial static per-caller bearer map may remain only as a compatibility
adapter. The target is a Platform-issued, short-lived workload token or another
approved independently rotatable workload credential. The authenticated token,
not an untrusted caller header/body, determines `workload_id`.

When Platform JWTs are selected, Gateway validates a dedicated Gateway audience,
workload principal type, required scopes, issuer, algorithm, time claims, and
key ID. Verification uses a bounded JWKS cache with periodic refresh,
unknown-`kid` refresh, current/previous key overlap, clock-skew policy, and a
documented last-known-good-key behavior during a Platform/JWKS outage. An outage
must not cause Gateway to accept an unknown key or extend a token lifetime.
Last-known-good verification has a configured maximum cache staleness; after it
expires, new workload authentication fails closed until trusted keys refresh.

### 8.2 Exact profile grants

Authorization is the intersection of:

```text
authenticated workload
AND registered product
AND exact profile grant
AND required capability
AND environment
AND rollout/kill-switch state
```

Prefix-based authorization such as allowing every `shared.*` profile is
prohibited. Evaluation profiles are granted only to evaluator workloads.

### 8.3 Product authorization

Gateway authorization proves only that the Dali service may call an AI
capability for its registered product. It does not prove that a user has access,
has paid, consented, or may store the result. Those checks occur in the product
service before the request.

No user token or Dali account ID is sent to Gateway.

## 9. Model Profile Design

A stable abstract profile may contain only transport and operational policy:

- product or explicitly shared technical namespace;
- capability;
- ordered provider/model route candidates;
- allowed input/output modalities and media formats;
- timeout, maximum size/duration, and provider-session lifetime;
- temperature/format ceilings where applicable;
- capacity and cost class;
- retry/failover policy;
- content-free measurement mapping; and
- active, shadow, canary, and kill-switch state.

A profile must not contain product prompts, terminology, target-language policy,
workflow steps, plan/tier logic, or durable content.

Profile and grant configuration uses strict typed schemas with unknown fields
forbidden. The schema explicitly rejects prompt/instruction/terminology,
workflow, plan/tier, product-content, and user/account fields rather than merely
ignoring them.

Configuration is loaded as one immutable, content-free generation with a stable
generation ID. Gateway validates the complete generation before atomically
activating it. An invalid or partial generation never changes live policy;
Gateway retains the last-known-good generation and reports a safe readiness/
alert state. Rollback activates a previously validated generation rather than
editing individual live records in place.

Products request profiles such as:

```text
classroom.transcription.live
classroom.translation.economy
interprete.transcription.fast
interprete.translation.audio.fast
interprete.speech.standard
```

Names are stable contract identifiers. Their provider/model mapping may change
through a separately approved product rollout without a client release.

## 10. Capability Contracts

All new contracts are additive. Released v1 Classroom requests and events remain
supported until a separately approved version retirement.

### 10.1 Text generation

Generic input:

- request ID, product, and profile;
- transient system instruction and input;
- provider-neutral output format/schema constraints; and
- bounded generation controls allowed by the profile.

Generic output:

- request ID;
- generated text or validated JSON text;
- abstract profile;
- normalized usage; and
- content-free route/fallback metadata where operationally approved.

Gateway does not know whether the operation is a summary, translation, study
guide, finance explanation, or another product workflow.

### 10.2 Batch audio transcription

Generic input includes bounded audio, declared format/sample rate/channels,
source language, and transient terminology instruction. Generic output includes
text, detected language when available, and normalized usage.

Provider-native timestamps, confidence, or speaker labels are added only through
provider-neutral optional structures with explicit cross-provider semantics.

### 10.3 Realtime transcription

One WebSocket represents one source transcription lane. The product service
owns session lifecycle beyond that lane, including reconnect-visible behavior,
durable transcript state, and any fan-out to translation lanes.

### 10.4 Realtime translation

One WebSocket represents one source-audio-to-one-target-language translation
lane. A product such as Interpreter opens independent sessions for multiple
target languages and owns their coordination and partial-failure UX.

The profile declares which lane outputs are supported. The caller chooses a
non-empty subset of those outputs for each session:

- source-language transcript;
- translated text;
- translated audio.

Source-language transcript events use `transcript.delta/final`; translated text
uses `translation.delta/final`; translated speech uses
`translation.audio.delta/final`. The Gateway must not discard a provider's
source transcript when the caller requested it. It also must not silently
downgrade an unsupported output selection; the session fails before provider
content is accepted.

Text-only callers cannot request an audio-producing profile without an explicit
grant.

### 10.5 Speech synthesis

Add a provider-neutral speech operation for products requiring audio output from
text. Input contains transient text, target language, and an abstract voice
profile. Output is bounded audio plus media metadata and normalized usage.

Provider voice/model IDs are not caller-selectable. Product-specific voice
cloning enrollment, consent, reference recordings, and durable voice assets are
outside Gateway unless a later dedicated privacy design explicitly authorizes
them.

### 10.6 Realtime translated audio

The normalized server protocol adds audio events with:

- event type (`translation.audio.delta` or `translation.audio.final`);
- stable response/item ID;
- monotonically increasing sequence number;
- target language;
- base64 audio chunk;
- provider-neutral codec, sample rate, channel count, and sample format; and
- final/incomplete disposition.

The Gateway forwards audio transiently and does not assemble or store complete
translated audio. The product service decides whether and how to buffer,
play, persist, or discard it.

## 11. Realtime Protocol

### 11.1 Client events

Required normalized client events are:

- `session.start`;
- `audio.append` with a monotonically increasing input sequence number;
- `audio.commit`;
- `audio.clear`;
- `session.stop`; and
- `session.rotate` or reconnect using a rotation token when the negotiated
  contract supports it.

Every session start declares one product, profile, capability, request ID,
audio format, and product-resolved transient instructions.

`session.ready` negotiates maximum chunk bytes, maximum unacknowledged chunks/
bytes, and the initial input sequence. Gateway emits `audio.accepted` containing
the highest contiguous input sequence successfully handed to the active provider
adapter/transport. This watermark means transport acceptance, not that the
provider produced or durably retained a result.

The product service stops sending when the negotiated unacknowledged window is
full. A protocol violation or sustained inability to accept input closes the
session with a normalized retryable backpressure disposition. Gateway never
uses an acknowledgment to claim semantic provider completion.

### 11.2 Server events

Required normalized server events are:

- `session.ready` with negotiated modalities and limits;
- transcript delta/final;
- translation text delta/final;
- translation audio delta/final where enabled;
- `usage.update` and `usage.final` with content-free measurements;
- `session.rotation_required` with a bounded deadline and last accepted input
  sequence;
- `session.closed` with normalized disposition, last accepted input sequence,
  and final output sequence; and
- normalized error with retryability and failure stage.

Text/audio events include stable item/response IDs and sequence numbers where
needed for duplicate suppression. Gateway never forwards raw provider events.

Every session has bounded outbound event/byte queues and a send timeout. Gateway
may apply contract-defined delta coalescing only when it cannot alter final text
or audio; it never silently drops a final event or translated-audio chunk. When
a caller is too slow and the provider cannot be safely paused, Gateway closes
with a normalized `slow_consumer` disposition and reports the last emitted and
accepted sequence watermarks. All queued content is then discarded.

### 11.3 Rollover and recovery

Planned provider-session expiry is not reported as a generic outage. Gateway
emits `session.rotation_required` early enough for the product service to open a
replacement lane. Its last-accepted input watermark defines the possible replay
boundary, but provider processing may still be ambiguous. The product service
owns the bounded replay-tail policy and durable duplicate suppression; Gateway
supplies stable input/output sequencing and clear session boundaries.

Gateway does not silently restart a realtime lane after content may have been
accepted. That could duplicate or reorder transcript/audio. Reconnect requires
an explicit product-service decision.

## 12. Routing, Failure, and Fallback

### 12.1 Route candidates

A profile can define an ordered list of compatible provider routes. Route
compatibility requires equivalent product-approved capability, modality,
language, privacy, and output-contract behavior.

Fallback never crosses from text-only to audio-producing behavior, changes a
target language, weakens privacy terms, or selects an unapproved preview model.

### 12.2 Safe fallback points

- **Before provider acceptance:** Gateway may choose the next healthy compatible
  route automatically.
- **Batch request after an unambiguous no-result failure:** Gateway may retry only
  under the profile's bounded retry policy.
- **After an ambiguous provider result or charge:** Gateway returns a normalized
  ambiguous outcome; it does not automatically duplicate the request.
- **Realtime after audio acceptance:** Gateway does not silently fail over. It
  tells the product to rotate/reconnect.

Stable request IDs support correlation and usage deduplication, but Gateway does
not persist generated content to provide response replay.

### 12.3 Provider health and circuit state

Gateway maintains shared, content-free route health/circuit state across
instances:

- configured/credential-ready;
- healthy, degraded, open, or disabled;
- bounded failure and latency counters;
- retry-after/open-until time; and
- operator/product kill-switch state.

Circuit state affects new admission only and does not expose provider errors or
content to callers.

## 13. Admission and Capacity

Admission is enforced before provider work using shared leases/counters rather
than process-local dictionaries.

Policy dimensions may include:

- workload and product;
- capability/profile;
- provider route;
- realtime versus batch;
- concurrency;
- audio duration/bytes or token estimate;
- priority class; and
- configured reserve.

Host and Classroom limits are independently configurable. New products cannot
consume a documented Host reserve even when Host is not yet a Gateway caller.
Unused reserve borrowing, if allowed, must be explicit, bounded, immediately
reclaimable, and tested; it is disabled by default.

Admission state has a documented failure mode. Silently reverting to
per-process limits is prohibited. A dependency failure either fails closed for
new costly work or applies a separately approved conservative local ceiling.

## 14. Usage Measurement

Gateway produces content-free measurements; product services associate those
measurements with their own durable operation/account records.

A normalized measurement contains only:

- stable event/request/session ID;
- workload, product, capability, and profile;
- route/provider/model identifiers where approved for accounting;
- input/output token counts;
- accepted source-audio and generated-audio duration/bytes;
- request/session timestamps and disposition;
- fallback/rotation count; and
- measurement schema/version.

It contains no Dali account ID, prompt, transcript, translation, audio, target
content, or product object name.

For batch operations, usage is returned in the response. For realtime, Gateway
emits updates and one final measurement. Platform ingestion is idempotent by
event ID.

Measurement semantics distinguish:

- bytes/duration received by Gateway;
- bytes/duration accepted by the provider adapter/transport;
- provider-reported input/output tokens and audio duration;
- Gateway estimates, including the versioned estimation method;
- generated/forwarded output audio;
- partial versus complete disposition; and
- whether provider acceptance/charging is ambiguous.

Billing/quota policy must name the exact authoritative field. Gateway never
substitutes received audio for provider-accepted audio or an estimate for an
exact provider report without marking that distinction. Disconnect, timeout,
cancellation, rotation, partial output, and ambiguous provider outcomes all
produce a final content-free measurement when any provider work may have
occurred.

Because Gateway remains content-stateless, reliable delivery uses these
patterns:

1. Realtime/provider work whose measurement must survive caller disconnect is
   published to an external durable content-free usage sink with confirmation.
2. Batch responses may return the exact measurement to the trusted product
   service for durable relay to Platform under its workload identity.
3. A product may also relay a received realtime final measurement as a duplicate
   recovery path; Platform deduplicates by event ID. It is not the only durable
   path because the caller may disconnect before receiving it.

The selected pattern and failure behavior must be fixed in the Platform-Gateway
usage contract before billing or quota authority depends on it. Gateway must not
silently drop a measurement after provider work. A realtime profile without an
approved durable sink remains non-authoritative for billing/quota and cannot
pass shared-production readiness.

## 15. Readiness and Health

Liveness reports only that the process event loop is serving.

Readiness requires:

- valid workload-authentication configuration;
- valid product/profile/capability grants;
- every enabled profile having a valid route configuration and every profile
  marked required for that deployment resolving to at least one credential-ready
  route;
- required shared admission/circuit state available or in its approved degraded
  mode;
- required usage sink available when its policy is fail-closed; and
- configuration generation fully loaded.

The mere construction of an Ollama/provider adapter does not make a route ready.
Periodic route probes are content-free and must not send product prompts or
audio. Probes run outside the readiness request path, use bounded timeouts and
rate, and publish cached state with a maximum staleness. Readiness never incurs
provider work synchronously. Provider outage may degrade selected profiles
without making unrelated profiles unavailable; only profiles configured as
required for that deployment affect its ready/not-ready result. Readiness
exposes only safe aggregate state.

## 16. Privacy and Security Invariants

1. Only authenticated Dali workloads call Gateway.
2. Exact product/profile/capability grants are deny-by-default.
3. No Dali account ID or user token crosses the Gateway boundary.
4. Product content exists only in bounded memory and encrypted transport.
5. Gateway never writes content to a database, file, durable queue, log, trace,
   metric, audit event, or error.
6. Provider errors are normalized and never echoed raw.
7. Provider credentials remain external secrets and are independently rotatable.
8. Request validation errors contain no submitted values.
9. Realtime buffers are bounded and cleared on commit, clear, timeout, error,
   disconnect, and shutdown.
10. Workload, profile, capacity, and kill-switch changes are content-free and
    auditable.
11. A provider/model route is enabled only after its privacy, data-use,
    retention, region, and training terms are approved for that product.
12. Evaluation profiles and production profiles use separate workload grants.

## 17. Observability and Operations

Required content-free metrics include:

- request/session admission, active count, rejection, and lease age;
- provider route connection/success/failure/timeout/circuit state;
- first-event, final-event, and total latency histograms;
- input/output token and audio-duration totals;
- reconnect, planned rotation, fallback, and ambiguous-outcome counts;
- WebSocket close disposition and abnormal duration;
- usage publication/relay outcome and lag; and
- readiness/config generation/key age.

Operations require:

- at least two Gateway instances for shared-production traffic;
- tested shared admission and circuit state;
- per-product/profile rollout and kill switches;
- key/credential rotation and rollback procedures;
- provider outage and capacity-exhaustion drills;
- load tests for batch and realtime concurrency;
- multi-hour realtime soak tests including repeated provider rollovers;
- redacted dashboards and alerts;
- deployment and rollback runbooks; and
- named service, provider, security, incident, and capacity owners.

During planned shutdown, Gateway stops new admission and sends active realtime
callers `session.rotation_required` with a drain deadline and last accepted
input sequence. It then emits `session.closed` where possible, closes provider
sessions, clears buffers, and releases shared leases. Forced termination remains
bounded and is exercised in rollout tests.

## 18. Interpreter Compatibility Boundary

Gateway can support a future Interpreter migration only after it provides:

- Interpreter-specific abstract profiles and exact workload grants;
- batch transcription and text-generation compatibility;
- realtime source transcription;
- one-target-per-lane translated text and translated audio;
- provider-neutral speech synthesis where Interpreter requires it;
- sequence IDs, planned rotation, reconnect boundaries, and finalized usage;
- capacity policy that preserves the approved Host reserve; and
- tested direct-provider rollback.

Interpreter continues to own:

- session routing, organization/listener/host behavior, and authentication;
- source and target language selection;
- prompt construction and context/terminology policy;
- multi-target lane orchestration and partial-failure behavior;
- transcript refinement, translation revision, summary workflow, and TTS choice;
- durable transcript/translation/audio records;
- user-visible QoS, reconnect, and retry behavior;
- plan/tier entitlement and usage association; and
- all Host compatibility behavior.

No Gateway feature in this section changes Host. A separate Interpreter Gateway
migration design must map the existing runtime events and rollout/rollback gates
before any implementation in `app_server`.

Interpreter cannot be selected for canary until the existing Classroom Gateway
gates relevant to shared operation are closed or explicitly waived: provider
quality, privacy/data-use terms, latency, one-hour/multi-rollover reliability,
capacity reserve, provider-outage rollback, and content-free usage parity.

## 19. Other Product Onboarding

Every additional product supplies a reviewed manifest containing:

- product and workload owner;
- product ID, workload IDs, capabilities, and exact profile grants;
- transient input/output content classes;
- prompt/instruction owner;
- approved providers/routes and privacy terms;
- expected concurrency, duration, token/audio volume, and priority;
- capacity pool/reserve relationship;
- normalized usage schema and durable relay owner;
- product-side authorization, retention, and deletion behavior;
- canary cohort, success metrics, rollback triggers, and rollback owner; and
- proof that mobile/browser clients cannot reach Gateway directly.

A new profile or adapter does not enroll a product. Configuration, credentials,
capacity, usage, privacy, and rollout must all pass independently.

## 20. Contract and Implementation Roadmap

The actionable work breakdown, dependencies, verification, rollout, rollback,
and status tracking are maintained in
[`multi_product_gateway_implementation_plan.md`](multi_product_gateway_implementation_plan.md).

The recommended order is:

1. Fix exact caller/profile authorization and truthful readiness.
2. Define Platform-compatible workload identity and usage contracts.
3. Add shared admission, provider circuit state, reserves, and kill switches.
4. Extend realtime contracts with sequence, rotation, translated-audio, close,
   and usage events while preserving v1 Classroom behavior.
5. Add provider-neutral speech synthesis if required by the first approved
   non-Classroom product.
6. Add bounded compatible route fallback and ambiguous-outcome handling.
7. Complete multi-instance, outage, load, privacy, and multi-hour soak gates.
8. Write and approve the separate Interpreter migration design.
9. Canary one capability/lane with direct-provider rollback before expanding.

## 21. Shared-Production Readiness

Gateway is ready for an additional production product only when:

- exact workload/product/profile grants replace prefix authorization;
- readiness proves all enabled profile dependencies;
- workload credentials are independently rotatable;
- admission and reserve enforcement work across instances;
- normalized usage is complete, idempotent, and durably deliverable;
- provider failure, circuit, fallback, and realtime rotation behavior are
  contractually defined and tested;
- content/privacy inspection finds no persistence or diagnostics leakage;
- the product contract and adapter pass provider-neutral conformance tests;
- load, outage, and soak gates pass at the approved capacity;
- canary and direct-provider rollback have named owners and have been exercised;
  and
- Classroom remains compatible and Host remains unchanged unless separately
  authorized.

Passing these gates for one product does not automatically approve another
product, capability, provider, or model.

## 22. Decisions Required Before Implementation

1. Platform workload-token profile versus another workload credential mechanism.
2. Exact contract version boundary for new realtime/audio/usage events.
3. Shared admission/circuit-state technology and degraded-mode policy.
4. Host reserve size, borrowing policy, and capacity owner.
5. Direct usage sink versus product-service durable relay.
6. Allowed translated-audio formats and maximum chunk/session sizes.
7. Whether speech synthesis is required for the first non-Classroom pilot.
8. Route compatibility and automatic fallback criteria per product profile.
9. First non-Classroom product/capability pilot and rollback owner.
10. SLO, load, outage, and soak thresholds for shared-production approval.

No unresolved decision should be replaced by an implicit production default.
