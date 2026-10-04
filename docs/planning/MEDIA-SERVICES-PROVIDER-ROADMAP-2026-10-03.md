# Music services: Apple Music and other provider implementation contract

Status: gap analysis and proposed dependent stories; **not** a claim of verified account connections. The canonical `rex.media` account/target registry, MediaAccountStore, output routing and action lifecycle belong to US-121 and US-122 and should be reused. No second player/room/account authority.

## Existing code and real gap

`rex.media.adapters` supports Home Assistant-backed media controls and a Music Assistant playback adapter; `rex.integrations.music_assistant` contains a local HTTP client, and Plex has its own adapter. The August US-121 media contract intentionally supplies only Apple Music-capable provider/account metadata; it does not implement Apple Music authorization or prove live playback. Music Assistant can expose separately connected streaming music sources, but that is not equivalent to a native Rex MusicKit login or official Apple-supported music streaming.

## Implementation decisions

### Preferred official Apple Music path

- Integrate MusicKit on Apple-supported app surfaces, with explicit per-user Apple authorization and subscription capability checks, using the existing user-bound MediaAccountStore and credential vault.
- A developer token is required for direct Apple Music API calls; developer enrollment, signing keys and approvals are operator-owned. Apple Music's user-library requests additionally require a Music User Token or platform-managed MusicKit token. Never create fake tokens or copy user's Apple ID/password into Rex.
- Separate catalog search, user library, playlist mutations and actual playback capabilities by platform. The Apple Music API alone must not be treated as a general unrestricted audio-stream endpoint for arbitrary Windows speakers.
- On iPhone/macOS, use the supported native MusicKit playback API when authorized. For an Electron/Windows UI, investigate official MusicKit-on-the-Web support and its limitations in the packaged app before promising direct desktop playback.
- All outputs and target choices still follow Rex's per-user permissions, trusted request-origin speaker, safe fallback and independently observed playback state. Denied/expired subscription, unavailable player and unsupported transfer must be reported truthfully.

### Optional Music Assistant integration

Music Assistant currently documents streaming sources for Apple Music and Spotify. The Rex Music Assistant adapter can issue playback commands to an already-configured server, subject to its actual supported API, target health, and observed result. Apple Music streaming through third-party Music Assistant is **not official Apple playback**; require opt-in and accurately label the limitations. Never scrape cookies, intercept personal credentials, or silently activate unofficial streaming. For an owner who expressly chooses that setup, distinguish Rex-to-Music-Assistant transport verification from Music Assistant-to-Apple playback verification.

### Other providers

Use the same media-provider and per-user account interfaces for Spotify and further licensed services, but implement a service only after verifying its currently available developer scopes, subscription requirements, supported players, API use restrictions, and delivery-state semantics. Prefer maintained, official playback SDKs or native installed apps where available. Third-party servers such as Music Assistant may be optional capability providers, never identity, account-permission, or verification authorities for Rex.

## Proposed post-PR-433 user-story slices

- MEDIA-001: inventory and test the current Plex/Home Assistant/Music Assistant adapters and speaker playback using live user-authorized devices; establish the independent playback-state verification contract.
- MEDIA-002: add an explicit Apple Music provider capability matrix and secure developer/user-token lifecycle, with mocked authorization failures and isolated per-user accounts (no live enrollment required for code review).
- MEDIA-003: implement official platform-supported Apple Music search/library and player controls; prove real user authorization and subscription capability on an authorized supported device before marking verified.
- MEDIA-004: implement Apple Music desktop/web integration only for documented supported capabilities; separately test cross-platform speaker handoff, expired token and unavailable playback, and do not promise unsupported streaming.
- MEDIA-005: expose already-configured Music Assistant music sources (including optional Apple Music/Spotify) through Rex's current adapter; check source reachability, API method support, routing, action receipt and actual player state.
- MEDIA-006: add direct Spotify/other service adapters where their reviewed SDK/API terms and user requirements permit, with user-bound auth/revocation, approved capabilities, UI status, and live playback evidence.
- MEDIA-007: test multiple household accounts, unknown speaker fail-closed behavior, explicit output and group routing, privacy isolation, service outages, zero false-success playback, cross-platform mobile/desktop consistency, and accurate provider labels.

These slices belong after the core security/CI and release-readiness work. Add concrete accepted stories to `PRD-production-readiness.md` through normal role ownership and review; never rewrite completed US-121/122 or silently elevate unofficial streaming to a supported release requirement.

## External references checked 2026-10-03

- Apple developer-token rules: https://developer.apple.com/documentation/AppleMusicAPI/generating-developer-tokens
- Apple Music user authorization: https://developer.apple.com/documentation/applemusicapi/user-authentication-for-musickit
- MusicKit native framework: https://developer.apple.com/documentation/musickit
- Music Assistant Apple Music source and unofficial-playback warning: https://www.music-assistant.io/music-providers/apple-music/
- Music Assistant Spotify account and playback engine requirements: https://www.music-assistant.io/music-providers/spotify/
