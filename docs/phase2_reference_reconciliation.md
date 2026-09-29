# Phase 2 reference workload and rollback reconciliation

2026-09-15. Read-only process/unit inspection; no inference, load test, service
restart, credential change or policy activation. Integrated acceptance follows
Phase 3 on us3. Scope is existing Classroom/Audio references, not permission to
onboard DaliJob or enlarge capacity.

## Current declarations versus historical documents

| Instance | Observed release | Reference workload and limit | Scope |
| --- | --- | --- | --- |
| aws-us2 production Gateway | `20260915T-gateway-3aa195b` | `dali_classroom_server`: 1 | Four Classroom text/transcription profiles; legacy service-token compatibility still listed |
| us3 original Gateway | `20260830T211723Z` | Classroom: 1; Scribe: 1 | Separate existing production process; not the Audio test Gateway |
| us3 Audio test Gateway | `audio-spike-20260908-v2-wav` | Audio: 1; Chat: 1 | Separate test instance, speech only, both legacy service identities |

Audio now uses product `dali_audio`, workload `dali_audio_server`, profiles
`dali_audio.speech.openai` and `dali_audio.speech.gemini`. Chat's separate test
grant uses product `dali_chat` and its `dali_chat.speech.*` profiles. The original
[spike deployment record](../deploy/us3/README.md) lists only Chat because it
predates Audio provisioning; do not reuse Chat identity for the Audio backend.
Platform billing still uses product `audio` and audience/client `dali-audio`:
these identifiers belong to different contracts and are not interchangeable.

Classroom's observed profiles on both hosts are
`classroom.translation.economy`, `classroom.summary.economy`,
`classroom.transcription.economy`, `classroom.transcription.live`.

aws-us2's current grants additionally include Chat, Scribe, Bible and Interpreter.
The active Platform-workload-JWT identity list includes Interpreter and Bible;
the legacy list includes Classroom, Chat and Scribe. The checked-in
`deploy/aws-us2/two-product.env.example` is an example, not current production
inventory: its older disabled Interpreter entry must not be used to overwrite
the active policy. No identities or routes were changed to resolve this drift.

## Capacity evidence and explicit limits

Observed us3 systemd ceilings: Audio test Gateway 2 CPU-seconds/second and 1 GiB;
Audio API 1 CPU-second/second and 512 MiB; durable worker 0.5 CPU-seconds/second
and 512 MiB. These are ceilings, not guaranteed reserves. aws-us2 Classroom and
Gateway units currently show no systemd CPU/memory ceiling.

Gateway workload limits isolate admitted callers on their configured instance.
They do not by themselves establish a global Host reserve across processes,
hosts or shared provider quotas. `AI_GATEWAY_SHARED_ADMISSION_REQUIRED` and
`AI_GATEWAY_USAGE_DELIVERY_REQUIRED` were unset in the inspected active process
environments; current local defaults are false. This is not evidence that an
optional sink/store is absent, nor evidence of replica-wide enforcement. Config
files/defaults and external quota sharing must be verified at acceptance.

**No numerical provider-wide Host reserve is certified by this inventory.**
Do not expand Audio concurrency, put batch Audio on a Host pool, enable new
consumer traffic, or claim HA-wide capacity until the post-Phase 3 review records
the actual shared admission backend, pool ceilings, Host reserve and provider
quota relationships. A new capacity partition is an architectural/operational
approval, not something to infer from idle CPU or these caller limits.

## Rollback reconciliation

- Audio API and worker both point to `commercial-20260908-v1`. Do not apply the
  original Chat-only spike rollback to the Audio commercial ledger/content.
  Preserve the protected content key and newer usage/deletion state; do not
  restore an older allowance DB over committed usage.
- Gateway profile/configuration IDs are pinned in Audio jobs. A rollback that
  changes effective configuration must stop generation for review, not silently
  replace voice/provider or regenerate already completed segments.
- Retain exact prior instance release/policy identifiers and protected credential
  references. Roll back only the target instance; do not stop the original us3
  Gateway/Host/Classroom when changing the isolated Audio test Gateway.
- Preserve acknowledged/undelivered usage outside the stateless Gateway. A
  Gateway rollback is not permission to discard product outbox/billing records.
- Validate proxy changes with a scoped diff/config check. The enabled us3 Apache
  site is a regular file; do not restore an entire historical vhost backup over
  unrelated routes. No proxy operation was performed in this reconciliation.

Evidence mechanism: the Platform repository's read-only
`scripts/inspect_reference_policy.py` filters active process environments to
allowlisted policy metadata and excludes credentials/content. Its release paths
identify deployments but do not prove Git provenance, full effective settings,
backup freshness, performance, or a successful rollback drill.
