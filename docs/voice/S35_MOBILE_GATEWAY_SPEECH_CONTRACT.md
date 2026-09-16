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

This document is authoritative as of backend base commit
`6b35130ddd3f578dd6ce9a2df5dd000d71bc14ab` (`detached HEAD`, re-verified
directly from `.git/HEAD`, `.git/logs/HEAD`, and `.git/packed-refs`; it is
currently identical to `refs/remotes/origin/lead/production-readiness-live-test`).
Two earlier drafts of this section named `0d6420b656d6d1a40f3ecf552c03d03b93a2e285`
and, later, `60d67f2eb417b7eb5803f055ee794677989ca98c`; neither matches the
current HEAD (the live branch tip advanced between those drafts and this
one) and both are corrected here. Whoever next edits this file must
re-read `.git/HEAD` rather than trust either superseded hash.

This working tree's `.git/config` records `origin` as
`C:\Users\james\rex-ai-test\rex-ai-production-readiness`, and this HEAD is
the tip of `lead/production-readiness-live-test`, not `lead/us126-listening-privacy-tray`.
Any coordination guidance that names `rex-ai-us126-final` as the worktree
holding this contract is describing a different checkout than the one this
document was verified against; the sole authoritative source for this
contract is whichever worktree actually contains `rex/mobile_api/routes/voice.py`
at this HEAD with the shapes below, and that must be re-confirmed by
direct inspection in `rex-ai-us126-final` before this document is treated
as binding there.

`contract_vectors.json`'s `contract_version` field is `"2026-09-16.323.5"`;
that version string, not any mailbox-quoted hash, is the way to confirm a
copy is current absent a freshly computed SHA-256 (below).

This backend snapshot contains only the mobile-gateway voice/TTS routes from
issue #323 (`rex/mobile_api/routes/voice.py`, `rex/mobile_api/voice.py`).
Direct search of this tree at this HEAD confirms `rex/speech/`,
`tests/speech/`, and `docs/voice/SPEECH_ROUTER_VOICESTUDIO.md` do not exist
here. Any mailbox guidance about a provider-neutral `SpeechRouter`,
VoiceStudio integration, redirect-origin hardening, or bidirectional
call-time provider fallback (`STORY-S35-SPEECH-ROUTER`) describes a
different, broader story that is not present in this snapshot and is out of
scope for this document, which covers only the `/mobile/voice/upload` and
`/mobile/tts/playback` wire contract.

A SHA-256 of the exact `tests/mobile_api/contract_vectors.json` bytes was
not computed by this change: this session has no shell/code-execution
access, and several previously mailbox-quoted "SHA-256" values (e.g.
65–66 hex characters) are not valid SHA-256 digests (32 bytes / 64 hex
characters) and must not be trusted. The fixture's `tts_base64`/
`audio_base64` value (`UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAACAgICA`)
was hand-verified, base64 group by base64 group, to decode to exactly these
48 bytes (hex): `52 49 46 46 28 00 00 00 57 41 56 45 66 6D 74 20 10 00 00 00
01 00 01 00 40 1F 00 00 40 1F 00 00 01 00 08 00 64 61 74 61 04 00 00 00 80
80 80 80` — a structurally valid RIFF/WAVE/fmt/data 8 kHz mono 8-bit PCM WAV
(`ChunkSize=40`, `Subchunk1Size=16`, `AudioFormat=1`, `NumChannels=1`,
`SampleRate=8000`, `ByteRate=8000`, `BlockAlign=1`, `BitsPerSample=8`,
`Subchunk2Size=4`, 4 bytes of `0x80` silence), matching what
`test_fixture_audio_vectors_are_valid_decodable_wav` asserts. The
deterministic supervisor test run must still compute the whole-file
SHA-256, e.g.:

```
python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('tests/mobile_api/contract_vectors.json').read_bytes()).hexdigest())"
```
