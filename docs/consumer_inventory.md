# Gateway Consumer and Profile Inventory

Status: initial local inventory; credentials and production activation are not
included.

| Workload | Product | Current role | Profile namespace |
|---|---|---|---|
| `dali_classroom_server` | `classroom` | Classroom v1 compatibility pilot | `classroom.*` |
| `dali_chat_server` | `dali_chat` | Chat capability-demo pilot | `dali_chat.*` |

The Gateway grants are exact workload/product/profile/capability intersections.
No mobile or browser client calls the Gateway directly; clients call their
product server.

## Current capabilities

- Classroom: text generation, batch transcription, realtime transcription;
  realtime translation remains separately controlled by rollout/configuration.
- Dali Chat: text generation, batch/stream transcription, translation,
  realtime interpretation, speech synthesis, image analysis, and video
  analysis, subject to profile enablement and provider readiness.

## Provider route namespaces

Provider/model route IDs remain server-side and are derived from the configured
provider and model (for example `openai.gpt-realtime-translate`). They are not
caller-selectable identifiers.

## Not yet decided

- Platform workload-token profile and rotation overlap.
- Shared admission/circuit/usage state technology.
- Host reserve and capacity owner.
- Authoritative configuration distribution.
- Durable content-free usage sink and reconciliation owner.
- First non-Classroom canary and rollback owner.
