# Dali Audio Technical Spike: Gateway and Chat

Date: 2026-09-07  
Scope: A stateless, single-section narration trial using the existing Dali Chat
app and backend. Follows [Dali Audio Architecture](dali_audio_architecture.md).

Hosted testing is now deployed on **us3**. See the
[deployment record and phone configuration](../deploy/us3/README.md) for the
HTTPS endpoint, live-provider results, authentication, and rollback details.

## Implemented Slice

```text
Chat Flutter app -> Chat backend -> AI Gateway -> speech provider
```

The Gateway exposes authenticated, workload-scoped speech configuration
discovery at `GET /ai/v1/audio/speech/capabilities?product=...&profile=...`.
It returns the resolved provider/model, configured voice aliases, input limits,
and a content-free configuration ID. Voice routes remain server configuration.
If a legacy profile has no voice routes, `voices` is null; discovery does not
invent a provider voice catalog. Chat retains its existing curated choices for
that legacy case.

`POST /ai/v1/audio/speech` accepts an optional `configuration_id`. A mismatch
returns non-retryable HTTP 409 before admission or provider execution. The
response includes `X-Dali-Speech-Configuration` from the same immutable profile
used to execute the request. Changes to the model, provider, voice mappings, or
input byte limit change the identity. Unrelated policy changes do not.
This detects configuration drift; it does not retain old configurations, pin a
provider's internal model revision, or guarantee identical sound. Callers still
record the selected voice, instructions, and profile with each product revision.

The existing instructions field transports delivery intent. Discovery labels
its semantics as best effort; no exact pacing or pronunciation guarantee is
claimed. OpenAI binary speech responses no longer report character count as
provider-reported token usage. Missing token usage remains unavailable.

Chat discovers speech configuration through its backend, exposes delivery
instructions, keeps the section editable for regeneration, and provides replay
of the latest result without another inference call. It displays received audio
bytes, content type, provider/model, available usage, and client-observed time
to receive audio. Browser CORS exposes the necessary metadata headers.
Stale configuration produces a refresh-and-review message, with no automatic
provider fallback or retry.

## Run the Isolated Local Demo

Prerequisites: the Gateway and Chat server virtual environments and the Chat
Flutter dependencies are installed using their existing READMEs. The launcher
defaults to the sibling checkout `../mobile_app/dali_chat`; override with
`--chat-root` if necessary.

From the Gateway repository:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_audio_spike --check
.\.venv\Scripts\python.exe -m scripts.run_audio_spike
```

The first command runs an end-to-end HTTP check and stops both processes.
The second keeps the two services running on loopback ports 15040 and 15050.
The launcher generates a transient service credential in process environments,
ignores deployment configuration, and enables only the selected speech profile
with one concurrent Chat inference slot. It does not alter running deployments
or their Host reserves. Ctrl+C stops both child services.

From the Chat checkout, in another terminal:

```powershell
flutter run -d chrome --web-hostname localhost --web-port 18080 --target lib/audio_spike_main.dart
```

The explicit local entrypoint uses the real Chat API and UI with the isolated
development backend. It accepts only loopback server URLs. The production
`main.dart` entrypoint retains Platform login. Do not deploy the local entrypoint
or demo launcher as a production authentication configuration.

The default provider emits a half-second synthetic tone, and the catalog labels
it as a transport test. It does not narrate the input. This makes the demo
repeatable without credentials, provider charges, or stored audio.

For a live narration trial, populate `AI_GATEWAY_OPENAI_API_KEY` or
`AI_GATEWAY_GEMINI_API_KEY` from an environment-managed secret, then run one of:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_audio_spike --provider openai
.\.venv\Scripts\python.exe -m scripts.run_audio_spike --provider gemini
```

The live choices reuse the repository's existing models/adapters; this spike
does not establish model availability, production approval, price, or quality.
No credential is placed in Flutter, a URL, a command argument, or a file.

## Demo Procedure

1. Select **TTS**. Confirm the resolved model and `narrator_main` alias.
2. Enter a short approved section. Generate and listen. In fake mode expect
   only a tone; use a live provider for listening review.
3. Change delivery instructions, for example to a warm, restrained reading.
   Generate again and compare by listening. Instructions remain best effort.
4. Edit one sentence and regenerate. The same section remains in the composer.
5. Replay the latest audio. Replay does not invoke inference.
6. Inspect time-to-receive, audio size, format, and available token measurements.
   These are trial observations, not a capacity benchmark or billing ledger.
7. If configuration changes, refresh capabilities and review the selection
   before generating again. No silent voice substitution occurs.

## Verification and Limits

Local verification completed: 157 Gateway tests, 17 Chat backend tests, four
Flutter tests, Flutter analysis, Gateway compilation/OpenAPI checks, and the
Flutter web build. The two-service synthetic check returned a 24,044-byte WAV;
the browser trial displayed the result and enabled replay while retaining the
editable section. No provider key or approved resolver was configured locally,
so live speech quality and provider capacity remain unevaluated.

Automated checks cover authenticated discovery, product scope, voice alias
resolution, input byte limits, configuration mismatch before provider calls,
configuration identity changes, control forwarding through Chat, CORS metadata,
actionable conflicts, and preservation of editable text after errors. Existing
Gateway admission tests continue to cover workload isolation.

Run the Gateway quality gate from `AGENTS.md`, Chat server `pytest`, and
Flutter `analyze` and `test`. The launcher `--check` adds the real two-service
HTTP path using the selected provider. Fake mode validates transport only.

There are no durable jobs, segment assembly, historical asset revisions,
account billing changes, or `dali_audio_server` deployment in this spike.
Regeneration is a new request for the currently edited section. The latest
result is retained for session playback; the servers do not persist content.
The local browser demo uses in-memory playback. Native audio plugins may use
platform temporary files and require a separate retention review before a
production Audio client claims memory-only playback on every platform.

Before production rollout, review complete workload policy and capacity
allocations, configure curated voice routes, verify provider controls through
listening evaluation, and integrate the durable Audio service and Platform
contracts described in the architecture document. Gateway deployment must
precede this Chat version because speech discovery is now required for an
available TTS catalog entry.
