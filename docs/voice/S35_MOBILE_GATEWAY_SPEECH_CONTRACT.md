# S35 Mobile Gateway Speech Contract

Status: authoritative for the two HTTP routes below. Revision: `2026-09-16.s35.1`.
This uncommitted revision is based on backend HEAD
`3eb777f5bd2d33e4d6a744743312ab39ef68d15a`; the supervisor owns the final
checkpoint commit.

## Canonical sources

The implementation in `rex/mobile_api/routes/voice.py`, its focused route
tests, and `tests/mobile_api/contract_vectors.json` define this contract.
The fixture is a machine-readable cross-repository vector, not illustrative
documentation.

## `POST /mobile/voice/upload`

The response has `request_id`, `transcript`, `response`, `status`, and
`toolUsed`. It additionally has `ttsBase64` only when configured TTS succeeds.
The two S35 upload additions are intentionally camelCase:

```json
{
  "request_id": "<uuid>",
  "transcript": "<string>",
  "response": "<string>",
  "status": "completed",
  "toolUsed": null,
  "ttsBase64": "<base64-audio>"
}
```

`ttsBase64` is base64 for the synthesized container bytes, but it does not
carry a MIME label or data-URI prefix. It is not independently directly
playable from the response alone: a client must only use it where it has
out-of-band knowledge of the configured provider/container. The server
determines the bytes' MIME through `TextToSpeechAdapter.mime_type()` from the
configured provider, but deliberately does not serialize that value in this
upload response. Do not infer MIME from a voice ID.

## `POST /mobile/tts/playback`

The response contains a directly playable inline data URI in `audio_url`:

```json
{
  "request_id": "<uuid>",
  "audio_url": "data:audio/wav;base64,<base64-audio>",
  "voice": "<resolved-voice-id>",
  "requested_voice": "default"
}
```

The MIME type is the media type between `data:` and `;base64` in `audio_url`.
It is selected server-side by `TextToSpeechAdapter.mime_type()` from the
configured provider. The data URI's decoded bytes are a complete audio
container, not raw PCM.

## Fixture audio

`http.voice_response.ttsBase64` and `http.tts_response.audio_url` embed the
same minimal valid 48-byte WAV vector. The vector is 8 kHz, mono, 8-bit PCM,
with four silent samples. Focused hygiene tests base64-decode it and open it
with the standard-library `wave` module.

The SHA-256 of those decoded fixture bytes is
`93bef78ec2fb0694560ab8a8cb26c1d914799f6a11db73335d3e0f74ab3fc2ae`.

## Superseded guidance

Snake-case upload fields `tool_used`, `tts_base64`, and `tts_mime_type`, and
playback fields `audio_base64` and `mime_type`, are superseded. No separate
MIME field accompanies `ttsBase64`; playback MIME belongs inside `audio_url`.
