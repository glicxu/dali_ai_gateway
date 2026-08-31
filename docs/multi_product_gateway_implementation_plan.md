# Dali AI Gateway Multi-Product Implementation Plan

Status: G1 local foundation complete; G2 consumer-ready boundary implemented  
Design authority:
[`multi_product_gateway_design.md`](multi_product_gateway_design.md)  
Related agent boundary:
[`agent_runtime_mcp_boundary_design.md`](agent_runtime_mcp_boundary_design.md)  
Scope: Evolve the restricted Classroom Gateway into a private, product-neutral,
shared AI data plane while preserving Classroom v1 and keeping product prompts,
workflow, authorization, accounts, plans/tiers, and durable content outside the
Gateway.

## 1. How to Use This Plan

This document tracks executable work. The multi-product design remains
authoritative for architecture, ownership, privacy, and service boundaries. If
an implementation task conflicts with that design, stop and resolve the design
or record an approved ADR before changing behavior.

Checkboxes describe implementation status:

- `[ ]` not started;
- `[~]` in progress; and
- `[x]` completed and verified.

Code alone does not complete a task. Focused tests, contract checks, privacy
inspection, compatibility, rollout defaults, and the slice exit gate must pass.

## 2. Protected Baseline

The current restricted Classroom release is the non-regression baseline:

- authenticated text generation and batch transcription;
- authenticated realtime transcription and realtime translation;
- OpenAI, Gemini, and Ollama provider adapters;
- server-owned abstract profile mappings;
- caller-to-product authorization;
- process-local caller/capability admission;
- privacy-safe validation and provider errors;
- OpenAPI 3.1 and JSON Schema 2020-12 contracts; and
- a private production systemd unit.

At plan creation, the repository quality gate passes with 23 tests. That test
count is historical context, not a fixed acceptance threshold.

Every slice must preserve:

- existing Classroom v1 request/response/event behavior;
- mobile/browser prohibition from calling Gateway directly;
- transient-only prompt, text, transcript, translation, and audio handling;
- provider credentials outside Git;
- no `app_server`, `dali_ai`, Classroom, Host, or Platform runtime imports;
- product ownership of prompts, workflows, durable content, user authorization,
  plans/tiers, retries, and user-visible errors; and
- Host remaining unchanged unless a separate ADR and explicit approval authorize
  a migration.

## 3. Authorization Boundary

This plan authorizes local implementation and test work only after the relevant
slice is selected. It does not by itself authorize:

- production infrastructure, DNS, credentials, signing keys, databases, or
  shared-state provisioning;
- provider/model purchases or product mapping changes;
- a Host/Interpreter runtime modification;
- a general-user rollout;
- removal of a direct-provider rollback; or
- billing/quota authority based on a new usage path.

Any task requiring one of those actions stops at a disabled, deployable boundary
until separately approved.

## 4. Dependency Map

```text
G0 Decisions, contracts, and inventory
 `- G1 Exact authorization and truthful readiness
     |- G2 Workload identity and usage
     |- G3 Shared admission, reserves, and circuit state
     |- G4 Realtime v2, translated audio, and conditional TTS
     |    `- G5 Routing, failure, and fallback
     `-------------------------------+
                                     `- G6 Resilience and operations
                                          `- G7 First non-Classroom canary

Optional after G1-G6:
A1 Agent inference contract and pilot (separate agent design)
```

G2, G3, and the contract portion of G4 may overlap after G1 identifiers and
policy interfaces stabilize. G5 depends on the normalized G4 terminal/recovery
semantics. G6 gates every production canary. G7 is repeated separately for each
new product/capability.

### 4.1 Cross-repository dependencies

| Gateway work | External dependency | Contract/ownership rule |
|---|---|---|
| G2 Platform workload JWT verification | Dali Platform MS2 account/workload token profile and MS3 workload identity | Platform owns issuer/JWKS and workload token claims; Gateway owns its audience/scope enforcement and exact profile grants |
| G2 usage ingestion | Dali Platform MS3 generic usage envelope/producer authorization | Gateway owns provider-derived measurement semantics; Platform owns accepted idempotent usage; the approved durable sink/relay owns delivery retry |
| G4/G5 product adapter | Product-service contract and provider-neutral Gateway contract | Product service owns prompts, workflow, durable content, user authorization, plan/tier, retries, and final enforcement |
| G7 client/product release | Product repository release and rollback | Gateway configuration alone never enrolls a product or changes a client |
| Interpreter consideration | Approved Interpreter migration design and current Host ADR gates | No `app_server` edit or Host dependency is authorized by this plan |

Gateway may complete interfaces, fake-provider tests, and the legacy credential
adapter before Platform MS2/MS3. It must not mark the target G2 identity/usage
path complete or disable legacy authentication until the Platform contracts and
conformance fixtures pass in both repositories.

## 5. Delivery Status

| Slice | Status | Depends on | Production effect |
|---|---|---|---|
| G0 | Not started | None | None |
| G1 | Local foundation complete | G0 identifiers/contracts | Classroom-compatible policy/readiness hardening |
| G2 | In progress | G1 | New workload/usage paths disabled initially |
| G3 | In progress | G1 | Shared capacity policy disabled until state is approved |
| G4 | Not started | G1 | New contract version/profile types disabled initially |
| G5 | Not started | G3, G4 | Fallback disabled by default |
| G6 | Not started | G2-G5 | Operational readiness only |
| G7 | Not started | G6 | Explicitly approved canary only |
| A1 | Deferred/optional | G1-G6 | Separate agent-runtime pilot approval |

G1 runtime work is complete except for connecting the generation loader to the
authoritative configuration source. That source remains an explicit G0
infrastructure decision. Until it is approved, startup environment
configuration remains the compatibility source; no polling/watcher or
production distribution mechanism is implied.

G2 includes an injected workload authenticator, disabled-by-default Platform
RS256/JWKS verification, a caller-specific legacy adapter, a strict internal
measurement model/JSON Schema, and an idempotent delivery interface. Production
activation, a concrete durable sink, Platform ingestion conformance, and
billing/quota authority remain blocked on the Platform MS2/MS3 contracts and
the G0 issuer/audience/scope/sink decisions.

The local measurement boundary now includes a lifecycle accumulator that emits
one canonical terminal event for complete, partial, disconnected, cancelled,
timed-out, provider-failed, and ambiguous outcomes. It is an interface/test
slice only; request handlers do not treat it as billing or quota authority
until Platform ingestion and durable-delivery ownership are approved.

## 6. Cross-Cutting Engineering Rules

Every behavior change must:

- use immutable typed models at provider, policy, and service boundaries;
- deny unknown caller/product/profile/capability combinations;
- keep product/profile rollout disabled unless explicitly enabled;
- bound text, schema, audio, streaming buffers, duration, concurrency, and
  timeout;
- clear transient buffers on completion, clear, cancellation, timeout, error,
  disconnect, and shutdown;
- normalize errors without raw provider payloads or submitted values;
- avoid logging bearer tokens, keys, prompts, messages, audio, transcripts,
  translations, summaries, tool schemas/results, user IDs, or product object
  names;
- support deterministic tests with fake providers and clocks/state adapters;
- update source contracts and examples with implementation; and
- run the full repository quality gate before slice handoff.

Provider/model support does not select that route for a product. Product mapping,
privacy approval, credential activation, and rollout remain independent gates.

## 7. G0 - Decisions, Contracts, and Consumer Inventory

### Objective

Resolve decisions that affect schema or infrastructure, identify the first
non-Classroom consumer, and freeze the shared identifiers before implementation.

### Required decisions

- [ ] Choose the Platform workload-token profile or another workload credential
  mechanism and rotation overlap.
- [ ] Choose the additive v1 versus new version boundary for realtime audio,
  usage, rotation, and terminal events.
- [ ] Select shared admission/circuit-state technology and degraded behavior.
- [ ] Approve Host reserve size, borrowing policy, and capacity owner.
- [ ] Select the external durable content-free sink required for realtime usage
  across caller disconnects, plus the allowed batch/product-relay behavior.
- [ ] Approve translated-audio codec/sample-rate/channel formats and limits.
- [x] Approve batch speech synthesis for the Dali Chat capability-demo pilot;
  realtime translated audio remains a separate decision.
- [ ] Define route compatibility and automatic fallback rules.
- [ ] Select the first non-Classroom product/capability and rollback owner.
- [ ] Set shared-production SLO, load, outage, and soak thresholds.
- [ ] Select the authoritative configuration source/distribution mechanism and
  atomic generation/rollback policy.

### Identifier inventory

- [ ] Inventory all current callers, service tokens, products, profiles,
  capabilities, providers, and model mappings without outputting secrets.
- [ ] Define stable workload IDs for current Classroom call paths.
- [ ] Define exact profile grants for Classroom and evaluation workloads.
- [ ] Define the first pilot's product, workloads, capabilities, and profiles.
- [ ] Define provider route IDs separately from stable profile names.
- [ ] Define capacity pool/reserve and usage-schema identifiers.

### Contract work

- [ ] Document released Classroom v1 compatibility fixtures.
- [ ] Define exact workload/product/profile grant configuration/schema.
- [ ] Define strict transport-only profile configuration and forbidden product-
  logic/content fields.
- [ ] Define immutable configuration generation, activation, readiness, and
  rollback semantics.
- [ ] Define readiness state and safe response semantics.
- [ ] Define normalized workload claims/credential behavior.
- [ ] Define generic content-free usage envelope and idempotency.
- [ ] Define exact accounting points for received/accepted audio, exact/estimated
  tokens/audio, partial work, disconnect, cancellation, and ambiguous outcomes.
- [ ] Define normalized realtime sequence, rotation, audio, usage, close, and
  error events.
- [ ] Define input sequence/acceptance acknowledgment, unacknowledged window,
  replay watermark, output backpressure, slow-consumer, and drain semantics.
- [x] Define provider-neutral batch speech synthesis for the approved Dali Chat
  pilot, with product-owned text/voice selection and transient binary output.
- [ ] Add positive, negative, limit, and compatibility examples.
- [ ] Validate OpenAPI, JSON Schema, examples, and backward compatibility.

### Exit gate

- [ ] All schema-affecting decisions are recorded.
- [ ] Classroom v1 compatibility fixtures validate.
- [ ] First pilot and rollback owners approve the consumer manifest.
- [ ] No Host runtime change is included.

## 8. G1 - Exact Authorization and Truthful Readiness

### Objective

Close the existing shared-profile authorization gap and make readiness describe
enabled production profiles rather than merely constructed adapters.

### Policy model

- [x] Introduce a typed workload/caller grant model containing exact products,
  capabilities, and profile names.
- [x] Replace permissive profile dictionaries with strict typed transport-policy
  models whose unknown fields are forbidden.
- [x] Explicitly reject prompt, instruction, terminology, workflow, plan/tier,
  product-content, user/account, MCP, and durable-state fields in profiles and
  grants.
- [x] Reject overlapping/conflicting grant records deterministically.
- [x] Remove prefix authorization for `shared.*` profiles.
- [x] Grant evaluation profiles only to dedicated evaluator workloads.
- [x] Preserve current Classroom product/profile behavior through explicit seed
  or compatibility configuration.
- [x] Add independent workload/product/profile/capability kill switches.
- [x] Keep all new non-Classroom grants disabled.

### Configuration generations

- [ ] Load policy/profile/grant data from the G0-approved authoritative source
  as one immutable content-free generation with a stable generation ID.
- [x] Validate the complete generation before activation; prohibit partial
  record-by-record mutation of live policy.
- [x] Atomically swap only a fully valid generation.
- [x] Retain the last-known-good generation when load/validation fails and emit
  a safe readiness/alert state.
- [x] Roll back by activating a previously validated generation.
- [x] Ensure diagnostics contain generation ID and normalized outcome only, not
  secrets or submitted configuration values.

### Transitional authentication

- [x] Continue comparing configured legacy service tokens in constant time.
- [x] Bind a legacy token to exactly one configured caller/workload grant.
- [x] Reject body/header caller identity that conflicts with authenticated
  credential identity.
- [x] Support safe token overlap rotation if retained during G2 transition.
- [x] Never log credential values or configuration containing them.

### Readiness

- [x] Distinguish provider adapter construction from credential/route readiness.
- [x] Validate every enabled profile has a valid allowed route configuration and
  every profile marked required for the deployment resolves to at least one
  credential-ready route.
- [x] Do not treat an unreachable/unconfigured Ollama adapter as ready.
- [x] Represent unrelated profile degradation independently.
- [x] Add a content-free periodic probe/state interface where provider policy
  permits probing.
- [x] Run probes asynchronously outside the readiness request path with bounded
  timeout/rate and a maximum cached-state staleness.
- [x] Mark which enabled profiles are required for the deployment; an optional
  degraded profile must not flap unrelated readiness.
- [x] Make readiness depend on required policy/config generation.
- [x] Return only safe aggregate readiness details.

### Verification

- [x] Classroom caller cannot request an evaluation or another product profile.
- [x] Evaluator cannot request Classroom production profiles unless explicitly
  granted.
- [x] Capability mismatch remains denied.
- [x] No credentials/provider routes makes provider work not ready.
- [x] One disabled/degraded profile does not incorrectly disable unrelated
  profiles unless global policy requires it.
- [x] Existing Classroom API and WebSocket contract tests remain unchanged.
- [x] Profile/grant schemas reject every forbidden product-logic/content field
  and unknown field.
- [x] Invalid/partial configuration cannot replace the last-known-good
  generation; atomic activation and rollback pass concurrency tests.
- [x] Privacy scan covers new policy/readiness diagnostics.

### Exit gate

- [x] Exact grant tests pass with no prefix bypass.
- [x] Readiness matches enabled route availability in deterministic tests.
- [x] Classroom v1 remains green.
- [x] New callers remain disabled.

## 9. G2 - Workload Identity and Content-Free Usage

### Completion decision gate

Local Gateway and product-consumer implementation is complete up to the
external contract/resource boundary. G2 production activation remains blocked
on:

1. the stable Platform production issuer and JWKS URL;
2. the exact SQS region, queue URL, IAM role, and deployed relay owner;
3. the exact provider-accepted fields that may become billing/quota authority;
4. runtime measurement finalization/delivery for every batch and realtime
   terminal disposition; and
5. Platform MS2/MS3 workload-token and idempotent-ingestion conformance
   fixtures.

Approved identifiers are audience `dali-ai-gateway` and scope
`ai_gateway:invoke`, with five-minute access tokens and 30 seconds of clock
skew. The approved durable sink technology is AWS SQS Standard in the aws-us2
deployment region, consumed by a product-owned relay that deduplicates by
`event_id` before account association. The stable Platform production issuer,
JWKS URL, exact SQS region/queue URL, IAM role, and Platform conformance
fixtures remain operator/external inputs and are not provisioned by this plan.

### Workload identity

- [x] Add an injected workload authenticator interface independent of HTTP/WS
  routes.
- [x] Implement the approved short-lived token/credential verifier.
- [x] Derive workload identity from the verified credential, not an untrusted
  caller header/body.
- [x] Validate issuer/audience/principal type/scopes or equivalent credential
  attributes.
- [x] Implement bounded JWKS caching with periodic refresh, refresh on an
  unknown `kid`, current/previous overlap, clock-skew limits, and key-retirement
  tests.
- [x] Define last-known-good JWKS behavior during Platform/JWKS outage; never
  accept an unknown key, extend token lifetime, or disable normal time checks.
- [x] Set a maximum JWKS cache staleness after which new workload authentication
  fails closed until trusted keys refresh.
- [x] Bound JWKS fetch timeout/retry and prevent a request stampede on unknown
  keys.
- [x] Map workload identity to G1 exact grants.
- [x] Support current/previous credential/key overlap and independent revocation.
- [x] Retain legacy token authentication behind a disabled-by-default,
  caller-specific compatibility switch during cutover.

### Measurement model

- [x] Define typed, versioned content-free measurements for text generation,
  batch transcription, realtime transcription, translation text/audio, and
  speech synthesis where enabled.
- [x] Generate a stable measurement event ID from safe request/session identity
  and measurement version.
- [x] Include workload, product, profile, capability, route, token/audio counts,
  timestamps, disposition, fallback, and rotation only as approved.
- [x] Exclude user/account IDs and all prompt/output/audio/tool/product-object
  content.
- [x] Normalize missing/estimated provider usage explicitly; never fabricate
  exact values.
- [~] Track received audio separately from audio accepted by the provider
  adapter/transport; designate the exact contract field eligible for billing or
  quota policy.
- [~] Distinguish provider-reported tokens/audio from Gateway estimates and
  include the versioned estimation method when estimated.
- [x] Finalize partial measurements for disconnect, timeout, cancellation,
  rotation, partial output, and ambiguous provider acceptance/charging.
- [~] Track generated/forwarded output audio separately from input audio.

### Delivery

- [x] Implement the approved external durable content-free sink adapter for realtime
  measurements that must survive caller disconnect.
- [ ] Implement exact batch response/product-relay behavior where approved.
- [~] Permit duplicate relay of a received realtime final event only with the
  same measurement event ID so Platform deduplication is deterministic.
- [ ] Make Platform ingestion idempotent by measurement event ID.
- [~] Define behavior when measurement delivery fails before, during, or after
  provider work.
- [ ] Prohibit silent measurement loss after incurred provider work.
- [ ] Keep realtime profiles without a durable sink non-authoritative for
  billing/quota and ineligible for shared-production readiness.
- [ ] Add retry/reconciliation ownership without storing content in Gateway.

### Cutover and verification

- [~] Shadow the new Classroom workload identity before disabling the legacy
  token path.
- [ ] Shadow/compare measurements without double-counting Platform usage.
- [x] Exercise identity rotation, revocation, sink failure, replay, and rollback.
- [x] Exercise Platform/JWKS outage, cache staleness, unknown-key refresh, fetch
  stampede prevention, and previous-key retirement.
- [ ] Compare measurements across provider-reported, estimated, partial,
  disconnected, cancelled, and ambiguous scenarios.
- [x] Verify no content enters measurement payloads, logs, traces, or errors.

### Exit gate

- [x] One workload cannot impersonate another or request its grants.
- [x] Measurement replay is idempotent and conflict-safe.
- [~] Delivery failure behavior is tested and owned.
- [x] Classroom remains operational with a tested credential rollback.

Consumer credential integration is implemented in both
`dali_classroom_server` and `dali_chat/server`. Each supports either a legacy
static token or a rotating Platform workload-JWT file, reads the file for each
new request/realtime connection, and rejects ambiguous dual configuration.
The reviewed two-product activation generation is
`deploy/aws-us2/two-product.env.example`; it remains incomplete until the
operator supplies the issuer, JWKS, queue, IAM, and protected credential values.

## 10. G3 - Shared Admission, Reserves, and Circuit State

### Shared admission

- [x] Introduce an injected admission-store interface with atomic acquire,
  renew/heartbeat where required, release, expiry recovery, and inspection.
- [ ] Implement shared leases/counters using the approved technology.
- [ ] Enforce workload, product, capability/profile, provider-route, realtime/
  batch, concurrency, and approved cost/volume dimensions.
- [ ] Bound lease duration and recover abandoned work after process failure.
- [x] Release capacity on success, failure, cancellation, timeout, disconnect,
  and shutdown.
- [ ] Implement the approved fail-closed or conservative degraded behavior;
  never silently return to independent process-local limits.

### Capacity reserves

- [ ] Define independently configurable Classroom, Host, evaluation, and future
  product pools.
- [ ] Enforce the documented Host reserve before any Host migration.
- [ ] Keep reserve borrowing disabled unless explicitly approved.
- [ ] If borrowing is approved, make it bounded, observable, and immediately
  reclaimable.
- [ ] Add per-product/profile rollout and emergency admission switches.

The local admission controller now accepts product, profile, and provider-route
dimensions for HTTP workloads with deterministic fallback to the existing
caller/capability limits. Realtime routes remain on the compatibility
caller/capability key until the versioned G4 session contract is implemented.

### Provider circuit state

- [~] Add shared route state: configured, healthy, degraded, open, and disabled.
- [~] Record only bounded content-free failure/latency counters.
- [~] Implement open-until/retry-after and operator/product kill switches.
- [x] Prevent one route's failure from poisoning unrelated routes.
- [~] Feed circuit state into readiness, routing, and admission.

### Verification

- [ ] Multi-process tests cannot exceed shared limits.
- [ ] Expired leases recover without double release.
- [ ] New product traffic cannot consume the Host reserve.
- [ ] Dependency degradation follows the approved safe ceiling/fail-closed rule.
- [x] Provider circuit transitions are deterministic and content-free.

The local circuit state machine and exact route kill switch are integrated into
batch provider work behind `AI_GATEWAY_PROVIDER_CIRCUIT_ENABLED=false` by
default. It fails a disabled/open route before provider work and keeps route
state isolated. Realtime integration, safe readiness details, retry-after,
latency counters, and shared persistence remain deferred until the G0
shared-state technology and degraded-mode policy are approved.

### Exit gate

- [ ] Shared admission and reserve tests pass under concurrency/process failure.
- [ ] Circuit state affects only intended profiles/routes.
- [ ] No new product is enabled.

## 11. G4 - Realtime v2, Translated Audio, and Conditional Speech

### Contract evolution

- [ ] Preserve v1 Classroom WebSocket paths/events.
- [ ] Add the approved versioned realtime contract rather than repurposing v1
  fields.
- [ ] Add request/session ID and monotonic sequence requirements.
- [ ] Add negotiated modality/format/limit data to `session.ready`.
- [ ] Add monotonically increasing input sequence numbers to `audio.append`.
- [ ] Add `audio.accepted` with the highest contiguous sequence handed to the
  active provider adapter/transport; document that this is not semantic provider
  completion.
- [ ] Negotiate maximum chunk bytes and maximum unacknowledged chunks/bytes.
- [ ] Add transcript and translation text delta/final events with stable item
  identity.
- [ ] Add `translation.audio.delta/final` with response ID, sequence, target
  language, codec, sample rate, channels, sample format, and disposition.
- [ ] Add `usage.update/final`; `session.rotation_required` with deadline and
  last accepted input sequence; `session.closed` with disposition, accepted
  input watermark, and final output sequence; cancellation; and normalized
  failure-stage semantics.
- [ ] Add a normalized `slow_consumer`/backpressure disposition and send-timeout
  behavior.
- [ ] Make unknown terminal events fail closed in clients/conformance fixtures.

### Provider-neutral session behavior

- [ ] Extend provider base protocols without leaking native provider events.
- [ ] Keep one source or one target-language lane per Gateway session.
- [ ] Preserve product ownership of multi-target orchestration and partial
  failure UX.
- [ ] Forward translated audio transiently without assembling/persisting it.
- [ ] Enforce per-chunk and per-session memory/duration ceilings.
- [ ] Enforce bounded outbound event/byte queues and provider/caller send
  timeouts.
- [ ] Pause provider reading where safely supported; otherwise close a slow
  consumer rather than silently dropping final text or translated-audio chunks.
- [ ] Permit delta coalescing only when contract tests prove it cannot alter
  final text/audio semantics.
- [ ] Surface planned provider expiry as rotation, not generic outage.
- [ ] Require explicit product-service reconnect after provider content
  acceptance; do not silently restart a lane.
- [ ] Return accepted-input/output watermarks so the product owns an explicit
  bounded replay-tail and duplicate-suppression decision.

### Conditional speech synthesis

- [x] Define a provider-neutral batch text-to-speech request and transient
  binary response for Dali Chat. Streaming speech remains deferred.
- [ ] Use abstract voice profiles; reject caller-selected provider voice/model
  IDs.
- [ ] Keep voice cloning enrollment, consent, recordings, and durable voice
  assets outside Gateway pending a separate privacy design.
- [ ] If speech is not approved, record it as deferred and do not implement an
  unused surface.

### Verification

- [ ] JSON Schema and example fixtures validate every event and terminal path.
- [ ] Sequence/duplicate/gap handling passes conformance tests.
- [ ] Input acknowledgment, unacknowledged-window enforcement, and replay
  watermark pass loss/duplication simulations.
- [ ] Slow-consumer tests bound memory and never silently omit final text/audio.
- [ ] Rotation, clear, cancellation, disconnect, and timeout clear buffers.
- [ ] Audio format/size violations fail without echoing content.
- [ ] Provider adapters emit only normalized events.
- [ ] Classroom v1 remains unchanged.

### Exit gate

- [ ] Fake-provider end-to-end v2 sessions pass text, audio, rotation, usage,
  cancellation, and error scenarios.
- [ ] At least one approved provider adapter passes applicable conformance.
- [ ] No product migration is implied.

## 12. G5 - Routing, Failure, and Fallback

### Route policy

- [ ] Replace one provider/model mapping with an ordered typed route policy while
  preserving existing single-route Classroom configuration.
- [ ] Encode capability, modality, language, privacy, output-contract, and usage
  compatibility requirements.
- [ ] Require product approval for every route candidate.
- [ ] Keep fallback disabled per profile until compatibility fixtures pass.

### Failure semantics

- [ ] Distinguish failure before provider acceptance, unambiguous no-result,
  ambiguous acceptance/charge, partial output, planned rotation, cancellation,
  and terminal provider failure.
- [ ] Allow bounded retry/fallback only at design-approved safe points.
- [ ] Prohibit silent batch duplication after ambiguous outcomes.
- [ ] Prohibit silent realtime failover after audio/content acceptance.
- [ ] Return safe normalized failure stage, retryability, and optional retry
  delay without raw provider payload.

### Circuit integration

- [ ] Skip open/disabled routes before admission/provider work.
- [ ] Update circuit state from normalized outcomes.
- [ ] Keep fallback counts and selected route in content-free measurement.
- [ ] Ensure a fallback route cannot weaken approved privacy/model terms.

### Verification

- [ ] Route ordering and selection are deterministic.
- [ ] Text-only routes cannot replace audio-producing routes.
- [ ] Tool/structured-output capability cannot silently disappear.
- [ ] Ambiguous failures never trigger automatic duplicate work.
- [ ] Circuit opening/recovery and kill switches pass concurrent tests.

### Exit gate

- [ ] Each enabled fallback pair has explicit compatibility evidence.
- [ ] Outage and partial-output simulations pass.
- [ ] Fallback remains disabled for unapproved profiles.

## 13. G6 - Resilience and Operational Readiness

### Deployment and dependencies

- [ ] Run at least two Gateway instances in the target pre-production
  environment.
- [ ] Verify shared admission, reserve, circuit, rollout, and usage-delivery
  behavior across instances.
- [ ] Remove unintended regional credential/state dependencies before claiming
  regional independence.
- [ ] Bound HTTP/provider/database/shared-state connection pools and overload
  behavior.
- [ ] Verify graceful shutdown drains/rejects work within the configured window
  and clears transient buffers.
- [ ] On planned drain, stop new admission and emit
  `session.rotation_required` with deadline/accepted-input watermark to active
  realtime callers before `session.closed` where possible.
- [ ] Release shared leases and close provider sessions on normal and forced
  termination without persisting in-flight content.

### Observability

- [ ] Add redacted metrics for admission, active leases, provider route outcome,
  first/final/total latency, audio/token usage, rotation, fallback, WebSocket
  disposition, usage delivery, readiness, config generation, and key age.
- [ ] Add product/profile/provider dashboards without user/content dimensions.
- [ ] Add alerts for capacity exhaustion, reserve encroachment, provider circuit,
  abnormal disconnects, usage lag/loss, readiness, and credential/key age.
- [ ] Verify logs/traces/metrics contain no prohibited content or identifiers.

### Runbooks and drills

- [ ] Write deployment, configuration, credential/key rotation, provider outage,
  shared-state failure, usage failure, rollback, and incident runbooks.
- [ ] Run batch/realtime load tests at and above approved capacity.
- [ ] Run provider outage and capacity-exhaustion drills.
- [ ] Run multi-hour realtime soak tests with repeated provider rollovers.
- [ ] Run slow-consumer, input-backpressure, planned drain, and forced-process
  termination tests with sequence/watermark verification.
- [ ] Test process/node termination with active sessions and lease recovery.
- [ ] Name service, provider, capacity, security, incident, deployment, and
  rollback owners.

### Exit gate

- [ ] Approved SLO/load/outage/soak thresholds pass.
- [ ] Multi-instance admission and reserve behavior pass.
- [ ] Usage delivery/reconciliation and rollback drills pass.
- [ ] No unresolved critical privacy, security, or operations finding remains.
- [ ] Operational review approves a restricted non-Classroom canary.

## 14. G7 - First Non-Classroom Consumer Canary

### Consumer manifest

- [ ] Record product/service/client repositories and owners.
- [ ] Record workload IDs, product ID, capabilities, exact profiles, and route
  approvals.
- [ ] Inventory transient content types and confirm product prompt/content
  ownership.
- [ ] Record expected concurrency, duration, audio/token volume, priority, and
  reserve relationship.
- [ ] Record product-side user authorization, plan/tier, retention, deletion,
  retry, and durable usage association.
- [ ] Record canary cohort, success metrics, rollback triggers, and direct-
  provider rollback owner.
- [ ] Prove mobile/browser clients cannot reach Gateway directly.

### Integration

- [ ] Implement a product-owned Gateway adapter; do not import Gateway runtime
  modules.
- [ ] Map product prompts/workflow to transient generic capability inputs.
- [ ] Verify Gateway does not store product prompts or output.
- [ ] Add workload credential/profile configuration disabled by default.
- [ ] Add shadow comparison without duplicate product writes or uncontrolled
  provider cost.
- [ ] Add product-specific rollout and immediate rollback switches.

### Canary and expansion

- [ ] Start with one bounded capability and dedicated cohort.
- [ ] Monitor product outcome, Gateway QoS, usage parity, capacity, and privacy
  without content/user identifiers.
- [ ] Exercise direct-provider rollback during the canary.
- [ ] Resolve every unexplained mismatch before increasing traffic.
- [ ] Expand capabilities/traffic only through separately approved gates.

### Interpreter/Host condition

- [ ] If Interpreter is selected, write and approve a separate Interpreter
  Gateway migration design before editing `app_server`.
- [ ] Close or explicitly approve a documented waiver for the existing
  Classroom shared-operation gates: provider quality, privacy/data-use terms,
  latency, one-hour/multi-rollover reliability, capacity reserve, provider-
  outage rollback, and content-free usage parity.
- [ ] Map transcript, translated text/audio, speech, multi-target lanes, QoS,
  rollover, billing usage, and rollback explicitly.
- [ ] Preserve Host behavior and reserve throughout the canary.

### Exit gate

- [ ] Product correctness, latency, reliability, usage, privacy, and rollback
  gates pass.
- [ ] The caller cannot access another product/evaluation profile or reserve.
- [ ] Product logic/content remains outside Gateway by inspection.
- [ ] Operational review approves the next cohort.
- [ ] Passing one product does not auto-approve another.

## 15. Optional A1 - Agent Inference

This workstream is not required for core multi-product Gateway readiness. It
starts only after G1-G6 and follows
[`agent_runtime_mcp_boundary_design.md`](agent_runtime_mcp_boundary_design.md).

- [ ] Select one approved agent runtime and read-only/no-side-effect pilot.
- [ ] Define separate `agent_inference` request and streaming contracts.
- [ ] Add normalized messages, tool declarations, tool-call proposals/results,
  cancellation, usage, and terminal states.
- [ ] Keep MCP discovery, credentials, connection, approval, tool execution,
  agent loop, prompt, and conversation state in the agent runtime.
- [ ] Implement fake-provider and one approved provider conformance fixtures.
- [ ] Shadow and canary with direct-provider rollback.
- [ ] Do not build an MCP facade without a real approved consumer and separate
  threat model.

Exit gate: all agent-boundary readiness criteria pass without Gateway connecting
to MCP or executing a tool.

## 16. Verification Matrix

### Gateway repository

```powershell
python -m compileall -q app tests
python -m pytest -q
python -m scripts.export_openapi --check
```

### Contract verification

- [ ] OpenAPI remains 3.1 and validates.
- [ ] Realtime/event schemas remain JSON Schema 2020-12 and validate.
- [ ] Every example validates against its schema.
- [ ] Backward compatibility with released Classroom v1 passes.
- [ ] Fake-provider conformance covers every capability/event/terminal state.
- [ ] Unknown fields/events follow documented compatibility/fail-closed rules.

### Security and privacy

- [ ] Wrong/missing/revoked workload and wrong product/profile/capability fail.
- [ ] Evaluation/shared profile isolation passes.
- [ ] Credential rotation and overlap pass without secret output.
- [ ] Request validation/provider errors contain no submitted values.
- [ ] Source/privacy scan covers logging, tracing, metrics, queues, exception
  serialization, and newly introduced state stores.
- [ ] Strict profile/grant schemas reject prompts, instructions, terminology,
  workflow, plan/tier, product content, and user/account fields.
- [ ] Static independence scan rejects runtime imports from `app_server`,
  `dali_ai`, Classroom, Host, and Platform repositories.
- [ ] Buffer clearing passes success/error/cancel/timeout/disconnect/shutdown.
- [ ] No Dali account ID/user token enters Gateway contracts.

### Concurrency and failure

- [ ] Shared limits/reserves pass multi-instance concurrency tests.
- [ ] Input sequence/acceptance watermarks and output backpressure pass loss,
  duplication, slow-consumer, rotation, drain, and reconnect simulations.
- [ ] Lease expiry/recovery and circuit transitions pass deterministic tests.
- [ ] Provider timeout/outage/partial/ambiguous/cancellation paths pass.
- [ ] Usage sink/relay failure, replay, conflict, and reconciliation pass.
- [ ] Load and soak tests meet approved thresholds.

No slice passes solely on unit tests when it changes authentication, shared
state, realtime protocols, provider routing, usage authority, or production
rollout.

## 17. Consumer Manifest Template

```text
Product:
Product service/repository:
Client/repository (if relevant):
Product owner:
Service owner:
Gateway owner:
Capacity owner:
Rollback owner:

Product ID:
Workload IDs and credential owner:
Capabilities:
Exact profile grants:
Approved provider routes/privacy terms:
Transient input/output content classes:
Prompt/instruction/terminology owner:
Durable content owner and retention/deletion behavior:
Product user/account/plan-tier authorization owner:

Expected concurrency:
Expected request/session duration:
Expected token/audio volume:
Priority/capacity pool:
Reserve relationship:
Usage schema and durable relay/sink owner:

Shadow method:
Canary cohort:
Success/SLO thresholds:
Rollback triggers:
Direct-provider rollback:
Observation window:
Expansion gates:
```

## 18. Rollback Principles

- Preserve released Classroom v1 routes/contracts throughout rollout.
- Additive configuration/contracts are preferred; disable new paths before
  removing schema or code.
- Roll back by workload/product/profile/route rather than globally when safe.
- Keep direct-provider rollback until the approved observation window passes.
- Never roll back by restoring a compromised credential; rotate/revoke instead.
- Do not discard content-free usage/idempotency/reconciliation records required
  for billing or audit integrity.
- Do not silently downgrade capability, modality, privacy, language, or output
  semantics during fallback/rollback.
- A failed realtime lane closes/rotates explicitly; it is not invisibly replayed
  by Gateway.
- Production config/backfill changes require dry-run, redacted comparison,
  idempotent apply, and an approved reversal procedure.

## 19. Completion Definition

Core multi-product Gateway implementation is complete for an approved pilot only
when:

- G0-G7 exit gates pass;
- Classroom v1 remains compatible and operational;
- exact workload/product/profile grants are enforced;
- readiness accurately reflects every enabled profile dependency;
- configuration generations validate and activate atomically without allowing
  product logic/content fields;
- workload credentials are independently rotatable;
- shared admission, circuit state, and Host reserve work across instances;
- realtime rotation/terminal/usage semantics are normalized and tested;
- realtime input acknowledgment, replay watermarks, and output backpressure are
  bounded and tested;
- translated audio and speech exist only if required and approved;
- usage is content-free, idempotent, and durably deliverable;
- provider failure/fallback behavior cannot duplicate or silently weaken work;
- content/privacy inspection finds no durable or diagnostic leakage;
- load, outage, soak, canary, and rollback gates pass; and
- design, contracts, runbooks, configuration, and this status plan match the
  deployed behavior.

Completion for one consumer does not approve another product, capability,
provider, model, agent runtime, or Host migration.
