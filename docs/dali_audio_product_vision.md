# Dali Audio — Product Vision

Status: Proposed kickoff direction  
Date: 2026-09-07  
Home: `dali_ai_gateway`

Architecture update: [Dali Audio Architecture](dali_audio_architecture.md)
places the app backend and shared narration capabilities in
`dali_audio_server`. It supersedes this kickoff document's placement of
planning, durable jobs, content storage, and user authorization inside
`dali_ai_gateway`; the Gateway remains the stateless inference data plane.

## 1. Vision

**Turn written content into natural, expressive speech through a reusable, provider-independent service.**

Dali Audio is primarily a server-side capability within `dali_ai_gateway`. It plans how content should be narrated, coordinates speech synthesis, and produces reusable audio assets. Dali Podcasts will consume this capability. A future Dali Audio app may expose it for Dali-provided and user-provided content, and other Dali apps may use it for narration.

The product boundary is deliberate: **Dali Audio owns narration and speech generation; consuming apps own their content and workflows.**

## 2. Problem

Calling a text-to-speech model is straightforward. Producing consistent, expressive, editable narration is harder. Applications otherwise repeat the same work: preparing text, selecting voices, handling pronunciation, dividing long passages, retrying failed synthesis, regenerating sections, and assembling output.

Direct provider integration also ties application behavior to vendor-specific voices, controls, limits, and output formats. Dali Audio provides one stable contract while allowing providers and models to evolve behind it.

## 3. Principles

- **Separate narration intelligence from synthesis.** Decide what to say and how to deliver it before invoking a speech provider. Planning can begin with deterministic rules; it does not require an LLM.
- **Preserve source meaning.** Default to faithful reading. Do not silently summarize, rewrite, or add spoken content. Any editorial transformation must be explicitly requested and reviewable.
- **Use stable Dali profiles.** Apps select Dali voice and style identifiers, not vendor voice IDs. Provider-specific configuration stays inside the gateway.
- **Make work inspectable and repeatable.** Persist source, plan, profile versions, synthesis settings, and asset lineage. Repeatability means traceable inputs, not guaranteed identical audio from nondeterministic models.
- **Treat segments as editable units.** Retry or regenerate a section without paying to regenerate an entire narration.
- **Keep delivery choices honest.** Record requested and applied controls; do not imply that all providers support equivalent expression or timing.
- **Build on gateway infrastructure.** Reuse authentication, authorization, provider credentials, storage conventions, metering, and observability where available.

## 4. Users and Use Cases

| User / consumer | Primary need |
| --- | --- |
| Dali Podcasts production workflow | Generate and revise narration from an approved script; receive audio for downstream episode production. |
| Dali content creators | Narrate stories, reflections, Scripture readings, educational material, and promotional text. |
| Future Dali Audio app users | Turn Dali-provided or their own written content into speech using understandable voice and style choices. |
| Other Dali app teams | Add narration without implementing provider selection and audio generation infrastructure. |

First end-to-end use case: submit an approved single-speaker script, generate narration, review the result, regenerate one section, and export an assembled audio file.

## 5. Architecture and Ownership

```text
Dali Podcasts     Future Dali Audio app     Other Dali apps
      \                    |                    /
                    dali_ai_gateway
                           |
                   Dali Audio API
                           |
          Narration planner + profile resolver
                           |
                 Generation orchestrator
                           |
                 Speech provider interface
                           |
          Provider/model adapters → speech services
                           |
                Assembly + asset delivery
```

| Layer | Owns | Does not own |
| --- | --- | --- |
| Consuming apps | Content selection and editing, user experience, business workflow, review decisions | Provider credentials, vendor-specific synthesis orchestration |
| Dali Podcasts | Episodes, shows, series, show notes, artwork, scheduling, publishing, episode production | Shared narration and speech generation logic |
| Dali Audio | Narration plans, profile resolution, segmentation, synthesis jobs, retries, section regeneration, narration assembly, audio assets | Episode/show entities, podcast publishing, content discovery, general media editing |
| Narration planner | Spoken text, segment boundaries, pronunciation hints, delivery intent, pauses | Provider calls or audio generation |
| Speech adapter | Translate a resolved request into a provider call; normalize results and errors | Editorial decisions or application workflow |

These are logical boundaries inside the gateway, not a requirement to deploy separate services. Begin as a module with background execution appropriate to the existing gateway architecture.

## 6. Narration Pipeline

1. **Accept and validate.** Authenticate the caller; validate text size, language, profile access, format, and requested controls. Retain an immutable source revision.
2. **Plan.** Normalize input without changing meaning, identify paragraphs and sentences, create stable segment IDs, and attach delivery intent and pronunciation hints. Preserve source-to-segment mapping. Record the planner version.
3. **Resolve.** Map versioned Dali profiles to an eligible provider/model/voice and check capabilities. Split further for provider limits while retaining logical segment mapping. Persist the executable plan.
4. **Synthesize.** Execute segments as a background job with bounded concurrency, timeouts, metering, and retry rules. Persist successful segment assets as work completes.
5. **Assemble and validate.** Normalize compatible audio formats and combine segments in order with supported pauses. Check for missing segments, unreadable files, and invalid duration. Technical checks do not replace listening review.
6. **Deliver.** Return job state, segment metadata, warnings, and an access-controlled final asset reference with duration and format.
7. **Revise.** Regenerate selected segments as a new revision, reuse unaffected assets, and reassemble. Preserve previous revisions and their provenance.

The narration plan is the explicit handoff between planning and synthesis. It contains provider-neutral intent; provider resolution records how that intent will actually be realized.

## 7. Provider Abstraction

Define a small `SpeechProvider` contract for capability discovery and segment synthesis. Adapters may eventually cover OpenAI, Google, ElevenLabs, or other services; initial selection should follow a short audio evaluation and gateway integration review.

Each adapter declares supported languages, voice options, delivery controls, input limits, formats, and relevant operational limits. It returns normalized audio metadata, provider request references, usage where available, and structured errors. Provider errors should distinguish retryable failures from invalid or unsupported requests.

Routing resolves Dali profiles using configured mappings and caller policy. A provider preference may narrow eligible choices but must not expose credentials or arbitrary vendor configuration. Missing capabilities must produce an explicit rejection or an allowed degradation warning.

**Voice consistency takes priority over automatic fallback.** Pin the resolved voice/provider configuration for a narration revision. In the MVP, retry transient errors with the same configuration; fail visibly if unavailable. Future cross-provider fallback must be opt-in and explain potential voice changes rather than silently mixing voices within an asset.

## 8. App-Level Voice and Style Profiles

Profiles are app-facing identifiers managed and resolved by Dali Audio, with access scoped to the caller where needed.

| Profile | Purpose | Illustrative identifier |
| --- | --- | --- |
| Voice | Perceived speaker identity, language suitability, and approved provider voice mappings | `dali_reflective_male` |
| Style | Delivery intent such as pacing, warmth, energy, and pause preferences | `sacred_reflection` |

Keep voice identity separate from delivery style so the same voice can serve multiple contexts. Profiles are versioned; a job records the exact versions and resolved mapping. Changes to defaults affect new jobs, not previously generated revisions. Validate combinations because a style may not be realizable by every voice/model.

Apps can present friendly labels and curate available profiles. Profile names express intent, not a promise of identical sound across vendors.

## 9. API-Level Conceptual Model

This is a proposed domain model, not a frozen endpoint specification.

| Concept | Key information |
| --- | --- |
| Narration request | Source text, language, voice/style profile references, optional speed and pronunciation hints, output format, optional provider preference |
| Narration plan | Source revision, planner version, ordered stable segments, spoken text, delivery intent, profile versions, warnings |
| Generation job | Job ID, plan revision, state, progress, resolved provider configuration, errors, usage |
| Segment revision | Logical segment ID, revision, synthesis inputs, audio asset, provenance |
| Audio asset | Asset ID, authorized download URL, URL expiry, duration, format, source job/revision |

Core operations: create a narration job, inspect its plan and progress, retrieve its audio, and regenerate selected segments into a new revision. Previewing or editing a plan before synthesis can be added later without merging the planner and synthesizer.

Illustrative request:

```json
{
  "text": "A written passage to narrate.",
  "language": "en-US",
  "voice_profile": "dali_reflective_male",
  "style_profile": "sacred_reflection",
  "speed": 1.0,
  "output_format": "mp3"
}
```

Use asynchronous jobs: `queued → planning → synthesizing → assembling → succeeded`, with `failed` available from processing stages. Expose segment completion and structured failure details, but publish a final asset only when assembly is complete. Use polling first; webhooks can follow demand.

Require caller-scoped idempotency for creation and regeneration. Access to plans, jobs, segments, and assets must follow gateway tenant/user authorization. Apply input and usage limits, keep provider secrets server-side, and avoid logging source text by default. Define source/audio retention and deletion behavior before launch. The request schema must contain no episode, show, series, or publishing fields.

## 10. MVP

Deliver one production-quality vertical slice:

- Plain-text, single-speaker narration in one initial language selected from actual Dali content needs.
- One real speech provider behind the adapter interface, plus a fake adapter for contract testing.
- A small curated set of versioned voice/style profiles with verified provider support.
- A deterministic narration planner with sentence/paragraph segmentation, faithful text handling, and basic delivery intent.
- Background jobs, polling, bounded retries, idempotency, and visible failures.
- Persisted segment audio, regeneration of a selected segment, revision-safe assembly, and one agreed export format.
- Authorized asset delivery, usage reporting, latency/error metrics, and an agreed retention policy.
- A thin Dali Podcasts integration proving script-to-audio and section regeneration without podcast entities entering Dali Audio.

MVP completion depends on acceptable listening quality and reliable operation, not the number of integrated providers.

## 11. Non-Goals for MVP

- Podcast show management, episode metadata, publishing, distribution, or analytics.
- A standalone Dali Audio consumer app.
- Script/topic generation, silent rewriting, or content research.
- Music generation, background music mixing, recording, transcription, or general audio editing.
- Voice cloning, multi-speaker dialogue, real-time streaming, or a comprehensive audiobook production workflow.
- Automatic cross-provider failover or identical expression across providers.

## 12. Future Direction

Expand according to demonstrated consumer needs: additional providers and languages; richer pronunciation controls; reviewable intelligent narration planning; multi-speaker narration; better pause and pronunciation editing; callbacks; and opt-in routing policies balancing quality, cost, and latency.

A future Dali Audio app can provide content selection, previews, editing, and export over the same service. Dali Podcasts can grow its own episode production and publishing workflow independently. Domain-specific workflows remain in consuming apps.

## 13. Success Criteria

Before implementation, select a representative evaluation set and record a direct-provider baseline. Include short and long passages, names, punctuation, numbers, and material requiring restrained expression.

| Area | MVP acceptance evidence |
| --- | --- |
| Listening quality | Content reviewers accept naturalness, pronunciation, pacing, and voice consistency on the agreed sample set; all blocking defects are resolved. |
| Fidelity | Plans preserve source meaning; reviewers find no unexplained omissions, repetitions, or additions in the accepted audio samples. |
| Revision workflow | A selected section can be regenerated and assembled while unchanged segment assets are reused and previous revisions remain retrievable. |
| Reliability | Transient-error tests recover within retry bounds; permanent errors produce actionable failures; duplicate requests with the same idempotency key do not create duplicate jobs. |
| Abstraction | A consuming app uses only Dali profile IDs; adapter contract tests pass for the real and fake adapters. |
| Boundaries | Dali Podcasts generates and retrieves narration without Dali Audio storing podcast-specific entities. |
| Operations | Measure time to completed audio, failure rate, and cost per generated audio minute; agree pilot budgets and latency targets after the provider spike, before rollout. |
| Access | Cross-caller access is denied for jobs and assets; retention and deletion behavior is verified. |

Track the percentage of segments accepted without regeneration as a practical quality signal during the pilot. Set numeric quality and performance thresholds from the evaluation baseline rather than inventing them at kickoff.

## 14. Initial Implementation Plan

1. **Inspect the gateway and establish the baseline.** Identify existing provider, job, storage, authentication, and metering components. Evaluate candidate speech models on sample scripts. Choose the first language, provider/model, profiles, export format, limits, and pilot targets. Output: a short decision record and reviewed sample audio.
2. **Define contracts and persistence.** Specify request validation, plan/job/segment/asset schemas, profile versioning, provider capabilities, error taxonomy, idempotency, ownership, and retention. Output: API examples and adapter contract tests.
3. **Build the vertical slice.** Implement deterministic planning, one adapter, background synthesis, segment persistence, assembly, polling, and authorized delivery. Output: an end-to-end script-to-audio demonstration.
4. **Add revision and failure handling.** Implement selected-segment regeneration, immutable revisions, asset reuse, bounded retries, and safe resumption. Output: demonstrated edits and recovery from provider failures without corrupting completed work.
5. **Pilot through Dali Podcasts.** Connect its approved-script workflow, conduct listening review, verify authorization and operational behavior, and compare against the baseline. Output: documented acceptance results and a prioritized follow-up backlog.

Resolve during step 1: whether existing gateway background execution is sufficient, which provider yields the best acceptable narration for the initial content, and what storage lifetime and operating budget the pilot needs. Keep the first release small enough to validate narration quality and the service boundary before expanding features.
