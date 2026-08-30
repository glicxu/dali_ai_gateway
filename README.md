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

Product services retain prompts, terminology, state, durable content, and UX
policy. The Gateway normalizes provider transport, model-profile routing,
admission, timeouts, errors, and measurements. It never stores request or
response content and never logs it.

## First local slice

- authenticated `POST /ai/v1/text/generations`;
- authenticated `POST /ai/v1/audio/transcriptions`;
- authenticated `WS /ai/v1/realtime/transcriptions`;
- authenticated `WS /ai/v1/realtime/translations`;
- OpenAI text, batch-transcription, realtime-transcription, and
  realtime-translation adapters;
- Gemini text, realtime-transcription, and realtime-translation adapters;
- Ollama text adapter;
- server-owned profile catalog and per-caller concurrency limits;
- OpenAPI 3.1 export and contract tests.

The initial profile catalog is development policy, not a production model or
price approval. In production, provider credentials are resolved from the
shared `secret.key_store` table through a narrowly scoped database identity.
Local development may instead use provider-specific environment variables.
Service tokens remain environment-managed. With no provider credentials, the
service remains live but is not ready for provider work.

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
