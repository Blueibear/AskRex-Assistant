# STORY-S35 — Canonical mobile gateway speech (voice upload / TTS) wire contract

Revision: `2026-09-17.s35.2`

## Status

This document resolves the cross-instance mailbox conflict over the mobile
gateway voice-upload/TTS wire shape (camelCase + data-URI vs. snake_case +
base64/MIME). The contract below is derived directly from the executable
backend behavior in this worktree — the actual route serializers in
`rex/mobile_api/routes/voice.py`, the adapters in `rex/mobile_api/voice.py`,
the focused tests in `tests/mobile_api/test_voice_upload.py` and
`tests/mobile_api/test_tts.py`, and the cross-repo fixture
`tests/mobile_api/contract_vectors.json`. It does not adopt any wire shape
proposed only in a mailbox message. Any prior message asserting a
`camelCase`/`toolUsed`/`audio_url` data-URI shape does not match this
repository's implemented routes and is superseded by this document.

See also `docs/mobile/MOBILE_API_MASTER_SPEC.md` section 6.4, which describes
the same endpoints; this document is the authoritative field-level contract
for STORY-S35 and both must agree.

## Wire casing

All JSON field names on both endpoints are `snake_case`. There is no
camelCase field anywhere in this contract. This is enforced by
`tests/mobile_api/test_contract_vectors.py::TestVectorHygiene::test_every_wire_key_is_snake_case`.

## POST /mobile/voice/upload

Bearer authentication required (`voice.use` scope). Request is
`multipart/form-data`:

- `audio` — exactly one file part, required. Container is sniffed from
  actual byte signatures (`rex.mobile_api.voice.sniff_audio_container`),
  never from the declared filename or `Content-Type`. Supported containers:
  `wav`, `mp3`, `aac` (ADTS), `m4a`/`mp4`.
- `mode` — must equal the literal string `mobile_voice`.
- `client_context` — optional, a JSON object encoded as a form string.
- Forbidden fields (rejected with `400 BAD_REQUEST`): `user_id`, `role`,
  `permissions`, `risk`, `approval`, `biometric`.

Limits: 15 MiB (`config.max_audio_bytes`) and 60 seconds
(`config.max_audio_seconds`) of decoded audio.

Response body (`200`), exact keys, no more and no fewer
(`rex/mobile_api/routes/voice.py::_handle_voice_upload`):

```json
{
  "request_id": "9a2b1c3d-4444-4444-8444-444444444444",
  "transcript": "Turn off the downstairs lights",
  "response": "The downstairs lights are off.",
  "status": "completed",
  "tool_used": null,
  "tts_base64": "UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAACAgICA",
  "tts_mime_type": "audio/wav"
}
```

Field notes:

- `status` is always the literal string `"completed"` for this route today.
  This route only ever returns a conversational reply; it never fabricates
  `"verified"` (`tests/mobile_api/test_voice_upload.py::test_status_never_upgraded`,
  VOI-019). `attempted` / `verified` / `failed` / `needs_confirmation` are
  the general action-status vocabulary (`contract_vectors.json` →
  `statuses.action_statuses`) used elsewhere in the gateway; this route does
  not currently emit them.
- `tool_used` is always `null` for this route today — no tool-dispatch
  identifier is currently wired into the voice-upload response.
- `tts_base64` / `tts_mime_type` are **optional**: present only when TTS is
  available and the reply text is non-empty
  (`rex/mobile_api/routes/voice.py::_add_optional_tts`). When TTS is
  unavailable, both keys are simply absent from the body — there is no
  `null` placeholder and no separate audio-availability flag.
- `tts_base64` is the raw synthesized audio bytes, standard base64-encoded
  (`base64.b64encode(...).decode("ascii")`), never a `data:` URI and never
  wrapped in an `audio_url` field.
- `tts_mime_type` is computed from the actually-configured/succeeding TTS
  provider (`TextToSpeechAdapter.mime_type()`): `audio/mpeg` for `edge-tts`,
  `audio/wav` for `xtts` and `pyttsx3`. It is never a hardcoded constant
  independent of the provider that produced the bytes.

## POST /mobile/tts/playback

Bearer authentication required (`voice.use` scope). Request body (JSON):

```json
{
  "text": "The downstairs lights are off.",
  "voice": "default"
}
```

- `text` — required, non-empty after `strip()`, at most 2000 characters
  (`MAX_TTS_TEXT_CHARS`).
- `voice` — optional string. `null`/absent/blank/`"default"` selects the
  configured or provider-default voice id. An explicit value must be one of
  the resolvable provider voice IDs (`TextToSpeechAdapter.resolve_voice`);
  an unknown voice fails with `400 BAD_REQUEST`, never a silent fallback.

Response body (`200`), exact keys
(`rex/mobile_api/routes/voice.py::_handle_tts_playback`):

```json
{
  "request_id": "9a2b1c3d-4444-4444-8444-444444444444",
  "audio_base64": "UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAACAgICA",
  "mime_type": "audio/wav",
  "voice": "en-US-AriaNeural",
  "requested_voice": "default"
}
```

Field notes:

- `audio_base64` is standard base64 of the raw synthesized bytes (same
  encoding convention as `tts_base64` above) — never a `data:` URI.
- `mime_type` is computed from the provider that actually produced the
  audio (`TextToSpeechAdapter.mime_type()`), the same rule as
  `tts_mime_type` on the voice-upload response.
- `voice` is the concrete resolved provider voice ID that was actually used
  to synthesize, not an echo of the request.
- `requested_voice` is the client's original request value, normalized to
  the literal string `"default"` when the client sent `null`/absent/blank.

## Audio vector encoding in fixtures

`tests/mobile_api/contract_vectors.json` uses the same minimal valid WAV
vector for both `tts_base64` and `audio_base64`:
`UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAACAgICA`.
It decodes to 48 bytes and has a RIFF/WAVE header, so the fixture pairs it
with `audio/wav`. This fixture is a portable concrete example, not a claim
that every configured provider produces WAV: `edge-tts` responses use
`audio/mpeg`, while `xtts` and `pyttsx3` use `audio/wav`. Live conformance
that routes base64-encode actual provider audio is proven by
`tests/mobile_api/test_voice_upload.py::test_valid_audio_transcribes_and_answers`
and `tests/mobile_api/test_tts.py`.

## Canonical fixture identity

`tests/mobile_api/contract_vectors.json` in this backend worktree is the
sole canonical copy for this contract. The `http.voice_response` and
`http.tts_response` shapes in that file match the route serializers and the
tests above field-for-field. The mobile repository must synchronize its
`tests/contract/contract_vectors.json` (or equivalent) copy to be
byte-identical to this file. Its canonical SHA-256 is
`c819eacd06451cf3c45ab00b20885f3f4eab64a721c5927131569f6ce70ea7cb`.
Use a byte-preserving copy, not a parsed/reformatted JSON write; the focused
backend vector test locks this exact byte hash.

## Non-goals of this document

This document only defines the wire shape currently returned by
`/mobile/voice/upload` and `/mobile/tts/playback` in this repository. It
does not define a separate provider-neutral `SpeechRouter`/VoiceStudio
integration, provider-specific voice-ID negotiation, or bidirectional
call-time provider fallback; those are a distinct backend concern from this
mobile-gateway wire contract and must not be inferred from this document.
