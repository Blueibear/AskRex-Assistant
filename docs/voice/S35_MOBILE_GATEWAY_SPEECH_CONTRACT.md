# S35 Mobile Gateway Speech Contract

Document revision: `2026-09-16.s35.2`.
Wire contract version (the fixture's `contract_version`): `2026-09-16.s35.1` —
**unchanged**. Revision `.s35.2` is a documentation-and-regression revision: it
corrects the recorded base commit, adds the machine-readable fixture identity
block below, and states the upload `status` vocabulary explicitly. No request or
response field changed.

This revision is based on backend worktree HEAD
`6f359dbbea092635e130e09cffb346178ed854dd`. (Revision `.s35.1` recorded
`3eb777f5bd2d33e4d6a744743312ab39ef68d15a`; that commit is not the HEAD of this
worktree and the reference was stale.) The supervisor owns the final checkpoint
commit, so this file may be read while uncommitted.

Status: authoritative for the two HTTP routes below. Where this document, an
older mailbox message, or a mobile-side assumption disagree, the executable
sources listed next win, and this document is kept equal to them by the
regressions named in each section.

## Canonical sources

| Concern | Path |
|---|---|
| This contract | `docs/voice/S35_MOBILE_GATEWAY_SPEECH_CONTRACT.md` |
| Route serializers | `rex/mobile_api/routes/voice.py` |
| Server-side MIME selection | `rex/mobile_api/voice.py` (`TextToSpeechAdapter.mime_type()`) |
| Cross-repo wire fixture | `tests/mobile_api/contract_vectors.json` |
| Upload route regressions | `tests/mobile_api/test_voice_upload.py` |
| Playback route regressions | `tests/mobile_api/test_tts.py` |
| Fixture/document agreement | `tests/mobile_api/test_contract_vectors.py` |

The mobile repository keeps a byte-identical copy of the fixture at
`tests/contract/contract_vectors.json`.

The fixture is a machine-readable cross-repository vector, not illustrative
documentation. It is the only artifact both repositories are expected to hold
identically.

## `POST /mobile/voice/upload`

The response body has exactly five keys, plus `ttsBase64` when — and only when —
the configured TTS provider is available and synthesis succeeds:

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

The two S35 upload additions are intentionally camelCase: `toolUsed` and
`ttsBase64`. Every other key on this route, and every key on every other mobile
route, stays snake_case.

### Upload `status`

`status` is always the conversational value `completed`
(`rex.mobile_api.chat.STATUS_COMPLETED`). A voice turn is a reply, not a
mutation, so it is never upgraded to `verified` and never reports `attempted` or
`failed`; transport/processing problems are structured error envelopes with a
non-2xx code instead. The `attempted`/`verified`/`failed`/`needs_confirmation`
vocabulary in `statuses.action_statuses` belongs to action results, not to this
field.

### `ttsBase64` playability and MIME

`ttsBase64` is base64 of the synthesized container bytes. It carries no MIME
label and no data-URI prefix, so **it is not directly playable from the response
alone**: a client must either already know the configured provider's container
out of band, or sniff the decoded bytes, or use `POST /mobile/tts/playback`
(which is self-describing) when it needs a labelled stream.

The server does determine the bytes' media type — `TextToSpeechAdapter.mime_type()`
returns `audio/mpeg` for the `edge-tts` provider and `audio/wav` for every other
supported provider — but this route deliberately does not serialize that value.
MIME follows the configured provider only; never infer it from a voice ID, a
voice name, or the requested locale.

`toolUsed` is currently always `null` on this route: mobile voice turns do not
yet surface a tool identifier. Treat it as "nullable string", not as proof that
no tool ran.

## `POST /mobile/tts/playback`

The response has exactly four keys and contains a directly playable inline data
URI in `audio_url`:

```json
{
  "request_id": "<uuid>",
  "audio_url": "data:audio/wav;base64,<base64-audio>",
  "voice": "<resolved-voice-id>",
  "requested_voice": "default"
}
```

The MIME type is the media type between `data:` and `;base64` in `audio_url`. It
is selected server-side by the same `TextToSpeechAdapter.mime_type()` call, so a
server configured for `edge-tts` emits `data:audio/mpeg;base64,...`. Clients must
parse the media type out of `audio_url` rather than assuming `audio/wav`.

The data URI's decoded bytes are a complete audio container, not raw PCM.
`voice` is the resolved provider voice ID actually used; `requested_voice` echoes
what the client asked for (`"default"` when it asked for nothing). An unknown
requested voice is a 400 error, never a silent substitution.

## Fixture audio vector

`http.voice_response.ttsBase64` and the base64 payload inside
`http.tts_response.audio_url` are the **same** minimal valid WAV vector. It is a
real, decodable 48-byte RIFF/WAVE container: 8 kHz, mono, 8-bit unsigned PCM,
four silent samples (`0x80`). It is not placeholder text and not the literal
string `<base64-audio>`; `tests/mobile_api/test_contract_vectors.py` base64-decodes
it and opens it with the standard-library `wave` module, then reads its frames.

### Machine-readable identity

```
audio_vector_base64: UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAACAgICA
audio_vector_sha256: 93bef78ec2fb0694560ab8a8cb26c1d914799f6a11db73335d3e0f74ab3fc2ae
audio_vector_bytes: 48
wire_contract_version: 2026-09-16.s35.1
document_revision: 2026-09-16.s35.2
```

`audio_vector_sha256` is the SHA-256 of the **decoded** 48 audio bytes, not of
the base64 text and not of the fixture file.
`TestCanonicalContractDocument::test_document_records_the_fixture_audio_sha256`
recomputes it from the fixture and fails with the true digest if this document
ever drifts, so the value above is executable-verified rather than asserted by
hand.

### Fixture file identity

Do not use a raw SHA-256 of `contract_vectors.json` as the cross-repository
identity on its own. The file had no end-of-line attribute, so a Windows
checkout could materialize CRLF bytes while a Linux checkout materialized LF,
and the same logical fixture then hashes differently in each clone. That fully
explains the divergent fixture digests reported from different scratch trees.

Two things now make the bytes deterministic:

1. `.gitattributes` pins `tests/mobile_api/contract_vectors.json text eol=lf`,
   so every checkout of a synchronized copy materializes identical bytes.
2. `TestCanonicalContractDocument::test_fixture_file_is_canonical_json_text`
   asserts the file is exactly
   `json.dumps(obj, indent=2, ensure_ascii=False) + "\n"`, so a synchronized
   copy can be regenerated byte-for-byte from its parsed content.

Regeneration plus the pinned EOL is the authoritative synchronization check.
A file digest is only meaningful once both sides confirm LF line endings and a
single trailing newline; comparing `audio_vector_sha256`, `contract_version`, and
the field sets below is the check that cannot be defeated by checkout settings.

## Superseded guidance

The following shapes appeared in earlier S35 mailbox traffic and are superseded.
None of them exists in `rex/mobile_api/routes/voice.py`, and route regressions
now assert their absence:

| Superseded | Authoritative |
|---|---|
| upload `tool_used` | upload `toolUsed` |
| upload `tts_base64` | upload `ttsBase64` |
| upload `tts_mime_type` | no MIME field on the upload response at all |
| upload `status` of `attempted` / `verified` / `failed` | upload `status` is always `completed` |
| playback `audio_base64` + `mime_type` | playback `audio_url` inline data URI, MIME inside the URI |

No separate MIME field accompanies `ttsBase64`. Playback MIME belongs inside
`audio_url` and nowhere else.
