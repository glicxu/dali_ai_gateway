# Dali AI Gateway

`dali_ai_gateway` is the private AI execution boundary for Dali product
services. It builds on the useful multi-vendor direction in `dali_ai` while
providing a new privacy-safe, authenticated, realtime-capable service contract.

The Gateway is a data plane, not a product workflow engine:

```text
Classroom Server ----+
Interprete Server ---+--> dali_ai_gateway --> OpenAI / Gemini / Ollama
Other Dali services -+           |
                                 +--> content-free usage measurements
```

`dali_chat_server` is an optional, disabled-by-default demo product service with
its own admission bucket. Enabling it requires an explicit complete policy
generation and service credential. Its Flutter client never receives Gateway
credentials and never calls this service directly.

The reviewed aws-us2 two-product generation is
`deploy/aws-us2/two-product.env.example`; it enables only Classroom and Dali
Chat and requires the external Platform/AWS values listed in
`docs/aws_us2_classroom_chat_activation.md` before activation.

Product services retain prompts, terminology, state, durable content, and UX
policy. The Gateway normalizes provider transport, model-profile routing,
admission, timeouts, errors, and measurements. It never stores request or
response content and never logs it.

The proposed architecture for evolving the restricted Classroom pilot into a
multi-product data plane, while keeping prompts and product workflows outside
the Gateway, is documented in
[`docs/multi_product_gateway_design.md`](docs/multi_product_gateway_design.md).
The staged G0-G7 implementation work, verification matrix, consumer manifest,
and rollout/rollback gates are tracked in
[`docs/multi_product_gateway_implementation_plan.md`](docs/multi_product_gateway_implementation_plan.md).
The proposed boundary for using Gateway as an inference backend for agent
runtimes, without making it an MCP host/client or tool executor, is documented
in
[`docs/agent_runtime_mcp_boundary_design.md`](docs/agent_runtime_mcp_boundary_design.md).

## First local slice

- authenticated `POST /ai/v1/text/generations`;
- authenticated `POST /ai/v1/audio/transcriptions`;
- authenticated `POST /ai/v1/audio/speech` with binary audio output;
- authenticated `POST /ai/v1/media/analyses` for transient image/video input;
- authenticated `WS /ai/v1/realtime/transcriptions`;
- authenticated `WS /ai/v1/realtime/translations`;
- OpenAI text, batch-transcription, realtime-transcription, and
  realtime-translation adapters;
- Gemini text, dedicated batch/realtime transcription, speech-synthesis, and
  realtime-translation adapters;
- OpenAI speech-synthesis adapter;
- Ollama text adapter;
- OpenAI image-analysis and Gemini image/video-analysis adapters;
- strict, versioned transport-profile policy with exact workload grants;
- profile-aware readiness and per-caller concurrency limits;
- OpenAPI 3.1 export and contract tests.

The initial profile catalog is development policy, not a production model or
price approval. In production, provider credentials are resolved from the
shared `secret.key_store` table through a narrowly scoped database identity.
Credential rows may contain either a plain secret or the existing provider JSON
object (`OPENAI_API_KEY` or `GEMINI_API_KEY`); JSON-string-wrapped forms are
decoded without logging their contents. Local development may instead use
provider-specific environment variables.
Service tokens remain environment-managed. Each configured legacy caller must
have exactly one enabled workload grant. With no provider credentials, or with
a missing provider required by an enabled deployment profile, the service
remains live but is not ready for provider work. Ollama is disabled unless
explicitly enabled. Provider reachability is checked by bounded, content-free
background probes; readiness reads only the cached health state. During legacy
token rotation, a caller may temporarily configure a JSON array containing the
current and previous token, limited to two unique values.

The target Platform workload-token verifier and JWKS cache are implemented but
disabled by default. Activation requires explicit issuer, single audience,
required scope, workload allowlist, JWKS URL, TTL/skew policy, and rollout
configuration. Legacy acceptance is independently allowlisted per workload.
The internal usage envelope and idempotent delivery interface are not billing
authority and have no production sink until the Platform MS2/MS3 contracts and
durable delivery owner are approved.

Batch provider-route circuits are available for isolated deployment testing
but disabled by default. When enabled, exact disabled/open routes fail before
provider work, failures are bounded per route, and successful calls close a
degraded circuit. This process-local implementation is not a substitute for
the shared circuit state required by multi-instance production readiness.

## Local setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 5040
```

Do not place real credentials in `.env.example`, tests, or Git.

## Provider support versus product choice

An adapter being present does not select that provider for an application.
Products request abstract, server-controlled profiles. Deployment configuration
maps those profiles to provider models. Classroom's Gemini mapping is an
explicit, reversible pilot decision recorded in Classroom ADR 0004; it was not
implied merely by adding the Gemini adapter.

Additional quality candidates use isolated `shared.evaluation.*` profiles
enabled only for an evaluator service identity. See
[evaluation/README.md](evaluation/README.md).

## Relationship to existing `dali_ai`

`dali_ai` remains the existing finance/Bible/research application. This
repository does not import it at runtime. Vendor-neutral behavior is
reimplemented behind typed contracts so existing applications are not changed
or exposed to realtime Classroom traffic.
