# aws-us2 Classroom and Dali Chat Gateway Activation

Status: implementation-ready; external Platform and AWS values pending  
Policy generation: `aws-us2-classroom-chat-v3`

## Supported product services

The reviewed policy in `deploy/aws-us2/two-product.env.example` grants:

- `dali_classroom_server` only the `classroom` product and its released batch
  translation, summary, transcription, and realtime-transcription profiles;
  the unreleased realtime-translation profile is not part of this generation;
  and
- `dali_chat_server` only the `dali_chat` product and its text, recorded-audio,
  streaming transcription, native streaming speech-to-speech interpretation,
  image, and video profiles.

Neither product can select the other product's profiles. Mobile clients call
their product server and never call the Gateway directly. Product prompts,
workflow, account authorization, plan/tier rules, and durable content remain in
the product server.

## Workload credential contract

Platform workload JWTs must use:

- audience `dali-ai-gateway`;
- scope `ai_gateway:invoke`;
- `principal_type=workload` and `token_use=access`;
- `sub` equal to `workload_id`;
- maximum lifetime five minutes; and
- RS256 with a recognized Platform JWKS `kid`.

Classroom configures `CLASSROOM_AI_GATEWAY_WORKLOAD_TOKEN_FILE`. Dali Chat
configures `DALI_CHAT_GATEWAY_WORKLOAD_TOKEN_FILE`. Each file contains only the
compact JWT and may be atomically replaced by the Platform credential agent.
The servers read it for every new HTTP request or realtime connection. Configure
the token file or the legacy service token, never both.

## Durable content-free usage

The Gateway includes an AWS SQS Standard adapter. It sends the canonical
`dali.ai.usage.v1` envelope and safe `event_id`/schema-version attributes. It
does not use FIFO-only fields. The product-owned relay must treat delivery as
at-least-once, deduplicate by `event_id`, and only then associate usage with an
account outside the Gateway.

AWS credentials come from the instance/task IAM role. They must not be placed
in the Gateway environment file or Git.

## Required operator inputs

Before activation, provide in the protected runtime environment:

1. the stable HTTPS Platform issuer and JWKS URL;
2. the actual SQS Standard queue URL and AWS region for aws-us2;
3. an IAM role limited to sending messages to that queue;
4. Platform-issued tokens or a token-file rotation agent for both workload IDs;
5. legacy service tokens only for workloads participating in the shadow/cutover
   window; and
6. enabled provider credentials/routes for the selected Dali Chat canary.

Do not enable the reviewed generation with blank issuer, JWKS, queue, or region
values. Do not remove a legacy workload until its Platform-token shadow check
and rollback test pass.

## Verification sequence

1. Validate the complete environment by constructing Gateway settings without
   printing secrets.
2. Start the Gateway and require readiness with Platform authentication.
3. Call one Classroom batch profile and one bounded Dali Chat text profile.
4. Verify cross-product profile calls return forbidden.
5. Rotate each workload token file and repeat without restarting a product
   server.
6. Confirm SQS receives content-free envelopes and relay replay is idempotent by
   `event_id`.
7. Exercise the caller-specific legacy rollback before removing legacy access.

## Deployment record: 2026-08-31

- Gateway release `20260831T201534Z` is active on aws-us2 and ready on
  `127.0.0.1:5040`.
- Policy generation `aws-us2-classroom-chat-v1` is active with independent
  legacy credentials for `dali_classroom_server` and `dali_chat_server`.
- Classroom's released Gemini batch translation, summary, transcription, and
  realtime-transcription paths remain enabled. Classroom required no server
  redeployment and remained publicly ready throughout the rollout.
- A Classroom text smoke request succeeded; a Classroom credential attempting
  a Dali Chat profile was denied; a Dali Chat Gemini text smoke request
  succeeded.
- Dali Chat Server release `20260831T202206Z` is active and enabled on aws-us2
  at `127.0.0.1:5050`; its authenticated product-server-to-Gateway Gemini text
  smoke request succeeded.
- The deployed Chat capability catalog enables only the healthy Gemini text,
  dedicated transcription, translation, speech, image, and video profiles.
  OpenAI and Ollama are reported as unavailable and rejected before provider
  work until their aws-us2 routes pass a separate health/smoke check.
- Platform workload JWT and SQS delivery remain disabled for this legacy-token
  rollout. No AWS queue, IAM role, issuer, or JWKS endpoint was provisioned.
- After the DNS owner pointed `chat.dalifin.com` to aws-us2, a dedicated Apache
  reverse proxy and Let's Encrypt certificate were activated. HTTP redirects to
  HTTPS, unauthenticated capability access returns 401, authenticated capability
  access returns 200, and a public Gemini chat smoke request returns 200.
- The certificate expires on 2026-11-29 and is covered by the active Certbot
  renewal timer. The Apache access format intentionally excludes query strings.
- Public server availability does not distribute the protected shared demo
  client token or constitute a general-user authentication design. Client
  configuration/release remains a separate product rollout step.
- An Android debug build configured for the public endpoint was installed on a
  physical phone and tested over its cellular connection. Capability discovery
  selected Gemini, a text request completed successfully, and the UI displayed
  the Gemini provider, concrete model, and content-free input/output usage. The
  server access log recorded `POST /v1/chat` with status 200 and no query or
  submitted content. No device crash, TLS, socket, or server warning was found.
- Gateway release `20260831T214043Z` and Chat Server release
  `20260831T213259Z` add the Dali Chat capability-demo slice. Public smokes
  verified Gemini 3.5 Transcribe with an exact controlled-phrase match, Gemini
  3.5 Flash Lite translation, and Gemini 3.1 Flash TTS with valid transient WAV
  output. Classroom profiles and grants were not changed.
- Gateway release `20260831T225108Z` and Chat Server release
  `20260831T224005Z` add Gemini streaming transcription and native streaming
  speech-to-speech interpretation for Dali Chat. Apache proxies the two product
  WebSocket routes, policy generation `aws-us2-classroom-chat-v3` isolates the
  Chat profiles from Classroom, and the physical Android phone established and
  cleanly closed both public WebSocket paths using a Platform login token.
- Gateway release `20260831T225757Z` adds transient 16 kHz to 24 kHz PCM conversion
  for the OpenAI realtime-translation profile. The profile remains disabled in
  the Chat capability catalog because the configured aws-us2 OpenAI credential
  returns `invalid_api_key`; enable it only after a successful credential and
  model-access probe.
- Gateway release `20260831T230600Z` correctly extracts provider API keys from
  the existing JSON-object rows in `secret.key_store`, while retaining plain and
  JSON-string compatibility. A content-free aws-us2 probe resolved the same
  OpenAI key fingerprint used by Interprete and confirmed the provider healthy.
  Dali Chat's separate deployment capability allowlist remains Gemini-only until
  its OpenAI profiles are explicitly enabled and smoke-tested.
- Gateway release `20260831T232158Z` activates policy generation
  `aws-us2-classroom-chat-v4`, adds independently allowlisted GPT-5.6 Sol,
  Terra, and Luna chat profiles, aligns OpenAI batch transcription with
  `gpt-4o-mini-transcribe`, and omits the unsupported `temperature` field for
  GPT-5.6 Chat Completions requests. All three model-access probes passed and a
  bounded GPT-5.6 Luna request succeeded through Dali Chat Server.
- Dali Chat Server release `20260831T232159Z` exposes 18 available model/function
  combinations with a fixed 2026-08-31 price snapshot. OpenAI and Gemini choices
  are enabled for chat, batch/stream transcription, interpretation, translation,
  TTS, and image analysis; Gemini also remains enabled for video analysis.
