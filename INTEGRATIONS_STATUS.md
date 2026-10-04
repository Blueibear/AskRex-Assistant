# AskRex Integration Status

This is the canonical integration release contract. Runtime status uses the same evidence vocabulary in `rex/integration_state.py`, `rex integrations`, `rex doctor`, the capability API, and Electron:

`unavailable`, `unconfigured`, `configured`, `reachable`, `authenticated`, `degraded`, `read_only`, `write_capable`, `write_tested`, `verified`.

Credentials alone mean `configured`. They never mean connected, authenticated, write-capable, or verified.

For the production-facing mapping from backend/docs capabilities to Electron visibility, configuration status, permissions, risk, health evidence, verification support, and future registry adapters, see [docs/CAPABILITY-PARITY.md](docs/CAPABILITY-PARITY.md). Capabilities that are backend-only remain explicitly developer-only rather than being implied production-ready.

| Integration | Product tier | Current contract |
|---|---|---|
| Home Assistant | Supported, credential-gated | Read access requires live authentication. Mutations use the unified policy service; sensitive actions require action-bound confirmation and all writes return verified, attempted-but-unverified, denied, or failed. Live-device transitions remain externally verified. |
| Email | Partial | IMAP/SMTP backend paths exist. GUI email sending is unavailable; it creates a draft that can be copied to a mail client. Outlook Graph OAuth is unavailable. |
| Calendar | Partial / read-only by backend | ICS reads exist. Credentials are configured-only until authenticated. Provider writes and Outlook Graph OAuth are unavailable. |
| SMS / Phone | Experimental | Twilio paths require complete credentials. Status checks do not claim delivery or calling success; live delivery is externally verified. |
| Web search | Partial | Provider selection exists. DuckDuckGo is a no-key fallback when enabled; keyed providers require usable credentials. Explicit current-news requests are routed through Web Search before the LLM and fail closed if no verified search result is returned. Configuration still does not prove current network reachability. |
| OpenAI / OpenRouter / Ollama | Supported provider options | A key or URL is configuration evidence only. Provider availability depends on a live request. |
| MQTT / Telegram / Push | Experimental | Visible and configurable, but live broker/provider authentication and delivery are externally verified. |
| OpenClaw gateway | Experimental and optional | Disabled by default. URL/token are configuration evidence; the GUI health check can establish `reachable`, but authentication and tool capability remain unproven. The external gateway is not required by the packaged app. |
| Browser automation / Windows control | Experimental | Environment-sensitive and permission-gated. Not part of the release-critical end-user path. |
| WordPress / WooCommerce | Experimental operator tools | WordPress is monitoring-oriented. WooCommerce reads and approval-gated writes exist; live stores remain externally verified. |
| Plex / smart speakers | Experimental | Hardware/service-dependent and not release-verified by automated tests. |
| Apple Music / Spotify media sources | Planned native linking / optional third-party Music Assistant source | The shared `rex.media` account metadata, target/routing and existing Music Assistant client are implemented, but native Apple Music user authorization, an official platform playback adapter and live provider/device evidence are not. Optional third-party streaming via Music Assistant is separate and requires explicit informed setup; see `docs/planning/MEDIA-SERVICES-PROVIDER-ROADMAP-2026-10-03.md`. |
| Mobile API gateway | Beta backend, credential-gated | `python -m rex mobile-api` serves the companion mobile client: JWT auth (`REX_JWT_SECRET`, fails closed), per-device sessions, localhost by default, rate-limited. Chat, streaming, voice upload, and TTS are implemented with truthful capability reporting; Home Assistant, notifications, approvals, tasks, workflows, audit, settings, and live duplex voice remain explicit not-implemented scaffolds with false capabilities. Physical-iPhone/LAN validation is externally verified and still outstanding. |

The Electron Integrations page displays evidence state rather than a generic Connected badge. `rex integrations` provides the CLI inventory; `rex doctor` includes the same configuration evidence. No status probe sends email, calendar writes, SMS, phone calls, or provider notifications.
