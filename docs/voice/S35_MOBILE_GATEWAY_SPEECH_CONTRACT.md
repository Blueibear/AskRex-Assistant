# S35 Mobile Gateway Speech Contract (canonical)

Status: **authoritative**. This file did not previously exist in this
repository; it is created here to end conflicting mailbox guidance about the
`/mobile/voice/upload` and `/mobile/tts/playback` wire shapes.

## Source of truth

The wire shape below was derived by direct inspection of the executable
source at the current backend HEAD, not from prior mailbox summaries:

- Route handlers: `rex/mobile_api/routes/voice.py`
  (`_handle_voice_upload`, `_add_optional_tts`, `_handle_tts_playback`)
- TTS/STT adapters: `rex/mobile_api/voice.py` (`TextToSpeechAdapter.mime_type`)
- Route tests: `tests/mobile_api/test_voice_upload.py`,
  `tests/mobile_api/test_tts.py`
- Shared cross-repo fixture: `tests/mobile_api/contract_vectors.json`
  (`http.voice_response`, `http.tts_response`)
- Fixture hygiene test: `tests/mobile_api/test_contract_vectors.py::TestVectorHygiene::test_every_wire_key_is_snake_case`,
  which fails the whole suite the moment any camelCase key appears anywhere
  in `contract_vectors.json`.

All five of those sources already agree with each other. There is exactly
one wire contract, and it is **snake_case**.

## Superseded guidance

Prior mailbox messages describing a camelCase upload shape (`toolUsed`,
optional `ttsBase64`) with an inline `audio_url` data-URI for playback do
not correspond to anything in this repository's source tree at the current
HEAD:

- No route, serializer, or test in `rex/mobile_api/` or `tests/mobile_api/`
  emits or accepts `toolUsed`, `ttsBase64`, or `audio_url`.
- `test_every_wire_key_is_snake_case` would fail immediately if any of those
  keys were introduced into `contract_vectors.json`.
- `docs/voice/SPEECH_ROUTER_VOICESTUDIO.md` and any VoiceStudio provider
  code referenced by related mailbox threads (redirect-origin hardening,
  provider fallback) also do not exist anywhere in this repository.

That camelCase/data-URI guidance is **superseded and must not be
implemented**. Mobile and backend should both treat this document, the
route source, the route tests, and `contract_vectors.json` as the sole
agreeing description of the wire contract, and disregard any prior message
that described a different shape.

## `POST /mobile/voice/upload`

Multipart form (`audio` file part + `mode=mobile_voice` + optional
`client_context` JSON) → transcribe → canonical Assistant reply → optional
inline TTS of the reply. Response body (`rex/mobile_api/routes/voice.py:176-184`):

```json
{
  "request_id": "<uuid>",
  "transcript": "<string>",
  "response": "<string>",
  "status": "completed",
  "tool_used": null,
  "tts_base64": "<base64-audio>",
  "tts_mime_type": "audio/wav"
}
```

- `tool_used`, `tts_base64`, and `tts_mime_type` are all snake_case.
  `tts_base64`/`tts_mime_type` are **optional**: they are present only when
  a TTS engine is configured and available (`_add_optional_tts`); when TTS
  is unavailable the response contains only `request_id`, `transcript`,
  `response`, `status`, and `tool_used`.
- `status` for a conversational voice turn is always `"completed"`; it is
  never upgraded to `"verified"` (`VOI-019`, `test_status_never_upgraded`).

### Is the inline `tts_base64` directly playable?

Yes. `tts_base64` is the base64 encoding of the *exact same synthesized
container bytes* that `/mobile/tts/playback` returns as `audio_base64` —
both come from `TextToSpeechAdapter.synthesize()`. It is not a data URI and
carries no `data:` prefix; a client must base64-decode it and treat the
result as a complete, self-contained audio file (WAV or MP3 container,
never a raw/headerless PCM stream) using the sibling `tts_mime_type` field.

### How is `tts_mime_type` determined?

Server-side only, from the configured TTS provider
(`TextToSpeechAdapter.mime_type()`, `rex/mobile_api/voice.py:261-262`):

- `"audio/mpeg"` when the configured provider is `edge-tts`.
- `"audio/wav"` for every other supported provider (`xtts`, `pyttsx3`).

The client must always read the MIME type from this field — it must never
be inferred from the `voice`/`requested_voice` identifiers, the request,
or a hardcoded assumption.

## `POST /mobile/tts/playback`

JSON `{"text": "...", "voice": "<optional>"}` → existing configured TTS →
authenticated JSON base64 audio. Response body
(`rex/mobile_api/routes/voice.py:217-228`):

```json
{
  "request_id": "<uuid>",
  "audio_base64": "<base64-audio>",
  "mime_type": "audio/wav",
  "voice": "<resolved-voice-id>",
  "requested_voice": "default"
}
```

Same rules as above: `audio_base64` is a complete container (not a data
URI, not raw PCM), and `mime_type` is the authoritative, explicit MIME
label — determined the same way as `tts_mime_type` above.

## Fixture audio vector

`tests/mobile_api/contract_vectors.json`'s `http.voice_response.tts_base64`
and `http.tts_response.audio_base64` previously held the literal placeholder
text `"<base64-audio>"`, which is not valid base64 and does not decode to
audio. Both fields now hold the base64 encoding of a minimal, genuinely
decodable 8 kHz mono 8-bit PCM WAV file (48 bytes: a standard 44-byte
`RIFF`/`WAVE`/`fmt `/`data` header plus 4 bytes of silent sample data).
`tts_mime_type`/`mime_type` in the fixture were updated to `"audio/wav"` to
stay truthful to the embedded bytes. `voice`/`requested_voice` example
values are illustrative identifiers only and are never used to infer MIME
type (see above).

`tests/mobile_api/test_contract_vectors.py::TestVectorHygiene::test_fixture_audio_vectors_are_valid_decodable_wav`
decodes both fields with `base64.b64decode` and opens the result with the
standard library `wave` module to prove they are structurally valid,
non-empty, decodable audio, not placeholder text.

## Revision

This document is authoritative as of backend HEAD commit
`4988bf383068a9113d41d1b6e633e8efc906c395` (detached `HEAD`, base branch
`lead/production-readiness-live-test` per the working tree at the time this
file was written). `contract_vectors.json`'s `contract_version` field was
bumped to `"2026-09-16.323.5"` alongside this change; that version string,
not any mailbox-quoted hash, is the way to confirm a copy is current.

A SHA-256 of the exact `tests/mobile_api/contract_vectors.json` bytes was
not computed by this change: this session has no shell/code-execution
access, and several previously mailbox-quoted "SHA-256" values (e.g.
65–66 hex characters) are not valid SHA-256 digests (32 bytes / 64 hex
characters) and must not be trusted. The deterministic supervisor test run
should compute it, e.g.:

```
python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('tests/mobile_api/contract_vectors.json').read_bytes()).hexdigest())"
```
