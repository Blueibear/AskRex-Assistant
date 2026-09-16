# S35 Mobile Gateway Speech Contract

Document revision: `2026-09-16.s35.5`.
Wire contract version (the fixture's `contract_version`): `2026-09-16.s35.1` —
**unchanged**. Revision `.s35.5` is a documentation-and-tooling revision: it adds
the portable synchronization checker `scripts/check_speech_contract_vectors.py`
so either repository can verify its own copy of the fixture without a
hand-carried file digest. No request or response field changed; `.s35.2` through
`.s35.5` describe the same wire contract, and `wire_contract_version`
deliberately does not move with the document revision.

`base_commit` below records the commit this revision was authored against:
`3780f744d295ea3d06a1a59382f438a4a5fec030`. (Earlier revisions recorded
`7bbf1cd998284ac0cd85f46a2b3cbf12619bc208`,
`d3994ea1fe5312128ed258ebf746db7b66e5a260`, `6f359dbbea092635e130e09cffb346178ed854dd`,
and `3eb777f5bd2d33e4d6a744743312ab39ef68d15a`.) The supervisor owns the final
checkpoint commit, so this file may be read while uncommitted and the checkpoint
commit is a descendant of the recorded base; a `base_commit` that is an ancestor
of the reader's HEAD is expected and is not drift. The base commit is provenance
only — it is **not** the synchronization key, and no consumer should gate on it.
Use `wire_contract_version` plus `audio_vector_sha256` plus the field sets below,
as described under "Fixture file identity".

Status: authoritative for the two HTTP routes below. Where this document, an
older mailbox message, or a mobile-side assumption disagree, the executable
sources listed next win, and this document is kept equal to them by the
regressions named in each section.

## Canonical sources

| Concern | Path |
|---|---|
| This contract | `docs/voice/S35_MOBILE_GATEWAY_SPEECH_CONTRACT.md` |
| Route serializers | `rex/mobile_api/routes/voice.py` |
| Server-side MIME selection (`TextToSpeechAdapter.mime_type()`) | `rex/mobile_api/voice.py` |
| Cross-repo wire fixture | `tests/mobile_api/contract_vectors.json` |
| Fixture end-of-line pin | `.gitattributes` |
| Portable synchronization checker | `scripts/check_speech_contract_vectors.py` |
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
`TestTtsPlayback::test_adapter_mime_follows_the_provider_not_the_voice` pins that
derivation, including the case where a provider that emits WAV is configured with
an `edge`-style voice name.

So a client has exactly three supported ways to obtain a media type for upload
inline TTS, in order of preference:

1. Call `POST /mobile/tts/playback` instead when a labelled stream is needed. It
   is self-describing and is the only route that serializes MIME.
2. Sniff the decoded bytes (`RIFF`/`WAVE` → `audio/wav`, `ID3`/frame sync →
   `audio/mpeg`).
3. Know the server's configured TTS provider out of band and apply the same
   provider → MIME mapping above.

Guessing `audio/wav` unconditionally is not supported, and the response must not
be handed to a player as if it were a URL or a data URI.

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
document_revision: 2026-09-16.s35.5
base_commit: 3780f744d295ea3d06a1a59382f438a4a5fec030
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

Three things now make the bytes deterministic:

1. `.gitattributes` pins `tests/mobile_api/contract_vectors.json text eol=lf`,
   so every checkout of a synchronized copy materializes identical bytes.
2. `TestCanonicalContractDocument::test_fixture_file_is_canonical_json_text`
   asserts the file is exactly
   `json.dumps(obj, indent=2, ensure_ascii=False) + "\n"`, so a synchronized
   copy can be regenerated byte-for-byte from its parsed content.
3. `TestCanonicalContractDocument::test_fixture_bytes_on_disk_are_lf_with_one_trailing_newline`
   checks the *raw bytes* rather than `read_text` (which normalizes newlines and
   therefore passes in a CRLF checkout): no `\r` anywhere, exactly one trailing
   `\n`, and byte-for-byte equality with the regenerated canonical JSON. This is
   the check that makes a whole-file digest reproducible, because it fails in the
   exact checkout that would otherwise hash differently.

Regeneration plus the pinned and byte-verified EOL is the authoritative
synchronization check. A file digest is only meaningful once both sides confirm
LF line endings and a single trailing newline; comparing `audio_vector_sha256`,
`contract_version`, and the field sets below is the check that cannot be defeated
by checkout settings.

### Authoritative synchronization procedure

Both repositories run these five checks against their own copy. All five passing
means the copies are equal; no message needs to carry a file digest.

1. `contract_version` equals `wire_contract_version` above.
2. `http.voice_response` keys are exactly
   `{request_id, transcript, response, status, toolUsed, ttsBase64}`.
3. `http.tts_response` keys are exactly
   `{request_id, audio_url, voice, requested_voice}`.
4. `http.voice_response.ttsBase64` equals `audio_vector_base64` above, and the
   base64 payload after `data:audio/wav;base64,` in `http.tts_response.audio_url`
   is the same string. Base64-decoding it yields `audio_vector_bytes` bytes whose
   SHA-256 is `audio_vector_sha256`.
5. The file text equals `json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"`
   and the raw bytes on disk contain no `\r` and end with exactly one `\n`.

`tests/mobile_api/test_contract_vectors.py` performs all five on the backend
side. Step 4's digest assertion fails with the *true* digest recomputed from the
fixture, so this document cannot silently drift from the bytes it describes.

`scripts/check_speech_contract_vectors.py` performs the same five checks as a
standalone stdlib-only script, so the mobile repository can verify its own copy
without a copy of this document and without a hand-carried file digest:

```bash
python scripts/check_speech_contract_vectors.py                        # backend copy
python scripts/check_speech_contract_vectors.py --vectors <path> --no-doc
CANONICAL_SPEECH_VECTORS_PATH=<path> python scripts/check_speech_contract_vectors.py --no-doc
```

The script exits non-zero with one line per failure, and always prints the
recomputed `audio_vector_sha256` plus both the raw and the LF-normalized
whole-file digests — so a comparison that does want a file digest can read the
true value from the run rather than from a message. Because the mobile copy has
no document, the script carries `wire_contract_version`, the two key sets, and
the audio-vector identity as constants;
`TestPortableSynchronizationChecker::test_checker_constants_match_the_document_and_the_fixture`
pins those constants to this document and to the fixture so the checker cannot
become a third contract. Use `--no-doc` only where this document is genuinely
absent; in this repository the document cross-check runs by default.

If a raw SHA-256 of the whole file is still wanted for a one-off comparison,
compute it over LF-normalized bytes on both sides
(`sha256(path.read_bytes().replace(b"\r\n", b"\n"))`) and treat a mismatch as a
prompt to re-run the five checks above rather than as the contract itself.

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
