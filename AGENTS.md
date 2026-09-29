# Dali AI Gateway Repository Instructions

Phase 2 consolidation: [reference reconciliation](docs/phase2_reference_reconciliation.md)
records 2026-09-15 Classroom/Audio deployments, policy drift, capacity evidence
limits and rollback boundaries. Do not use historical sample policy as live state
or infer a certified Host reserve. No Gateway configuration was changed.

## Mission

Provide a private, stateless, vendor-neutral AI data plane for Dali product
services, including low-latency realtime speech use cases.

## Boundaries

- Use Python 3.12, FastAPI, Pydantic 2, httpx, websockets, and pytest.
- Product services own prompts, workflows, user authorization, and durable
  content. The Gateway owns provider transport, routing, admission, and safe
  usage measurements.
- Never store or log audio, transcripts, translations, prompts, summaries,
  provider payloads, bearer tokens, API keys, or user identifiers.
- Accept traffic only from authenticated Dali services. Mobile and browser
  clients never call this service directly.
- Keep `dali_platform` content-free. Emit only content-free measurements to it.
- Provider credentials come only from environment-managed secrets or an
  approved secret resolver; never from Git.
- Do not import `app_server`, `dali_ai`, Classroom, or Host runtime modules.
- Host and Classroom capacity must be independently limitable. New product
  traffic must never consume a documented Host reserve.
- Realtime audio is transient process memory and must be discarded after it is
  forwarded or the connection closes.

## Quality gate

P3-01 (2026-09-15): generic bounded strict-schema text output is implemented
locally. See `docs/structured_output.md` for its limited subset and explicit
OpenAI profile opt-in. No live DaliJob grants/profile/capacity changes are made.
181 tests, compilation and OpenAPI freshness pass. Product schemas remain owned
by product services; do not add DaliJob-specific models to Gateway.

```powershell
python -m compileall -q app tests
python -m pytest -q
python -m scripts.export_openapi --check
```
