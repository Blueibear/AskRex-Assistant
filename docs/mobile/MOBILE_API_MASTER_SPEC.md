# AskRex Mobile API Gateway — Master Specification

Status: Planning baseline for issue #323  
Reviewed: 2026-07-14  
Backend repository: `Blueibear/AskRex-Assistant`  
Mobile repository: `Blueibear/AskRex`

## 1. Purpose

Build an authenticated mobile gateway that lets the AskRex Expo/iOS client use the existing Rex runtime over a trusted local network during development and, later, through a TLS-terminated public deployment.

The gateway is a transport and security boundary. It must reuse the canonical Rex assistant, identity, memory, permissions, policy, approvals, Home Assistant, speech-to-text, and text-to-speech systems. It must not create a parallel assistant runtime, second user database, second permission model, or client-authoritative approval path.

## 2. Truth and delivery rules

A mobile surface is described as:

- **Implemented** only when backend code exists and focused automated tests pass.
- **Integrated** only when the mobile client and backend use the same documented wire contract.
- **Verified** only when the complete path has been exercised against the real runtime.
- **Scaffolded** only when it returns an explicit `NOT_IMPLEMENTED` response and the capability is reported as false.

No mock, stub, queued request, HTTP success, or client-side biometric result may be described as a completed or verified external action.

## 3. Non-negotiable security invariants

1. The server derives identity from validated credentials. It never trusts client-supplied `user_id`, role, permissions, risk level, ownership, approval status, or biometric status.
2. User IDs are authorization keys. Validate them with `rex.identity.validate_user_id` before private state, path, cache, credential, database, event, memory, history, or tool access.
3. The validated request identity is passed explicitly to `Assistant` through `active_user_id` or an explicitly bound constructor. Missing identity fails closed.
4. Access and refresh tokens never appear in URLs, logs, exception text, analytics, or response diagnostics.
5. Refresh tokens are high-entropy opaque values. Only hashes are stored.
6. Every authenticated request validates access-token signature, issuer, audience, expiration, session status, user existence/status, and revocation state.
7. Authorization is resolved from current server-side permissions. Signed display claims are never the authority for tool execution.
8. Current Rex risk and policy rules remain authoritative. Unknown or medium/high-risk actions require the existing approval path unless an explicit policy allows them.
9. A client biometric result is not sufficient server proof for a critical action. Critical approval requires a server-issued challenge and a server-verifiable response or remains unavailable.
10. External actions use `attempted`, `completed`, `verified`, `failed`, or `needs_confirmation`. `verified` requires real completion evidence or state readback.
11. Repeated HTTP or WebSocket delivery of the same message ID cannot execute a tool twice.
12. Network exposure defaults to localhost. LAN binding is explicit, authenticated, and rate-limited. **Superseded by S7:** LAN (non-loopback) binding always requires and serves TLS via a desktop-owned, pairing-pinned self-signed certificate — it is never plaintext, regardless of the `require_tls` config flag. See `docs/mobile/DEVICE_PAIRING.md` (S7 section) and `rex/mobile_api/tls.py`.
13. Request and response bodies containing chat text, passwords, tokens, or voice content are not logged.
14. CORS is deny-by-default. Native mobile clients do not require permissive browser CORS.

## 4. Process and configuration

### 4.1 CLI

The canonical server command is:

```powershell
python -m rex mobile-api --host 127.0.0.1 --port 8765
```

LAN bind (S7: TLS-enforced, cannot start without usable certificate material):

```powershell
python -m rex mobile-api --host 0.0.0.0 --port 8765
```

The command must print:

- bind host and port;
- whether TLS is enabled for this bind, and the certificate's SHA-256
  fingerprint when it is;
- the resulting `https://`/`http://` status URL;
- no secret values.

A non-loopback bind that cannot obtain usable TLS material prints the error
and exits (code 1) without opening a socket — it never falls back to plain
HTTP.

### 4.2 Configuration

Add a typed `mobile_api` configuration group. The canonical JSON shape is:

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
    "rate_limit_voice": "10 per minute"
  }
}
```

Secrets remain in `.env`:

```text
REX_JWT_SECRET=<at least 32 random bytes>
```

The server uses an explicit issuer and audience:

```text
issuer:   askrex-assistant
audience: askrex-mobile
```

### 4.3 Development user setup

Add a safe CLI flow that reuses `rex.auth.create_user`, creates the matching Rex profile, and bootstraps first-user permissions. Recommended command:

```powershell
python -m rex mobile-user create --username james
```

The command prompts for the password twice with `getpass`, never accepts or echoes a password by default, and never commits credentials. A generated UUID remains the canonical user ID; the username/display name is not used as an authorization key.

## 5. Common HTTP behavior

### 5.1 Headers

Every response includes:

```text
X-Request-ID: <UUID>
X-AskRex-API-Version: 1.0
```

Authenticated requests use:

```text
Authorization: Bearer <access-token>
```

Chat requests may also send:

```text
Idempotency-Key: <message-id>
```

When both the header and JSON `message_id` are present, they must match.

### 5.2 Error envelope

Use the repository's nested error envelope and extend it consistently:

```json
{
  "error": {
    "code": "AUTH_INVALID_CREDENTIALS",
    "message": "Invalid username or password.",
    "retryable": false,
    "request_id": "<request-id>"
  }
}
```

The mobile client must read `error.message`; transitional parsing of a top-level string may remain temporarily, but new backend routes do not return mixed shapes.

Stable error codes include:

| Code | Typical status |
|---|---:|
| `BAD_REQUEST` | 400 |
| `AUTH_INVALID_CREDENTIALS` | 401 |
| `AUTH_TOKEN_EXPIRED` | 401 |
| `AUTH_TOKEN_INVALID` | 401 |
| `AUTH_TOKEN_REVOKED` | 401 |
| `AUTH_SESSION_REVOKED` | 401 |
| `AUTH_REFRESH_REUSED` | 401 |
| `FORBIDDEN` | 403 |
| `PERMISSION_DENIED` | 403 |
| `APPROVAL_REQUIRED` | 409 |
| `NOT_IMPLEMENTED` | 501 |
| `UNSUPPORTED_API_VERSION` | 426 |
| `INVALID_MEDIA` | 415 |
| `PAYLOAD_TOO_LARGE` | 413 |
| `RATE_LIMITED` | 429 |
| `BACKEND_UNAVAILABLE` | 503 |
| `INTERNAL_ERROR` | 500 |

Error text must not reveal whether an arbitrary username, session ID, account ID, credential reference, or private resource exists.

## 6. Canonical endpoints

### 6.1 Authentication

```text
POST /mobile/auth/login
POST /mobile/auth/refresh
POST /mobile/auth/logout
POST /mobile/auth/logout-all
GET  /mobile/auth/session
```

#### Login request

```json
{
  "username": "james",
  "password": "<password>",
  "device": {
    "device_id": "<stable-random-device-id>",
    "name": "James's iPhone",
    "platform": "ios",
    "app_version": "0.1.0"
  }
}
```

`device_id` is an app-generated stable random identifier, not an advertising identifier or hardware serial.

#### Login response

```json
{
  "access_token": "<JWT>",
  "refresh_token": "<opaque-token>",
  "token_type": "Bearer",
  "expires_in": 900,
  "refresh_expires_in": 2592000,
  "session_id": "<UUID>",
  "user": {
    "id": "<canonical-user-id>",
    "name": "James",
    "role": "owner",
    "permissions": ["admin"],
    "color": "#2D7FF9"
  }
}
```

`role` is a presentation projection: `owner` when the current user has `admin`, otherwise `member`. Authorization uses the live permission set.

#### Refresh request

```json
{
  "refresh_token": "<opaque-token>"
}
```

A successful refresh rotates the refresh token and returns a new token pair. Reuse of a consumed token revokes the entire token family/session and returns `AUTH_REFRESH_REUSED`.

#### Logout

`POST /mobile/auth/logout` revokes the current session identified by the access token. It is idempotent.

#### Logout all

`POST /mobile/auth/logout-all` revokes every active mobile session for the authenticated user. It cannot revoke another user's sessions.

#### Current session

`GET /mobile/auth/session` returns the current live user/session projection. The mobile app uses this endpoint to restore display state rather than trusting locally decoded claims.

### 6.2 Status and capabilities

```text
GET /mobile/status
GET /mobile/capabilities
```

These endpoints may be unauthenticated but reveal only non-sensitive compatibility and health data.

Status response:

```json
{
  "status": "ok",
  "api_version": "1.0",
  "server_version": "<package-version>",
  "request_id": "<request-id>"
}
```

Capabilities response:

```json
{
  "api_version": "1.0",
  "minimum_app_version": "0.1.0",
  "server_version": "<package-version>",
  "features": {
    "authentication": true,
    "chat": true,
    "chat_streaming": true,
    "websocket_chat": true,
    "voice_upload": true,
    "tts": true,
    "attachments": true,
    "live_voice": false,
    "notifications": false,
    "approvals": false,
    "home_assistant": false
  }
}
```

Each feature is true only after its real backend path and automated tests exist. Configuration alone does not make a capability true. `attachments` (US-086 slice) has no optional ML runtime dependency, so it is reported unconditionally true, like `authentication`.

### 6.3 Chat

```text
POST      /mobile/chat
POST      /mobile/chat/stream
WebSocket /mobile/chat/stream
```

Canonical chat request:

```json
{
  "message_id": "<UUID>",
  "conversation_id": "<UUID>",
  "sent_at": "<ISO-8601 UTC>",
  "message": "Turn off the downstairs lights",
  "mode": "mobile_text",
  "client_context": {
    "device": "iphone",
    "location_hint": "home",
    "response_preference": "brief"
  },
  "attachment_ids": []
}
```

No `user_id`, role, permissions, approval, or risk fields are accepted. `attachment_ids` is optional (default `[]`), holds at most 5 unique UUIDs (`PAYLOAD_TOO_LARGE` beyond that), and each ID must already belong to the authenticated user, the same paired device, and this exact `conversation_id` — see section 6.5.

Non-streaming response:

```json
{
  "request_id": "<request-id>",
  "message_id": "<message-id>",
  "conversation_id": "<conversation-id>",
  "response": "The downstairs lights are off.",
  "status": "verified",
  "events": [],
  "attachments": []
}
```

`attachments` mirrors the resolved `attachment_ids` as safe provenance metadata (section 6.5); it is empty when the request had none. The HTTP streaming and WebSocket terminal events carry the same `attachments` field.

Normal conversational replies use `completed`. `verified` is reserved for externally verified results.

The HTTP streaming endpoint uses `text/event-stream`. Each `data:` payload is one canonical event from section 7, ending with `message_done`. It does not emit plain text or reinterpret malformed JSON as a token.

### 6.4 Voice

```text
POST /mobile/voice/upload
POST /mobile/tts/playback
```

Voice upload requires Bearer authentication and multipart form data:

- `audio`: required file;
- `mode`: `mobile_voice`;
- `client_context`: JSON string.

Initial supported containers:

- M4A/MP4;
- AAC;
- MP3;
- WAV.

Validation uses file signatures/container parsing plus successful decoding. Filename and declared MIME type are insufficient. Limits are 15 MiB and 60 seconds. Empty, malformed, unsupported, or undecodable audio fails without invoking Rex.

Voice response:

```json
{
  "request_id": "<request-id>",
  "transcript": "Turn off the downstairs lights",
  "response": "The downstairs lights are off.",
  "status": "attempted",
  "toolUsed": null,
  "ttsBase64": "<base64-audio>"
}
```

The first implementation uses authenticated JSON with base64 audio rather than placing text or bearer material in a URL. A short-lived protected artifact endpoint may replace it later without changing the security requirements.

TTS request:

```json
{
  "text": "The downstairs lights are off.",
  "voice": "default"
}
```

TTS validates text length, resolves only an allowed/available voice, avoids logging text, and returns:

```json
{
  "request_id": "<request-id>",
  "audio_url": "data:audio/mpeg;base64,<base64-audio>"
}
```

The provider-neutral SpeechRouter (S35) preserves these shapes. The MIME type
in `audio_url` describes the provider that actually synthesized the returned
audio. `voice` may be an AskRex-owned alias (`majel`, `james`, `cole`) on the
request side. See `docs/voice/S35_MOBILE_GATEWAY_SPEECH_CONTRACT.md`.

### 6.5 Attachments (US-086 slice)

```text
POST /mobile/attachments
```

Attachments are private, short-lived, per-conversation chat inputs. They are not indexed, promoted to memory, or injected into unrelated turns — that broader indexing/memory work remains a later US-086 slice.

Upload requires Bearer authentication with the `attachments.upload` scope and `multipart/form-data` with exactly two parts:

- `conversation_id`: required UUID form field (repeated fields are rejected);
- `attachment`: required single file part.

Limits (`mobile_api` config): `max_attachment_bytes` (default 10 MiB, enforced from a bounded incremental read — declared `Content-Length` is never trusted alone), `max_attachments_per_conversation` (default 5, `PAYLOAD_TOO_LARGE`), `attachment_retention_seconds` (default 3600; expired rows/bytes are cleaned up lazily on the next create/reference call).

Content type is never taken from the client's declared MIME type or filename extension. It is sniffed from bounded, non-decoding structural validation of the bytes themselves (PNG chunk/CRC walking, JPEG segment framing, HEIC/HEIF BMFF box walking, PDF object/trailer/xref resolution, or else plain UTF-8 text); anything else, including a bare matching signature without a complete container, returns `INVALID_MEDIA` (415). The response filename is a sanitized basename only — client directory components are stripped and never echoed back.

Upload response:

```json
{
  "request_id": "<request-id>",
  "status": "ready",
  "attachment": {
    "attachment_id": "<UUID>",
    "conversation_id": "<conversation-id>",
    "filename": "note.txt",
    "media_type": "text/plain",
    "size_bytes": 12
  }
}
```

To use an uploaded attachment, the client passes its `attachment_id` in the chat request's `attachment_ids` (section 6.3, at most 5 per message). The server re-validates that every referenced ID belongs to the authenticated user, the same paired device, this exact `conversation_id`, and has not expired, returning `FORBIDDEN` (403) otherwise — an attachment can never be referenced from a different conversation, device, or user than the one that uploaded it. Matching chat/SSE/WebSocket responses echo the same safe `attachment` metadata (never a filesystem path) in their `attachments` field.

### 6.6 Explicit scaffolds

Until their real ownership, permission, and persistence contracts are implemented, these routes return HTTP 501 with `NOT_IMPLEMENTED` and their capability remains false:

```text
GET  /mobile/home/entities
POST /mobile/home/command
GET  /mobile/notifications
GET  /mobile/approvals
GET  /mobile/tasks
GET  /mobile/workflows
GET  /mobile/audit-log
GET  /mobile/settings
```

A scaffold never returns fake entities, actions, notifications, tasks, settings, approvals, or success states.

## 7. Canonical WebSocket protocol

All wire fields use `snake_case` except the historical mobile voice response
fields `toolUsed` and `ttsBase64`, which remain camelCase for compatibility
with the shipped client. New voice fields must not introduce another casing
style.

### 7.1 Authentication

The client opens `/mobile/chat/stream` with no token in the URL. The first frame must be:

```json
{
  "type": "auth",
  "access_token": "<JWT>",
  "client": {
    "platform": "ios",
    "app_version": "0.1.0",
    "device_id": "<stable-random-device-id>"
  }
}
```

No other frame is processed before authentication.

Success:

```json
{
  "type": "auth_ok",
  "session_id": "<session-id>",
  "user": {
    "id": "<canonical-user-id>",
    "name": "James",
    "role": "owner",
    "permissions": ["admin"]
  }
}
```

Failure:

```json
{
  "type": "auth_error",
  "code": "AUTH_TOKEN_EXPIRED",
  "message": "Access token expired."
}
```

Close codes:

- `4401`: missing, invalid, expired, or revoked authentication/session;
- `4403`: authenticated but forbidden;
- `4408`: authentication frame timeout;
- `4429`: connection rate-limited.

### 7.2 Chat frame

```json
{
  "type": "chat",
  "message_id": "<UUID>",
  "conversation_id": "<UUID>",
  "sent_at": "<ISO-8601 UTC>",
  "message": "Hello Rex",
  "mode": "mobile_text",
  "client_context": {
    "device": "iphone",
    "location_hint": "home",
    "response_preference": "brief"
  }
}
```

Acknowledgement after validation and durable idempotency reservation:

```json
{
  "type": "ack",
  "message_id": "<message-id>",
  "accepted_at": "<ISO-8601 UTC>"
}
```

### 7.3 Server events

```json
{ "type": "token", "message_id": "...", "content": "." }
```

```json
{
  "type": "tool_call",
  "message_id": "...",
  "tool": "home_assistant",
  "action": "turn_off",
  "target": "downstairs lights"
}
```

```json
{
  "type": "tool_result",
  "message_id": "...",
  "tool": "home_assistant",
  "action": "turn_off",
  "status": "verified",
  "message": "Downstairs lights are off."
}
```

```json
{
  "type": "approval_required",
  "message_id": "...",
  "approval_id": "...",
  "risk_level": "critical",
  "action": "...",
  "requires_biometric": true,
  "expires_at": "<ISO-8601 UTC>"
}
```

```json
{
  "type": "message_done",
  "message_id": "...",
  "conversation_id": "...",
  "full_content": "...",
  "status": "completed"
}
```

```json
{
  "type": "error",
  "message_id": "...",
  "code": "BACKEND_UNAVAILABLE",
  "message": "Rex is temporarily unavailable.",
  "retryable": true,
  "request_id": "..."
}
```

Ping/pong frames use JSON and do not carry private content:

```json
{ "type": "ping", "sent_at": "<ISO-8601 UTC>" }
{ "type": "pong", "sent_at": "<ISO-8601 UTC>" }
```

### 7.4 Idempotency

- The idempotency key is `(user_id, message_id)`.
- The same key is shared across HTTP and WebSocket transports.
- The server reserves the key before acknowledging a request.
- A duplicate in progress receives the original acknowledgement and attaches to or reports the current state without re-executing tools.
- A completed duplicate returns/replays the stored terminal result.
- The same `message_id` from another user is independent.
- Reusing a message ID with a different normalized request hash returns a conflict and never executes.
- Records have a documented retention period sufficient for reconnect/retry behavior.

## 8. Access-token and session model

Access JWT claims:

```json
{
  "iss": "askrex-assistant",
  "aud": "askrex-mobile",
  "sub": "<canonical-user-id>",
  "sid": "<session-id>",
  "jti": "<token-id>",
  "iat": 0,
  "nbf": 0,
  "exp": 0
}
```

Display claims may be included for UI convenience, but the server ignores them for authorization and the client refreshes authoritative display state through `/mobile/auth/session`.

The existing `data/users.db` remains canonical. Add migration-managed tables for:

- `mobile_sessions`;
- `mobile_refresh_tokens`;
- `mobile_message_requests`.

Add an explicit user-active state if the existing users schema cannot represent disabled users. Existing users remain active after migration.

Refresh rotation uses token families. A consumed token cannot be accepted again. Reuse revokes the family/session and creates an audit event without logging the token.

## 9. Runtime integration

- Create one process-level service container/application factory.
- Reuse one unbound `Assistant` or a safe managed assistant instance whose request receives the validated `active_user_id` explicitly.
- Never bind a process-global mutable identity.
- Route chat and voice transcripts through `Assistant.generate_reply()` or `Assistant.stream_reply()`.
- Preserve per-user memory/history/cache boundaries already enforced by issue #303 work.
- Introduce a narrow event adapter only where required to expose structured tool/approval events. Do not bypass `ActionDispatcher`, `PolicyEngine`, or current tool execution.
- Use current server-side permission checks for every tool. A mobile permission list is presentation data, not authorization.

## 10. Local-network and deployment boundary

- Default bind: `127.0.0.1:8765`.
- `0.0.0.0` requires explicit CLI/config selection.
- Plain HTTP is allowed only on loopback for local development. A TLS-owned app rejects plaintext requests with `TLS_REQUIRED`.
- The supported remote topology is direct LAN pairing to the desktop-owned TLS endpoint; reverse proxies and hosted defaults are not part of S7. The desktop GUI/admin routes must never be exposed through the mobile gateway.
- Do not start the mobile API automatically merely because configuration exists unless `mobile_api.enabled` is explicitly true.
- Windows Firewall instructions must scope inbound access to the selected private network/profile and port.

## 11. Live voice

Real-time duplex voice is out of scope for the initial gateway. `live_voice` remains false. A future version may add authenticated WebSocket or WebRTC sessions for continuous audio, VAD, partial transcripts, interruption, mute, reconnect, and streamed audio, but no placeholder may claim those capabilities today.

## 12. Completion criteria

The workstream is complete only when:

- backend and mobile contracts match exactly;
- auth/session rotation and reuse tests pass;
- HTTP and WebSocket idempotency prevents duplicate execution;
- two-user isolation tests pass;
- chat uses the existing Rex runtime with explicit identity;
- voice and TTS are authenticated and validated;
- capabilities are truthful;
- full repository quality and security checks pass;
- local server smoke tests pass;
- physical-iPhone/LAN results are documented truthfully;
- no mock or scaffold is presented as implemented or verified.
