# AskRex Mobile Client Contract Audit

Status: Planning baseline for issue #323  
Audit date: 2026-07-14  
Backend base audited: `683b765ad6f4cc79771197f7390982487b0ac6c8`  
Mobile default branch audited: `main`  
Relevant mobile draft PRs: #3, #5, #7

## 1. Scope

This audit compares the current `Blueibear/AskRex` client with the current `Blueibear/AskRex-Assistant` backend. It identifies what is implemented, what is only client-side, what conflicts across branches/specifications, and the canonical decisions that implementation must follow.

Files reviewed include the mobile agent guide, endpoint constants, authentication service, authenticated HTTP client, WebSocket service, voice service, app/chat contexts, chat hook, package metadata, draft PRs #3/#5/#7, and the backend authentication, permissions, routes, Assistant, CLI, configuration, errors, rate limiting, request logging, policy, contracts, identity tests, and bridge paths.

## 2. Executive findings

1. The mobile app has substantial client code, but the matching authenticated gateway is not present as a complete, verified backend surface.
2. The backend already has the correct building blocks for users, bcrypt passwords, JWT signing, permissions, request IDs, rate limiting, error envelopes, and the canonical `Assistant`. These should be extended, not replaced.
3. Current backend JWT authentication is insufficient for mobile: it uses a 24-hour stateless token and lacks issuer/audience checks, device sessions, refresh rotation, revocation, token-family reuse detection, and a current-session endpoint.
4. Current mobile `main` still places a WebSocket access token in the URL and marks the socket connected before a server authentication acknowledgement.
5. Draft mobile PR #3 corrects the broad security direction, but its wire field names conflict with the master specification and mix `snake_case` request fields with `camelCase` response fields.
6. Current mobile `main` chat fallback sends `user_id`, defaults missing identity to `james`, performs an unauthenticated HTTP fallback, and converts network failure into a mock assistant response. Draft PR #3 removes these behaviors.
7. Current voice code uses authenticated HTTP, but its fallback can place TTS text in a URL and its development mock claims `verified`. Draft PR #5 fixes those client-side defects and establishes concrete upload limits and error behavior.
8. Draft PR #7 is developer demo-mode work. It is backend-independent and must not be merged into the gateway contract or used to prove a real integration.
9. The mobile app currently restores UI identity by decoding JWT payload fields locally. The canonical contract must instead use `/mobile/auth/session` for authoritative current display state.
10. The current backend GUI chat route calls a direct `LanguageModel` helper rather than the canonical `Assistant`; the mobile gateway must not copy that route architecture.

## 3. Mobile endpoint inventory

`constants/config.ts` currently declares the following gateway paths: `/mobile/auth/login`, `/mobile/auth/refresh`, `/mobile/auth/logout`, `/mobile/auth/logout-all`, `/mobile/status`, `/mobile/chat`, `/mobile/chat/stream`, `/mobile/voice/upload`, `/mobile/tts/playback`, and the home/notifications/approvals/tasks/workflows/audit/settings paths.

Required additions are:

```text
GET /mobile/auth/session
GET /mobile/capabilities
```

The home, notifications, approvals, tasks, workflows, audit, and settings routes remain explicit 501 scaffolds until real ownership, permission, persistence, and tests exist.

The default mobile URL is currently `https://askrex.app`. Local testing must require an explicit local server URL; the app must not silently switch production traffic to an insecure LAN URL.

## 4. Authentication client audit

### Implemented client behavior

`services/authService.ts` already provides access/refresh token storage, access-token expiry tracking, login, refresh, logout, logout-all, session restoration, SecureStore on native platforms, and an AsyncStorage fallback for web/development.

`services/apiClient.ts` already provides Bearer header injection, pre-emptive refresh, one refresh-and-retry cycle after HTTP 401, timeout handling, and centralized auth-failure signaling.

### Required corrections

1. Replace locally decoded JWT authority during restoration with `GET /mobile/auth/session` after access-token validation/refresh.
2. Client-side JWT decoding may remain only as a non-authoritative display optimization while offline.
3. Error parsing must support `body.error.message`, `body.error.code`, `body.error.retryable`, and `body.error.request_id`.
4. Login must send stable random device metadata for per-device sessions.
5. Logout-all requires a valid authenticated session or explicit reauthentication; it must not rely on a knowingly expired access token.
6. AsyncStorage fallback is development/web-only and not production-equivalent to iOS SecureStore.
7. Store an app-generated stable random `device_id` in SecureStore; do not use advertising identifiers or hardware serials.

## 5. WebSocket audit

### Current `main`

The default-branch service refreshes before connection, appends the token as `?token=`, reports connected on `onopen`, has no first-frame acknowledgement, has no stable message/conversation IDs or replay idempotency, and treats unparseable server text as a token event. These behaviors are incompatible with the required boundary.

### Draft PR #3

Draft PR #3 improves the client by removing tokens from the URL, sending a first-frame authentication message, waiting for `auth_ok`, removing `user_id`, adding stable IDs/timestamps, retaining unacknowledged messages for replay, authenticating HTTP fallback, adding an idempotency header, and rejecting malformed streaming payloads.

### Contract conflicts

| Concern | Draft PR #3 | Canonical decision |
|---|---|---|
| Auth frame type | `authenticate` | `auth` |
| Client metadata | flat protocol/client fields | nested `client` object |
| Auth session | `sessionId` | `session_id` |
| Ack ID | `messageId` | `message_id` |
| Event ID | `messageId` | `message_id` |
| Approval ID | `approvalId` | `approval_id` |
| Risk | `riskLevel` | `risk_level` |
| Full response | `fullContent` | `full_content` |
| Rex status | `rexStatus` | `rex_status` |

All network fields use `snake_case`. TypeScript may convert at a UI boundary, but transport types should match the wire contract directly.

Additional corrections:

- Generate IDs with `expo-crypto`, not `Math.random()` plus timestamps.
- Persist a conversation ID only when product behavior requires continuity.
- Distinguish queued locally from accepted by the backend.
- Replayed messages retain the same ID and request body.
- Close `4401` triggers refresh/re-authentication; `4403` is not an automatic reconnect loop.
- Use `4408` for authentication timeout and `4429` for connection rate limiting.

## 6. Chat hook audit

Current `main` sends `user_id`, falls back to `james`, uses unauthenticated HTTP streaming, omits stable IDs, and converts backend/network failure into a mock assistant response. These are authorization and truthfulness defects.

Draft PR #3 removes client identity, adds Bearer authentication and an idempotency key, creates stable IDs, and reports failed requests as failed.

Required follow-up:

- use cryptographically strong IDs;
- adopt canonical snake_case incoming events;
- parse the shared nested error envelope;
- label normal generated conversation output `completed`, not `verified`;
- preserve backend tool/action statuses without upgrading them;
- use `/mobile/chat` for non-streaming and `/mobile/chat/stream` for SSE.

## 7. Voice client audit

Current voice code uses authenticated HTTP, but can fall back to a GET URL containing TTS text, has a development mock that claims `verified`, and has incomplete local validation/request diagnostics.

Draft PR #5 establishes useful requirements:

- 15 MiB maximum audio size;
- M4A/MP4/AAC/MP3/WAV allowlist;
- local empty/size/type checks;
- 30-second upload and 20-second TTS timeouts;
- cancellation support;
- structured voice errors with code/status/request ID/retryability;
- protected TTS as JSON URL or base64;
- no text in a TTS query string;
- development mocks return `attempted`, not `verified`.

Backend/client alignment:

- Backend revalidates size, duration, signature/container, and decodability.
- Playback TTS delivery is a JSON inline data URI: `audio_url` carries the MIME type and the audio together. Upload TTS delivery is bare base64 in `ttsBase64` with no MIME label.
- Response keys are `request_id`, `transcript`, `response`, `status`, `toolUsed`, and optional `ttsBase64` on upload; `request_id`, `audio_url`, `voice`, and `requested_voice` on playback. The draft keys `tts_base64`, `tts_mime_type`, `tool_used`, `audio_base64`, and `mime_type` are superseded — see `docs/voice/S35_MOBILE_GATEWAY_SPEECH_CONTRACT.md`.
- Client accepts a backend URL only when protected/short-lived and from an allowed origin.
- Backend decides available voice IDs.
- Missing real transcription returns `BACKEND_UNAVAILABLE`, not a mock transcript.

## 8. Backend reuse inventory

| Requirement | Existing canonical component | Required extension |
|---|---|---|
| Users/passwords | `rex.auth`, `users.db`, bcrypt | schema migration, active state, safe CLI |
| Identity | `rex.identity.validate_user_id` | validate after token and before private access |
| Permissions | `rex.permissions` | live authorization for every request/tool |
| JWT | `rex.auth`, PyJWT | short-lived issuer/audience/session tokens |
| Refresh/session | none sufficient | session and hashed token-family tables |
| Assistant | `rex.assistant.Assistant` | explicit `active_user_id` adapter |
| Memory/history/cache | issue #303 hardening | mobile transport isolation tests |
| Policy/risk | `rex.policy_engine`, contracts | truthful decisions/events; no bypass |
| Approvals | existing workflow/contracts | expose only when ownership/challenge is real |
| Errors | `rex.http_errors` | mobile codes and retryability |
| Request IDs | `rex.request_logging` | response headers and WS IDs |
| Rate limiting | `rex.rate_limiter` | route-specific sensitive limits |
| CLI | `rex.cli`, `rex.commands/` | `mobile-api` and safe mobile-user creation |
| Config | `rex.config` | typed `MobileApiConfig` |
| Chat | `Assistant.stream_reply()` | SSE/WS adapter and idempotency |
| STT | existing Whisper/voice pipeline | file decode/transcribe adapter |
| TTS | existing providers/voice selection | authenticated base64 adapter |
| Home Assistant | current HA bridge/tools | false capability until mobile path verified |

## 9. Backend defects that must not be copied

1. `/api/auth/login` returns one stateless `token`, not the mobile token-pair contract.
2. `/api/auth/logout` only tells the client to discard a token and does not revoke a session.
3. `_require_auth()` validates only the old token and returns old top-level errors.
4. GUI `/api/chat/send` uses a direct LLM helper and bypasses canonical Assistant orchestration.
5. Generic rate limiting is per IP with in-memory storage; sensitive routes need explicit limits.
6. Request logging assigns IDs but does not guarantee the mobile response header/error fields on every path.

## 10. PR #7 treatment

Developer demo mode remains separate. It must be labeled, must never silently activate after production auth/network failure, must never prove backend integration, and should be reviewed independently.

## 11. Canonical decisions

1. Flask remains the HTTP framework.
2. Use a Flask-compatible WebSocket extension in the same API runtime, validated on Windows/Python 3.11.
3. Existing `users.db` and permissions remain canonical.
4. Access tokens default to 15 minutes; refresh tokens to 30 days.
5. Refresh tokens are opaque, hashed, rotated, and grouped into revocable families.
6. `/mobile/auth/session` is authoritative for restored user display state.
7. WebSocket first frame type is `auth`.
8. All wire fields use `snake_case`.
9. Tokens never appear in WebSocket URLs.
10. `(user_id, message_id)` is the cross-transport idempotency key.
11. Chat uses canonical Assistant methods with explicit identity.
12. Initial TTS delivery uses authenticated JSON base64 audio.
13. Unsupported domains return 501 and false capabilities.
14. Normal conversational output is `completed`, not `verified`.
15. Mobile draft PRs #3/#5 must be updated to this contract before being called integrated.

## 12. Remaining real-device questions

Repository inspection cannot prove iPhone-to-Windows LAN reachability, Windows Firewall behavior, SecureStore behavior in a development build, physical recording MIME/container output, background/reconnect behavior, base64 playback, local Whisper/TTS availability, or real Home Assistant completion/readback. These require later physical-device smoke testing and truthful recording of results.
