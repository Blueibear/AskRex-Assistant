# S35 canonical mobile-gateway speech contract

Status: canonical for S35 (provider-neutral SpeechRouter + VoiceStudio)
Owner: backend
Related: `docs/voice/SPEECH_ROUTER_VOICESTUDIO.md`, STORY-S35-SPEECH-ROUTER
Canonical fixtures: `tests/mobile_api/contract_vectors.json`
(identical copy in the AskRex mobile repo)
Canonical spec section: `docs/mobile/MOBILE_API_MASTER_SPEC.md` §6.4

## Summary for mobile

**S35 introduces no mobile wire change.** Both speech endpoints keep their
current paths, request shapes, response shapes, status vocabulary, and error
envelope. Provider selection, policy (including Local Only), voice-alias
resolution, ordered fallback, and VoiceStudio integration are entirely
server-side. The app stays provider-neutral: no provider field, no provider
selection UI, no capability negotiation, and no direct VoiceStudio call.

Mobile needs **no diff** to remain compatible. The remainder of this document
is the exact contract the gateway guarantees while S35 is enabled.

## Endpoints (unchanged)

```text
POST /mobile/voice/upload     multipart/form-data
POST /mobile/tts/playback     application/json
```

Both require Bearer authentication and the existing route scopes
(`voice.upload`, `tts.playback`). Authentication, principal revalidation, TLS,
pairing, rate limiting, size/duration limits, idempotency, and audit happen
before any speech work. No speech provider can influence identity,
authorization, confirmation, action success, or verification state.

## `POST /mobile/voice/upload`

Request (unchanged multipart fields):

- `audio` — exactly one file part; container sniffed from bytes
  (M4A/MP4, AAC, MP3, WAV). Filename and declared MIME type are not trusted.
- `mode` — must be `mobile_voice`.
- `client_context` — optional JSON **string** encoding a JSON object, e.g.
  `{"device":"iphone","response_preference":"brief"}`.
- Identity-bearing fields (`user_id`, `role`, `permissions`, `risk`,
  `approval`, `biometric`) remain rejected with `400 BAD_REQUEST`.
- Limits remain 15 MiB and 60 seconds (server config).

Response `200` (unchanged, `snake_case`):

```json
{
  "request_id": "9a2b1c3d-4444-4444-8444-444444444444",
  "transcript": "Turn off the downstairs lights",
  "response": "The downstairs lights are off.",
  "status": "completed",
  "tool_used": null,
  "tts_base64": "<base64-audio>",
  "tts_mime_type": "audio/mpeg"
}
```

- `status` is the canonical chat status vocabulary (`completed`); it is not a
  speech-provider state.
- `tts_base64`/`tts_mime_type` remain **optional** and are omitted when TTS is
  unavailable; the text reply is still returned. There is no `tts_url` today.
- `tts_mime_type` always describes the provider that actually synthesized the
  returned bytes. With S35 fallback the synthesizing provider may differ from
  the initially selected one, so clients must use this field rather than
  assuming a fixed MIME type.

## `POST /mobile/tts/playback`

Request (unchanged):

```json
{
  "text": "The downstairs lights are off.",
  "voice": "default"
}
```

- `text` is required, trimmed, and bounded (2,000 characters).
- `voice` is optional. Accepted values: omitted/`null`/`"default"`, an AskRex
  voice alias (`majel`, `james`, `cole`), or a concrete voice ID the server
  exposes. Any other field is rejected with `400 BAD_REQUEST`.
- The app must not send a provider name and must not attempt provider
  selection; aliases are AskRex-owned and resolved server-side.

Response `200` (unchanged):

```json
{
  "request_id": "9a2b1c3d-4444-4444-8444-444444444444",
  "audio_base64": "<base64-audio>",
  "mime_type": "audio/mpeg",
  "voice": "en-US-AriaNeural",
  "requested_voice": "default"
}
```

- `requested_voice` echoes what the client asked for (`"default"` when
  omitted).
- `voice` and `mime_type` describe the provider that actually produced
  `audio_base64`. The requested alias is resolved independently for each
  attempted provider, so a fallback never returns another provider's voice ID
  or a mislabeled MIME type.
- The response is always JSON with base64 audio. No binary body, no streaming
  body, and no URL form is introduced by S35.

## Voice alias semantics

- `majel`, `james`, and `cole` are AskRex-owned identities, not provider IDs.
  The canonical mapping lives in `rex/speech/aliases.py` plus per-deployment
  VoiceStudio overrides in `speech.voicestudio_voice_aliases`.
- An alias that the selected provider cannot serve is retried against the next
  policy-permitted provider. Only when **no** permitted provider can serve the
  requested voice does the request fail with `400 BAD_REQUEST`
  (`"The requested voice is not available."`). There is never a silent
  substitution of a different voice.
- Aliases are optional for mobile. Sending no `voice` keeps today's behavior.

## Error envelope (unchanged)

```json
{
  "error": {
    "code": "BACKEND_UNAVAILABLE",
    "message": "Speech processing is not available on this server.",
    "retryable": false,
    "request_id": "<request-id>"
  }
}
```

Speech-relevant codes and statuses:

| Situation | HTTP | `code` | `retryable` |
|---|---|---|---|
| Unsupported/undecodable/empty audio | 415 | `INVALID_MEDIA` | false |
| Audio too large / too long / TTS audio too large | 413 | `PAYLOAD_TOO_LARGE` | false |
| Bad field, unknown field, or unknown voice | 400 | `BAD_REQUEST` | false |
| Missing/invalid token or scope | 401 / 403 | auth codes | false |
| Rate limited | 429 | `RATE_LIMITED` | true |
| Provider timed out (all permitted providers) | 503 | `BACKEND_UNAVAILABLE` | true |
| No permitted provider available (includes Local Only exhaustion) | 503 | `BACKEND_UNAVAILABLE` | false |

Policy denial is never disguised as success and never silently escalates to a
cloud provider: `Local Only` exhaustion is a truthful 503, not a cloud
fallback. Error messages stay generic and never name the failing provider,
endpoint, transcript, or credentials.

## Server-side behavior mobile can rely on

- Provider selection is independent for STT and TTS, and independent of LLM
  routing.
- Fallback order is policy-bounded; a provider excluded by policy is never
  used for recovery.
- `speech.enabled` is `false` by default, which keeps the pre-existing native
  Whisper/XTTS/edge-tts/pyttsx3 adapters in place. That is the rollback path
  and is also wire-identical.
- Undecodable audio is client truth: it stays a 415 and is never retried
  against another provider or relabeled as a backend outage.

## If this ever changes

Any future change to these shapes (for example streaming TTS, a protected
audio artifact URL, or capability flags for speech) will be sent to
`mailbox/mobile/` with an updated version of this document and updated
`tests/mobile_api/contract_vectors.json` **before** mobile is asked to depend
on it.
