# DaliJob structured-output schema extension

Status: Approved for implementation
Date: 2026-09-21
Owners: Dali AI Gateway and DaliJob
First consumer: `dali_job_ai`

## Decision summary

Extend the Gateway's bounded `json_schema` validator so DaliJob can preserve the
constraints required by its Job Profile extraction contract. The extension is
generic Gateway capability, but activation remains profile-scoped. Initially,
only `dalijob.job.extract` and `dalijob.job.extract.repair` will use it.

The first increment will accept bounded enums, constants, nullable unions,
patterns, and the existing scalar/array limits. DaliJob will continue to flatten
local `$defs` references before sending a schema. The Gateway will not resolve
references, persist schemas, log schemas, infer product semantics, or relax
post-generation validation in DaliJob.

## Context and problem

The initial Gateway structured-output implementation accepts only structural
object, array, and primitive type declarations. It deliberately rejects enums,
patterns, unions, references, descriptions, and other JSON Schema keywords.

DaliJob's Job Profile schema depends on constraints that the initial subset
removes:

- enum membership for role families, career levels, evidence contexts,
  employment types, and other controlled vocabularies;
- nullable fields represented by `anyOf` or a primitive type plus `null`;
- constants for provider-owned values that must remain server-controlled;
- string patterns, including two-letter country codes and local-reference
  formats;
- collection and numeric bounds.

Flattening the schema to primitive types allowed requests through the Gateway,
but did not constrain these values at generation time. In the US3 controlled
run on 2026-09-21, three of five profiles were created and two were rejected by
DaliJob validation. Examples included an unsupported evidence context and
non-null placeholder text in a nullable country field. This is the correct
failure behavior, but it is not reliable enough to enable scheduled profiling.

Official OpenAI documentation describes Structured Outputs as supporting a
JSON Schema subset that includes enums, `anyOf`, string `pattern`, numeric
bounds, and array bounds. It also requires an object root, required object
properties, and `additionalProperties: false`. The proposed Gateway subset
remains narrower and more tightly bounded than the provider subset.

Reference: https://developers.openai.com/api/docs/guides/structured-outputs

## Goals

- Make DaliJob Job Profile generation conform to controlled vocabularies and
  nullable-field semantics before the result reaches application validation.
- Preserve the Gateway's stateless, product-neutral, content-free boundary.
- Keep existing `text` and `json` requests unchanged.
- Keep structured output explicitly opt-in per model profile.
- Reject unsupported or excessive schemas before capacity admission and
  provider execution.
- Retain DaliJob's full Pydantic and semantic validation as the final authority.

## Non-goals

- Supporting arbitrary JSON Schema.
- Adding DaliJob models, prompts, field names, or business rules to Gateway.
- Resolving external references or fetching schemas.
- Enabling structured output for every workload or model profile.
- Changing other application-server grants or profile behavior.
- Using Gateway validation as a substitute for product-side validation.

## Proposed contract

### Root and object rules

- The root must be an object.
- Every object must define `properties`, require every property, and set
  `additionalProperties` to `false`.
- Property names remain bounded to 128 characters.
- Root-level `anyOf` remains prohibited.

### Allowed node keywords

| Node | Allowed keywords |
| --- | --- |
| All typed nodes | `type`, `enum`, `const` |
| Object | `properties`, `required`, `additionalProperties` |
| Array | `items`, `minItems`, `maxItems` |
| String | `pattern`, `minLength`, `maxLength` |
| Number/integer | `minimum`, `maximum`, `multipleOf` |
| Nested union | `anyOf` with bounded, individually valid branches |

`type` may be a supported primitive name or a two-item nullable declaration
containing one non-null type and `null`. This supports Pydantic optional fields
without accepting unrestricted unions.

`enum` and `const` values must be JSON scalars compatible with the declared
type. Enum values must be unique. A nullable enum may include `null`.

The first increment will continue to reject descriptions, titles, examples,
defaults, conditionals, negation, `allOf`, external references, and arbitrary
extension keywords. Those fields are unnecessary for enforcement and could be
used to transport content through the Gateway schema envelope.

### References and definitions

DaliJob will flatten internal Pydantic `$defs`/`$ref` references before sending
the schema. The flattener must preserve the allowed constraints above and reject
cycles. The Gateway will continue rejecting `$ref` and `$defs` in this
increment, avoiding a new reference-resolution attack surface.

### Bounds

The existing limits remain and are supplemented as follows:

- maximum canonical schema size: 32 KiB;
- maximum nesting depth: 12;
- maximum visited nodes: 256;
- maximum properties per object: 100;
- maximum `anyOf` branches: 4;
- maximum enum values across the schema: 256;
- maximum enum values on one node: 100;
- maximum aggregate enum/const string bytes: 16 KiB;
- maximum pattern length: 256 characters;
- non-negative collection/string bounds with an implementation-defined hard
  ceiling;
- finite numeric constraints only; booleans are never accepted as numbers.

Pattern strings are treated as bounded declarative data and forwarded to the
provider. The Gateway must not apply them to product content. Unsupported
patterns fail closed before provider execution.

## Validation flow

1. Authenticate the workload and authorize product/profile/capability as today.
2. Validate the request envelope and require `structured_output` exactly when
   `response_format=json_schema`.
3. Validate and bound the schema without logging it.
4. Admit against the existing DaliJob background capacity pool.
5. Forward the unchanged validated schema to the configured OpenAI profile.
6. Reject provider refusals, incomplete output, or transport errors without
   fallback or silent JSON downgrade.
7. Return generated JSON to DaliJob.
8. DaliJob parses the provider DTO, validates evidence references and semantic
   policies, then persists only a valid immutable Job Profile.

## Security and privacy impact

The change increases accepted schema expressiveness, not accepted application
content. Existing content rules remain:

- schemas, prompts, source spans, provider payloads, and generated profiles are
  transient and must not be logged or persisted by Gateway;
- tokens, credentials, and authorization headers remain redacted;
- Platform receives only content-free usage measurements;
- schemas are never fetched from a URL;
- unknown keywords and over-limit schemas fail closed;
- profile and workload authorization occurs before provider execution;
- DaliJob retains independent validation and does not trust provider conformance
  alone.

The principal new risks are parser complexity and resource amplification from
unions/enums. The proposed limits bound traversal, memory, and provider-request
size. Unit tests and fuzz/property tests must cover deeply nested unions,
duplicate enums, type/value mismatches, excessive strings, non-finite numbers,
and malformed bounds.

## Impact on other applications

No other application server requires a registration or code change.

The validator is shared Gateway code, so an authorized profile that already
opts into `json_schema` could submit the newly accepted keywords. This is
backward-compatible: previously valid schemas remain valid and their provider
payload is unchanged. Profiles that support only `text` or `json` cannot use
the feature. Workload grants, products, profile identifiers, models, capacity
pools, and rate limits remain unchanged.

The initial production policy change remains limited to:

- workload: `dali_job_ai`;
- product: `dalijob`;
- profiles: `dalijob.job.extract` and
  `dalijob.job.extract.repair`;
- capability: `text_generation`;
- output: `json_schema` only for these two extraction profiles.

## DaliJob impact

DaliJob will update its schema normalization to preserve the newly supported
constraints while flattening local definitions. Its full provider DTO,
evidence-reference allowlist, cleanup rules, policy assignment, and semantic
validators remain unchanged.

Temporary placeholder normalization added during the canary must not become a
general coercion layer. Once the constrained schema succeeds end to end, only
explicitly approved normalization should remain, and invalid controlled values
must continue to fail validation.

## Observability

Add content-free counters for:

- schema validation accepted/rejected;
- rejection category, using a bounded category such as size, depth, keyword,
  type, enum, union, or bound;
- provider structured-output rejection;
- product/profile/outcome usage already emitted by the Gateway.

Do not record property names, enum values, patterns, schema hashes derived from
content-bearing schemas, prompts, or generated output.

## Test plan

Gateway tests:

- preserve all previously accepted schemas;
- accept valid enums, constants, nullable unions, patterns, and bounds;
- reject root unions, unsupported keywords, external references, invalid
  type/value combinations, and every limit boundary;
- prove rejected schemas do not call the provider;
- verify the OpenAI adapter forwards the validated schema unchanged;
- verify logs and metrics remain content-free;
- regenerate and check the OpenAPI contract if the public schema changes.

DaliJob tests:

- transform the current `JobExtractionProviderResponse` schema into the new
  Gateway subset without losing enums, nullable unions, constants, patterns, or
  bounds;
- reject cyclic or unsupported local schemas;
- retain local validation for evidence references and semantic rules;
- exercise both initial extraction and the one bounded repair attempt.

US3 acceptance:

1. Keep `DALIJOB_CRAWLER_PROFILE_ENABLED=false`.
2. Deploy Gateway validation with no policy changes and verify readiness.
3. Run synthetic accepted/rejected schema calls under `dali_job_ai`.
4. Deploy the DaliJob schema transformation.
5. Run one Amazon publication capped at five records.
6. Require five profiles created, five authenticated publications, zero
   failures, no silent fallback, and content-free logs.
7. Re-enable scheduled profiling at the existing global limit of five.
8. Observe at least the next scheduled run before widening any limit or source.

## Rollback

Rollback is independent and bounded:

1. Disable `DALIJOB_CRAWLER_PROFILE_ENABLED` immediately.
2. Revert the two DaliJob profile registrations to their last known-good
   `json` output policy if structured provider calls are failing.
3. Roll DaliJob back to the JSON adapter release if necessary.
4. Roll Gateway back to the prior validator release or policy generation.

Existing valid Job Profiles are immutable and remain usable. Failed reviews
remain unpublished and eligible for a later controlled retry. No database
migration is required.

## Alternatives considered

### Keep JSON mode and rely on DaliJob validation

This path has produced valid profiles, but invalid generations consume repair
capacity and can still fail publication. It does not provide the conformance
guarantee required by the approved rollout contract.

### Coerce every invalid generated value in DaliJob

Rejected. Broad coercion can silently change matching semantics and weaken the
evidence-backed profile contract. Narrow, explicit missing-value normalization
may remain, but controlled vocabularies must be enforced rather than guessed.

### Add DaliJob-specific validation to Gateway

Rejected. Product models and semantics belong to DaliJob. Gateway should expose
a generic, bounded transport capability.

### Accept arbitrary JSON Schema

Rejected. It increases parser, resource, compatibility, and data-governance
risk without a demonstrated product requirement.

## Approval and completion criteria

Implementation may proceed after Gateway and DaliJob owners approve this
bounded subset and production rollback plan. The change is complete only when
the code, tests, OpenAPI check, synthetic Gateway calls, and five-record US3
publication all pass and scheduled profiling is safely re-enabled.
