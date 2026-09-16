# Mobile API Gateway — Windows Setup and Troubleshooting

This guide covers running the AskRex mobile API gateway on Windows 10/11 with
PowerShell.

The gateway serves `/mobile/*` only. Session 1 delivered authentication
(login, rotating refresh sessions, logout, session restore). Session 2 adds
authenticated chat (`POST /mobile/chat`), SSE streaming
(`POST /mobile/chat/stream`), WebSocket chat
(`WebSocket /mobile/chat/stream`, first-frame `auth`), voice upload
(`POST /mobile/voice/upload`), and protected TTS
(`POST /mobile/tts/playback`), all sharing one server-side
`(user_id, message_id)` idempotency store so a retried or replayed message
never executes twice.

Home Assistant, notifications, approvals, tasks, workflows, audit-log, and
settings routes remain explicit HTTP 501 `NOT_IMPLEMENTED` scaffolds and are
reported as `false` in `/mobile/capabilities` until they are really
implemented. `live_voice` (real-time duplex audio) is not implemented and
stays `false`. Voice and TTS capabilities are runtime-aware: they report
`true` only when their dependencies (Whisper + ffmpeg + a downloaded model;
the configured TTS engine) are actually available.

## 1. Prerequisites

- Python 3.11 virtual environment with the project installed (`pip install .`).
- The repository checked out (non-secret config lives in
  `config/rex_config.json`; secrets live in the credential vault).

## 2. Generate the JWT secret

The gateway signs short-lived access tokens with vault entry `REX_JWT_SECRET`.
The secret must be at least 32 characters; the server fails closed without it.

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

For an existing source install, place the output in `.env` only long enough to
run the transactional migration:

```text
REX_JWT_SECRET=<generated 64-hex-character value>
```

```powershell
python scripts/migrate_credentials_to_vault.py --scope household --owner household --apply
```

The migration verifies the encrypted entry/reference and atomically removes the
plaintext source. It creates no plaintext backup.

## 3. Create a mobile user

```powershell
python -m rex mobile-user create --username james
```

- The password is prompted twice with hidden input. It is never accepted on
  the command line, echoed, or logged.
- Users are stored in the canonical `data/users.db` with bcrypt hashes.
- A generated UUID is the canonical user ID; the username is display/login
  data only.
- The first registered user is automatically granted `admin` (shown as the
  `owner` role in the mobile app).

## 4. Configuration (optional)

Runtime settings live in `config/rex_config.json` under the `mobile_api`
group. All values below are the defaults; add only what you need to change:

```json
{
  "mobile_api": {
    "enabled": false,
    "host": "127.0.0.1",
    "port": 8765,
    "allowed_origins": [],
    "require_tls": false,
    "api_version": "1.0",
    "access_token_ttl_seconds": 900,
    "refresh_token_ttl_days": 30,
    "max_json_bytes": 1048576,
    "max_audio_bytes": 15728640,
    "max_audio_seconds": 60,
    "rate_limit_default": "60 per minute",
    "rate_limit_login": "10 per minute",
    "rate_limit_refresh": "30 per minute",
    "rate_limit_chat": "30 per minute",
    "rate_limit_voice": "10 per minute",
    "idempotency_retention_hours": 48
  }
}
```

Notes:

- CORS is deny-by-default: `allowed_origins` is empty and `"*"` is rejected.
  Native mobile clients do not need CORS.
- The server never starts automatically because configuration exists; it runs
  only when you start it explicitly.
- Rate limiting uses in-memory storage — suitable only for this
  single-process development server.
- `require_tls` only opts a **loopback** bind into TLS for local testing; it
  cannot disable TLS for a non-loopback (LAN) bind — that is always
  TLS-enforced regardless of this flag (S7).

## 5. Start the server

Localhost (default, recommended; plain HTTP):

```powershell
python -m rex mobile-api --host 127.0.0.1 --port 8765
```

LAN bind (the supported paired-device topology; S7 requires TLS and cannot
be started without it):

```powershell
python -m rex mobile-api --host 0.0.0.0 --port 8765
```

CLI flags override the `mobile_api` config values; otherwise the config is
used, then the safe localhost defaults. Any bind beyond loopback
(`127.0.0.1`/`localhost`) always provisions or reuses a self-signed TLS
certificate under `<household_data_dir>/mobile_tls/` and serves HTTPS — this
cannot be disabled. If that certificate/key material cannot be generated or
loaded, the command prints the error and exits without opening a socket
(fail closed); it never falls back to plain HTTP. The startup banner prints
the bind address, the resulting `https://` or `http://` status URL, and —
when TLS is enabled — the certificate's SHA-256 fingerprint. Pair mobile
devices (see `docs/mobile/DEVICE_PAIRING.md`) so they pin that fingerprint
before they connect; a certificate that later changes (data reset, deleted
`mobile_tls/` directory) requires every paired device to re-pair. Never
expose this development server directly to the internet.

## 6. Windows Firewall (LAN development only)

Scope inbound access to the private profile and the specific port:

```powershell
New-NetFirewallRule -DisplayName "AskRex Mobile API (private)" `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 `
  -Profile Private
```

Remove it when finished:

```powershell
Remove-NetFirewallRule -DisplayName "AskRex Mobile API (private)"
```

Find your PC's LAN IP for the phone's server URL (`http://<PC-LAN-IP>:8765`):

```powershell
Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp |
  Select-Object IPAddress, InterfaceAlias
```

## 7. Verify with PowerShell

Status (no authentication):

```powershell
Invoke-RestMethod http://127.0.0.1:8765/mobile/status
Invoke-RestMethod http://127.0.0.1:8765/mobile/capabilities
```

Login (prompts for the password without echoing it):

```powershell
$cred = Get-Credential -UserName james -Message "AskRex mobile login"
$body = @{
  username = $cred.UserName
  password = $cred.GetNetworkCredential().Password
  device   = @{ device_id = [guid]::NewGuid().ToString(); name = "Dev PC"; platform = "windows"; app_version = "0.1.0" }
} | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/mobile/auth/login `
  -ContentType "application/json" -Body $body
```

Current session:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/mobile/auth/session `
  -Headers @{ Authorization = "Bearer $($login.access_token)" }
```

Refresh (rotates the refresh token — keep only the new one):

```powershell
$refresh = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/mobile/auth/refresh `
  -ContentType "application/json" `
  -Body (@{ refresh_token = $login.refresh_token } | ConvertTo-Json)
```

Logout (revokes the current session; the access token stops working):

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/mobile/auth/logout `
  -Headers @{ Authorization = "Bearer $($refresh.access_token)" }
```

Chat (non-streaming; `message_id`/`conversation_id` are client-generated
UUIDs and double as the idempotency key — resending the exact same request
replays the stored result without re-executing):

```powershell
$chat = @{
  message_id      = [guid]::NewGuid().ToString()
  conversation_id = [guid]::NewGuid().ToString()
  sent_at         = (Get-Date).ToUniversalTime().ToString("o")
  message         = "Hello Rex"
  mode            = "mobile_text"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/mobile/chat `
  -ContentType "application/json" -Body $chat `
  -Headers @{ Authorization = "Bearer $($login.access_token)" }
```

SSE streaming uses the same body against `POST /mobile/chat/stream` and
returns `text/event-stream` frames (`token` events, then one terminal
`message_done` or `error`). The WebSocket endpoint is
`ws://127.0.0.1:8765/mobile/chat/stream`; the first frame must be
`{"type": "auth", "access_token": "<jwt>", "client": {...}}` — tokens never
go in the URL.

TTS (JSON inline data URI; text is never placed in a query string):

```powershell
$tts = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/mobile/tts/playback `
  -ContentType "application/json" -Body (@{ text = "Hello from Rex." } | ConvertTo-Json) `
  -Headers @{ Authorization = "Bearer $($login.access_token)" }
# audio_url is "data:<mime>;base64,<audio>" — the media type comes from the
# configured TTS provider, so split it off instead of assuming a container.
$mime, $payload = $tts.audio_url -replace '^data:', '' -split ';base64,', 2
$extension = if ($mime -eq 'audio/mpeg') { 'mp3' } else { 'wav' }
[IO.File]::WriteAllBytes("rex-tts-sample.$extension", [Convert]::FromBase64String($payload))
```

The response `voice` is the concrete provider voice that produced the audio;
`requested_voice` preserves the caller's request (`default` when omitted).
`docs/voice/S35_MOBILE_GATEWAY_SPEECH_CONTRACT.md` is the authoritative wire
contract for `/mobile/voice/upload` and `/mobile/tts/playback`.

Voice upload (multipart; the file's actual bytes are validated — M4A/MP4,
AAC, MP3, or WAV — and must decode successfully; limits are 15 MiB and 60
seconds):

```powershell
curl.exe -X POST http://127.0.0.1:8765/mobile/voice/upload `
  -H "Authorization: Bearer $($login.access_token)" `
  -F "audio=@recording.m4a" -F "mode=mobile_voice"
```

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Error: REX_JWT_SECRET is not set` | Store/migrate the secret into the vault (section 2) and restart. |
| `REX_JWT_SECRET is too short` | The secret must be at least 32 characters; regenerate with `secrets.token_hex(32)`. |
| `401 AUTH_INVALID_CREDENTIALS` on login | Wrong username or password (the error is deliberately identical for both), or the account is disabled. |
| `401 AUTH_REFRESH_REUSED` | A refresh token was used twice. All sessions in that token family were revoked as a safety measure — log in again. Make sure the client always stores the newest refresh token. |
| `401 AUTH_SESSION_REVOKED` | The session was logged out, revoked, or expired. Log in again. |
| `401 AUTH_TOKEN_EXPIRED` | The access token passed its 15-minute lifetime — refresh, or the refresh token passed its 30-day lifetime — log in again. |
| `429 RATE_LIMITED` | Too many requests; wait for the `Retry-After` seconds in the response. |
| `501 NOT_IMPLEMENTED` | The route is a truthful scaffold — that feature is not built yet (see `/mobile/capabilities`). |
| `409 IDEMPOTENCY_CONFLICT` | The same `message_id` was reused with a different message body, or the `Idempotency-Key` header does not match the JSON `message_id`. Send a new message ID. |
| `409 REQUEST_IN_PROGRESS` | The same message is still executing (e.g. a retry raced the original delivery). Wait for the original result; it is replayed for the same ID once complete. |
| `503 BACKEND_UNAVAILABLE` on `/mobile/voice/upload` | Whisper, ffmpeg, or the configured Whisper model is not available locally. Models are never downloaded during a request — install/download them first (`pip install -r requirements-cpu.txt`, ffmpeg on PATH, then run any local STT once to fetch the model). |
| `503 BACKEND_UNAVAILABLE` on `/mobile/tts/playback` | The configured TTS engine (`config.voice.tts_engine`) is not installed or failed/timed out. `/mobile/capabilities` reports the current truth. |
| `415 INVALID_MEDIA` on voice upload | The uploaded bytes are not a supported container (M4A/MP4, AAC, MP3, WAV) or could not be decoded. The filename and declared MIME type are ignored — only real content counts. |
| Phone cannot reach the server | Confirm the server is bound to `0.0.0.0`, the firewall rule targets the Private profile and correct port, both devices share the same network, and the phone uses `https://<PC-LAN-IP>:8765` (LAN binds are TLS-only; see S7 below). |
| `port ... in use` | Another process owns the port. Pick another port with `--port`. |
| `Error: Desktop TLS certificate material could not be provisioned.` (LAN bind) | The self-signed certificate/key under `<household_data_dir>/mobile_tls/` could not be generated or loaded (corrupted key file, unwritable data directory, or the `cryptography` package is missing). The command exits without opening a socket. Fix the underlying filesystem/dependency issue, or delete `mobile_tls/` to force regeneration (this invalidates the fingerprint every paired device pinned — they must re-pair). |
| `403 PAIRING_INVALID` from `/mobile/auth/activate-device` on a LAN bind | The device's pinned certificate fingerprint no longer matches the desktop's current certificate (rotated/reset since pairing), or the device was paired before S7 and has no pinned fingerprint. Re-pair the device. |

## 9. Security notes

- Access tokens live 15 minutes; refresh tokens 30 days, rotate on every use,
  and are stored as SHA-256 hashes only.
- Reuse of a rotated refresh token revokes the whole token family and its
  session, and the event is audited without logging token material.
- Logout invalidates otherwise-unexpired access tokens through server-side
  session checks; logout-all revokes only your own sessions.
- Passwords, tokens, hashes, and request bodies never appear in server logs.
- LAN binding without TLS is a development-only configuration for trusted
  networks; public deployment is separately gated on TLS and reverse-proxy
  work.

## Validation record (Session 1)

Local smoke performed on Windows 11 with PowerShell/loopback:
status → capabilities → login → invalid login → session → refresh →
refresh-reuse (family revoked) → scaffold 501 → logout → revoked session.
Physical-iPhone and LAN validation have **not** been performed in Session 1
and remain tracked under issue #323.

## Validation record (Session 2 correction pass)

Windows 11 / Python 3.11 loopback validation exercised an actual
Flask-Sock/simple-websocket upgrade and verified first-frame authentication,
ack/token/message_done delivery, invalid-token close `4401`, observable auth
timeout close `4408`, logout revocation close `4401`, no URL token, and
same-message HTTP replay with one Assistant execution. The automated test is
`tests/mobile_api/test_chat_websocket_live.py`.

Close code `4403` (authenticated but forbidden) is **reserved** and has never
been exercised: the canonical permission model gates tools at dispatch time,
not chat access, so Session 2 has no real authenticated-but-forbidden
condition and no server code path emits 4403. Clients still handle it
defensively (no automatic reconnect).

`python -m pytest -q tests/mobile_api` passed 252 tests. The complete repository
suite still has unrelated failures reproduced on `master` in time/weather,
workflow, and the generated coverage-report meta test. Physical-iPhone and LAN
validation remain not run.
