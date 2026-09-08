# us3 Audio Test Deployment

Deployed: 2026-09-08 UTC  
Gateway release: `audio-spike-20260908-v2-wav`  
Chat release: `audio-spike-20260908-v1`  
Host: SSH alias `us3` (`10.0.0.168`, x86_64, 64 logical CPUs)

## Endpoints and Isolation

| Component | Address / service |
| --- | --- |
| Chat test API | `https://server.dalifin.com/audio-test/v1/` |
| Chat health | `https://server.dalifin.com/audio-test/health/live` |
| Private test Gateway | `127.0.0.1:15040`, `dali-audio-test-gateway.service` |
| Private test Chat backend | `127.0.0.1:15050`, `dali-audio-test-chat.service` |
| Platform | Existing `https://server.dalifin.com/platform/v1/` |

The release lives under `/data/dali/test/services/dali-audio-gateway` and
`/data/dali/test/services/dali-audio-chat`, each with a `current` symlink to
its release above. Gateway v1 is retained for rollback. Separate service users, CPU/memory limits,
and one concurrent Chat inference slot isolate the test processes. Only the
two speech profiles are granted in policy generation `us3-audio-test-v1`.
Provider-side quotas may still be shared; these results are not a load test.

The existing Gateway on port 5040, application on port 5050, Host, Classroom,
Platform, Scribe, and Interpreter services were not replaced or restarted.
Apache was gracefully reloaded after configuration validation.

## Authentication and Secrets

Chat requires a Platform RS256 access token with audience `dali-chat` and
scope `chat:access`. Its issuer is `https://server.dalifin.com`; server-side
JWKS verification uses the existing local Platform service on port 5030.
There is no shared client-token bypass in this deployment.

The test Gateway accepts only its generated Chat service credential. Provider
keys were resolved through aws-us2's existing approved secret resolver and
transferred over SSH into the protected us3 test environment without local
secret files or log output. us3's old Gateway database credential was rejected
on a fresh connection, so it was not retained as the new test service's source.
No existing us2/us3 service credentials were rotated or overwritten.

Environment files, managed on the host:

- `/data/dali/test/config/secrets/dali_audio_gateway.env`
- `/data/dali/test/config/secrets/dali_audio_chat.env`

These files are root-owned, mode 0640, with the corresponding test service
group. Rotate the test provider environment values through approved secret
provisioning when required. No credentials belong in the app or repository.
This test release does not enable authoritative usage delivery or billing.

## Profiles

| Gateway profile | Model | Dali voice aliases |
| --- | --- | --- |
| `dali_chat.speech.openai` | `gpt-4o-mini-tts` | `narrator_main`, `narrator_warm` |
| `dali_chat.speech.gemini` | `gemini-3.1-flash-tts-preview` | `narrator_main`, `narrator_bright` |

Chat discovers aliases and configuration IDs through the Gateway. Delivery
instructions are best effort. Requests use the ordinary deployed entrypoints;
the local synthetic-tone fixture and local authentication entrypoint are not
used by these services or the phone build.

## Phone Build

An updated debug build was installed successfully on the attached Samsung
Galaxy S10+ using the standard `lib/main.dart` entrypoint. Open Dali Chat,
sign in to Platform, and choose **TTS**. The test catalog enables speech only.
USB forwarding is not needed; the phone must reach the us3 HTTPS hostname.

Rebuild from the Dali Chat checkout:

```powershell
flutter build apk --debug --target lib/main.dart --dart-define=DALI_CHAT_SERVER_URL=https://server.dalifin.com/audio-test --dart-define=DALI_PLATFORM_BASE_URL=https://server.dalifin.com
```

## Verification

- On us3: 161 Gateway tests (v2), 17 Chat backend tests, compilation, and the
  Gateway OpenAPI check passed.
- The first Gateway test run encountered an intermittent realtime disconnect
  cancellation; its isolated rerun passed. The release then aligned AnyIO with
  the locally validated 4.14.2 version using `constraints.txt`, and the complete
  gate passed. The race recurred while staging v2; the disconnect test now
  waits for provider closure and usage delivery before TestClient teardown
  cancels the ASGI task. The full 161-test gate passed. No tests were excluded.
- Gateway readiness reports both providers healthy and zero active leases
  after smoke completion.
- HTTPS health returns 200; unauthenticated catalog access returns 401.
- The attached phone independently verified those HTTPS results over cellular
  data, including the `X-Dali-Audio-Environment: us3-test` response marker.
  The phone does not require LAN access or USB forwarding for this endpoint.
- The deployed Chat GatewayClient exercised authenticated discovery, voice
  resolution, configuration mismatch rejection, and real provider synthesis.
- OpenAI: 237,644 bytes, approximately 4.95 seconds of audio, 2.27 seconds to
  receive the response. Gemini: 238,124 bytes, approximately 4.96 seconds of
  audio, 3.41 seconds to receive the response. These are individual trials.
  WAV duration was computed from actual PCM bytes because a streaming header
  may contain an unknown-length sentinel. No audio was saved.
- Existing us3 services remained active, and the original Gateway remained
  ready after activation.

### Android playback fix (v2)

The phone received OpenAI audio, but Android MediaPlayer rejected the WAV
with error `1, -2147483648`. Media volume was enabled. A native phone probe
confirmed that identical PCM samples played with complete RIFF/data sizes
and failed with OpenAI's `0xffffffff` streaming-size headers.

The OpenAI adapter now rebuilds the completed PCM WAV container in memory
with actual sizes, preserving the samples. Four regression tests cover
normalization, malformed frames, other formats, and adapter integration.

Using the phone's existing signed-in Platform session, the native probe
called the deployed Chat API and observed playback completion for both
OpenAI and Gemini. This checks device decoding and completion; subjective
voice quality was not evaluated. No authentication bypass was used.
The normal `lib/main.dart` app was restored after the probe.

The reusable probe is `integration_test/audio_playback_probe.dart` in the
Dali Chat checkout. Run with `flutter run --no-resident` targeting that file,
the two URL defines above, and `--dart-define=AUDIO_PROBE_LIVE=true` for live
providers. Restore the normal entrypoint afterward. The synthetic malformed
WAV case intentionally reports FAIL on the affected Android player.

## Operations and Rollback

The Apache include is `/etc/apache2/dali-audio-test-proxy.conf`, referenced
inside `/etc/apache2/sites-enabled/server_dalifin.conf` before its catch-all.
The pre-deployment backup is
`/etc/apache2/sites-enabled/server_dalifin.conf.before-audio-test-20260908`.
The enabled site is a regular file; editing only sites-available would not
change the active configuration on this machine.

To withdraw the test environment, remove only the Audio test Include line,
run `apache2ctl configtest`, and gracefully reload Apache. Then disable and
stop `dali-audio-test-chat` and `dali-audio-test-gateway`. Keep the release and
protected configuration for rollback review. Do not restore the entire site
backup over later unrelated routing changes.

For upgrades, stage a new immutable release, run both quality gates, preserve
the previous symlink targets, switch the two test services, and repeat readiness
and authenticated speech checks. Apply `constraints.txt` when installing this
test release's Python dependencies.
