# AskRex Speech Architecture Upgrade: Provider-Neutral SpeechRouter + VoiceStudio

Status: Approved work / implementation queued
Date: 2026-09-13
Owner: backend primary, mobile participating
External dependency: `debpalash/VoiceStudio` (reference/service only; do not modify upstream)

## Objective

Evolve the existing AskRex voice stack into a provider-neutral speech architecture without rewriting working voice behavior. AskRex remains the assistant, orchestrator, security authority, and owner of the user experience. VoiceStudio becomes one selectable speech provider behind AskRex-owned STT/TTS contracts.

The current AskRex worktrees are authoritative. Inspect current code before assuming any file name, class name, or VoiceStudio endpoint in this document still matches reality.

## Repository boundaries

- Backend/desktop: `Blueibear/AskRex-Assistant` owns the canonical SpeechRouter, provider contracts, policy, desktop voice path, TurnEngine integration, configuration, security, and mobile gateway integration.
- Mobile: `Blueibear/AskRex` continues to capture/upload/play audio through the authenticated AskRex gateway. It must not call VoiceStudio directly.
- VoiceStudio: `debpalash/VoiceStudio` is an external service/reference implementation. Consume it through API/service boundaries; do not merge/copy its source into AskRex.

## Non-negotiable architecture

`wake word / desktop mic / mobile mic -> SpeechRouter -> STT -> Assistant/TurnEngine -> TTS -> playback`

Voice input must never bypass the canonical Assistant/TurnEngine. Speech providers may recognize or synthesize speech; they may not decide identity, authorization, permissions, confirmation state, action success, or verification state.

STT and TTS must be independently routable. A single `use_voicestudio=true` switch is not the target architecture. Mixed configurations such as VoiceStudio STT + cloud TTS, cloud STT + VoiceStudio TTS, or all-local speech with a cloud LLM must remain possible.
## Provider and policy requirements

Canonical contracts should preserve/evolve existing abstractions such as `SpeechToTextAdapter`, `TextToSpeechAdapter`, and `MobileApiServices` rather than create a parallel voice system.

Provider families must support current native providers, VoiceStudio, OpenAI-compatible/local endpoints, explicitly configured cloud providers, and future providers. Provider selection for STT and TTS is independent from LLM/model routing.

Policy must support the structure needed for Automatic, Prefer Local, Local Only, Prefer Cloud, and Custom modes. The first implementation may use explicit provider selection and ordered fallback; do not build an elaborate optimizer yet.

**Privacy rule:** cloud fallback may never happen silently. `Local Only` must fail after permitted local providers are exhausted. Uploading audio/transcripts to a cloud provider requires a policy that explicitly permits cloud processing; enforce this in routing/policy code, not only UI copy.

AskRex-owned voice identities such as `majel`, `james`, and `cole` remain canonical. Providers resolve those aliases into provider-specific voice/profile IDs. Do not spread VoiceStudio/OpenAI/ElevenLabs IDs throughout unrelated application code.

## VoiceStudio integration boundary

Before implementing the client, inspect the current VoiceStudio repository and verify its current APIs, license, supported engines, health/error behavior, authentication/security controls, streaming behavior, and GPU/model lifecycle. Previously observed OpenAI-compatible routes included `/v1/audio/speech`, `/v1/audio/transcriptions`, `/v1/audio/transcriptions/stream`, and `/v1/audio/voices`; treat those as hypotheses until verified.

Prefer VoiceStudio on loopback only (for example `127.0.0.1`) initially. Do not expose it directly to the iPhone, LAN, or Internet without a separate explicit requirement and security design.

Keep dependency and GPU ownership separated. If VoiceStudio owns an STT/TTS model, it owns that speech model's dependencies and speech-specific GPU/VRAM lifecycle. AskRex should not import VoiceStudio engine internals merely to avoid HTTP/WebSocket calls.

Do not delete existing Whisper/XTTS/edge-tts/pyttsx3/rex-speak-api behavior during the first migration. Adapt existing providers into the canonical provider contracts first, add VoiceStudio, validate it, and preserve straightforward rollback.
## Incremental implementation phases

1. **Formalize provider contracts.** Confirm the current canonical STT/TTS seams and evolve them only as needed.
2. **Represent existing providers.** Make current Whisper/native STT and XTTS/edge-tts/pyttsx3 equivalents implementations of those contracts without regressions.
3. **Add VoiceStudio providers.** Implement provider configuration, endpoint/base URL, health/availability, bounded timeouts/responses, voice/profile selection, truthful errors, supported cancellation, and local-only defaults.
4. **Canonical routing.** Route desktop voice and mobile-gateway speech through the same provider-resolution layer wherever practical; do not duplicate selection logic by surface.
5. **Policy structure.** Support explicit STT provider, explicit TTS provider, local-only enforcement, provider availability, and ordered fallback only where policy permits.
6. **Voice aliases.** Formalize AskRex-owned voice identities and provider mappings.
7. **Reliability validation.** Compare VoiceStudio with existing providers for transcription quality, STT/TTS latency, cold/warm behavior, GPU/VRAM/CPU behavior, startup/unavailable/timeout/malformed-response/cancellation cases, provider recovery, fallback, voice selection, desktop flow, mobile flow, and Local Only enforcement.

## Minimum end-to-end proof

Desktop: `audio -> VoiceStudio STT -> Assistant/TurnEngine -> VoiceStudio TTS -> speaker`.

Mobile: `iPhone recording -> authenticated AskRex gateway -> SpeechRouter -> selected STT -> Assistant/TurnEngine -> selected TTS -> AskRex gateway -> iPhone playback`.

The mobile gateway remains authoritative for authentication, pairing, authorization scopes, TLS, validation, rate limiting, user/device identity, action verification, idempotency, and privacy policy. The mobile app ideally remains unaware of which speech provider handled the request.

## Security and privacy

Treat provider transcripts as untrusted user input and pass them through the same canonical policy/authorization path as typed input. Protect credentials, audio, transcripts, conversation content, and voice samples from inappropriate logging. Use existing Rex secret/credential handling.

No provider may establish user identity, household identity, permissions, confirmation state, device trust, tool authority, action success, or verification status. VoiceStudio must never create an alternate route around TurnEngine.
## Testing and acceptance

Use TDD/regression-safe development. At minimum test native STT/TTS preservation, VoiceStudio STT/TTS, independent STT/TTS selection, provider health/unavailable/timeout/malformed-response behavior, fallback ordering, cloud-permitted vs cloud-prohibited policies, Local Only, voice alias resolution, mobile authentication before speech processing, cancellation, stale-output rejection, logging/privacy behavior, and truthful failures.

Retain existing providers until VoiceStudio has demonstrated sufficient reliability. Physical audio/iPhone evidence remains a human/testing gate where automation cannot prove the path.

## Explicit non-goals for the initial integration

Do not replace AskRex with VoiceStudio; merge/fork VoiceStudio; expose VoiceStudio directly to mobile; bypass TurnEngine/mobile authentication; remove existing providers; hard-code VoiceStudio as the only provider; couple AskRex voice aliases to provider IDs; silently upload audio to cloud; implement full-duplex real-time Voice Mode; duplicate routing across desktop/mobile; or redesign unrelated AskRex systems.

Future real-time work may build on streaming STT, but VAD, turn detection, barge-in, TTS interruption, echo cancellation, playback/mic synchronization, cancellation propagation, stale-turn rejection, and conversational arbitration remain AskRex-owned future work.

## Workflow instruction

Use the shared AskRex coordination protocol and multi-agent orchestrator. Before implementation, inspect both current AskRex worktrees and the current VoiceStudio upstream, read applicable `AGENTS.md`/repo instructions, identify existing canonical abstractions, and produce a concise implementation plan against the actual tree. Cross-repo API/schema changes must be communicated through the shared coordination mailbox before the peer role depends on them.

Favor the smallest clean extension of the existing architecture. The goal is a local-first, provider-neutral, privacy-aware, fallback-capable, user-configurable speech layer in which VoiceStudio remains replaceable and native/cloud alternatives remain possible.
