# Dali Audio Architecture

Status: Proposed architecture following the agreed service separation  
Date: 2026-09-07  
Scope: Separate the Audio product backend, shared inference, consuming apps,
and Platform account and billing responsibilities.

## 1. Decision

Build `dali_audio_server` as both the Dali Audio app backend and the reusable
narration service. Do not introduce a separate `audio_gateway` deployment.
Keep app-facing endpoints, service-facing endpoints, and narration logic as
separate modules within the Audio server.

`dali_ai_gateway` remains the private, stateless inference data plane. It
provides provider-neutral access to text and speech models. `dali_platform`
owns shared accounts, entitlements, subscriptions, pricing, and billing.

This document updates the service placement in the
[Dali Audio product vision](dali_audio_product_vision.md). Its narration and
revision capabilities belong in `dali_audio_server`, rather than inside
`dali_ai_gateway`. Where the vision assigns durable content, product jobs, or
user authorization to the Gateway, this architecture takes precedence.

## 2. Service Layout

```text
Dali Audio app -----------------------> dali_audio_server
                                               ^
Dali Podcasts app -> podcasts_server -----------|
Other Dali apps ---> product backends ----------|
                                               |
                                               v
                                        dali_ai_gateway
                                               |
                                               v
                                         AI providers

dali_audio_server and product backends <-> dali_platform
    Account identity, entitlements, and billing integration

dali_ai_gateway -> content-free usage delivery -> product relay / Platform
```

Mobile and browser clients never call `dali_ai_gateway` directly. The Audio
app calls its Audio backend; other apps normally use their own product
backends to call the shared narration service. Product backends may also use
the AI Gateway independently for features unrelated to narration.

The initial Audio API and background worker can ship from one codebase and
release. Workers may run as separate processes for reliable execution and
capacity management without creating another product service boundary.

## 3. Ownership

| Component | Owns | Does not own |
| --- | --- | --- |
| `dali_audio_server` | Audio app APIs, resource authorization, narration plans, voice/style profiles, jobs, revisions, segment regeneration, assembly, audio assets, retention | Provider credentials or adapters, authoritative account records, pricing or billing ledger, podcast publishing |
| `dali_ai_gateway` | Authenticated service inference APIs, provider credentials and transport, model-profile routing, admission, timeouts, normalized errors, content-free inference measurements | User authorization, narration planning, durable source/audio content, product jobs, asset delivery |
| `dali_platform` | Accounts, identity contracts, subscriptions, entitlements, pricing, authoritative usage ledger and charges | Source scripts, narration plans, audio files, provider payloads, narration orchestration |
| Dali Audio app | Content entry, profile selection, preview and review UI, regeneration requests, export UX | Provider access, durable job execution, billing authority |
| Dali Podcasts and other product backends | Their original content, product permissions, business workflows, and references to narration jobs/assets | Shared narration implementation, provider adapters |

Dali Podcasts continues to own shows, episodes, artwork, scheduling, and
publishing. These entities do not enter the shared narration contract.

## 4. Audio Server Modules

- **App API:** serves the Audio app, validates Platform identity, and enforces
  account ownership and entitlements on each operation.
- **Service API:** authenticates consuming Dali services and validates their
  permitted account scope using a trusted service/delegation contract.
- **Narration planner:** preserves approved text, creates stable logical
  segments, and records pronunciation and delivery intent. Any editorial
  transformation requires an explicit product request.
- **Profile resolver:** owns versioned Dali voice/style definitions and maps
  their intent to approved Gateway transport profiles and supported controls.
- **Job coordinator and workers:** execute durable jobs, handle bounded
  retries, record progress, and regenerate selected sections.
- **Asset and revision manager:** stores source snapshots, plans, segment
  assets, assembled output, and lineage; enforces retention and deletion.
- **Usage integration:** associates content-free inference events with the
  correct product account and forwards billable usage to Platform.

Both API surfaces call the same narration application layer. There should be
one implementation of planning, synthesis coordination, and regeneration.

## 5. Narration Execution

1. The caller submits approved text, language, versioned voice/style choices,
   output format, and a caller-scoped idempotency key to the Audio server.
2. The Audio server authenticates the caller, verifies resource/account access
   and entitlements, applies product limits, and stores an immutable source
   snapshot and durable job.
3. The planner creates a versioned plan with stable logical segment IDs and
   source-to-segment mapping. The resolver checks supported controls and
   records the effective configuration.
4. A worker submits bounded segment inference requests to `dali_ai_gateway`
   using the Audio service identity and approved workload/profile grants.
   Only transient synthesis inputs cross this boundary; user identity and
   account credentials do not.
5. The Gateway calls the provider, returns binary audio and safe result
   metadata, emits content-free usage, and discards request/response content.
6. The Audio server persists completed segments, validates and assembles
   them, and marks the final asset available only after successful assembly.
7. The caller polls job progress and retrieves an authorized asset reference.
   Regenerating a section creates a new immutable revision, reuses unchanged
   assets, and preserves prior revisions until their retention period ends.

The first implementation should assess the existing
`POST /ai/v1/audio/speech` endpoint and OpenAI/Gemini speech adapters before
adding transport capabilities. The Audio server must not import Gateway
runtime modules or implement direct provider calls.

## 6. Contracts and Consistency

The Audio API exposes narration requests, plans, jobs, segments, revisions,
and assets. Its schemas contain no episode, show, or publishing fields.
Start with polling for asynchronous jobs; callbacks can follow demonstrated
demand.

Creation and regeneration require idempotency scoped to the authenticated
caller and account. Reusing a key with different inputs must fail explicitly.
Workers need durable execution state, bounded retry budgets, and atomic
completion updates so duplicate delivery cannot publish conflicting results.

Pin voice/style versions and the effective Gateway routing configuration for
a narration revision. A retry must preserve that configuration; unsupported
controls must fail or produce an explicitly allowed degradation warning.
Automatic cross-provider fallback is disabled for the MVP. Before building the
slice, verify whether the Gateway contract can preserve this configuration
across multiple calls; any required extension belongs in its transport policy.

A timeout after provider acceptance may have incurred usage even if no audio
was received. Job idempotency alone does not guarantee exactly-once provider
execution. Record separate attempts, bound retries, and reconcile usage using
the available provider/Gateway evidence.

## 7. Identity, Data, and Retention

Platform is the account and entitlement authority; the Audio server enforces
those decisions against its own resources. A service credential does not
automatically grant access to every account. The service-to-service contract
must establish which account a caller may act for, rather than trusting an
arbitrary account ID in the request body.

The Audio server owns durable narration data in its database and object
storage. Consuming products retain ownership of original content; the Audio
server holds the immutable snapshots necessary to reproduce and revise the
requested narration. Asset downloads require authorization or short-lived,
scoped download URLs. Treat those URLs as credentials and do not log them.

Before launch, define retention periods and deletion behavior for sources,
plans, segments, final assets, failed jobs, backups, and shared revision
references. Deleting an asset must account for retained revisions that still
reference it. Account deletion must propagate through the Audio service.

The Gateway never stores or logs audio, source text, plans, prompts, provider
payloads, secrets, or user identifiers. Its measurements remain content-free.
Audio server logs must also exclude content and credentials; authorized
product storage is separate from operational logging. Platform receives no
narration content.

## 8. Usage and Billing

Keep three responsibilities distinct:

| Responsibility | Owner |
| --- | --- |
| Measure inference attempts and provider usage | AI Gateway |
| Associate usage with a narration operation and authorized account | Audio server / product relay |
| Apply pricing, deduplicate ledger entries, and record charges | Platform |

Use content-free correlation and event identifiers compatible with the
Gateway usage contract. Keep the account-to-operation mapping outside the
Gateway. Do not send text, asset URLs, user identifiers, or content-bearing
provider metadata through the measurement pipeline.

Platform must deduplicate redelivered measurement events. A job summary and
its segment inference events must not independently charge for the same
usage. When Podcasts requests narration, define one billing account and one
reporting owner for that operation. Platform determines the charging policy
for failed attempts, regeneration, and any separate storage or assembly fees.

Entitlement checks and any budget reservation occur before execution through
the agreed Platform contract. Gateway admission protects inference capacity;
it is not a substitute for account credit or billing authorization.

## 9. Capacity and Failure Isolation

Register Audio as an independently controlled Gateway workload. Batch
narration must never consume a documented Host reserve. Apply both Audio
worker concurrency limits and Gateway admission limits so large scripts do
not crowd out realtime workloads. Any separate allocations for consuming
products must be enforced by trusted workload policy.

Queue work durably in the Audio service. Limit source size, segment count,
queued jobs, retries, and per-account concurrency. Support cancellation and
ensure workers stop scheduling new inference after cancellation. Specify
recovery and cleanup behavior for interrupted synthesis and assembly.

Track queue delay, completion latency, failure rate, regeneration rate, and
measured usage without recording content. Storage or worker failures remain
Audio service failures and must not require adding state to the Gateway.

## 10. Initial Delivery

1. Review existing speech transport, profile capabilities, usage correlation,
   and workload admission. Agree Platform identity, entitlement, and billing
   contracts. Record missing contracts before implementation.
2. Establish `dali_audio_server` with app/service API modules, a shared
   narration layer, durable jobs, and product-owned database/object storage.
3. Deliver plain-text, single-speaker narration using deterministic planning,
   one approved provider mapping, a small curated profile set, and one export
   format through the AI Gateway.
4. Add section regeneration, immutable revisions, asset reuse, cancellation,
   bounded recovery, authorized downloads, and verified retention behavior.
5. Prove reuse through the Podcasts backend and validate listening quality,
   cross-account access denial, billing deduplication, and capacity isolation.

Choose the initial language, provider/model, storage and queue technology,
retention periods, and pilot budgets during implementation planning. These
choices do not change the service ownership defined here.

## 11. Acceptance Criteria

- The Audio app and another product backend use the same narration logic.
- A script can become audio, one section can be regenerated, and unaffected
  assets and prior revisions remain intact under the retention policy.
- Only authenticated Dali services access the AI Gateway, and it persists no
  narration content or user identity.
- Cross-account job and asset access is denied by the Audio server.
- Repeated job submissions and usage deliveries do not create duplicate jobs
  or duplicate ledger charges; ambiguous provider attempts remain traceable.
- Audio load respects its configured limits and the documented Host reserve.
- Platform remains the account and billing authority and receives only
  content-free usage data.
