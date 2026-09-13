"""Provider-neutral speech (STT/TTS) routing layer (S35).

This package formalizes AskRex's speech provider contracts so that speech
recognition and synthesis can be routed independently across native
providers (existing Whisper/XTTS/edge-tts/pyttsx3 stack), VoiceStudio, and
future providers, without letting any provider bypass the canonical
Assistant/TurnEngine authority.

Submodules:

- ``contracts`` -- provider protocols and result types.
- ``policy`` -- explicit policy modes (Automatic, Prefer Local, Local Only,
  Prefer Cloud, Custom) and ordered-fallback resolution.
- ``aliases`` -- AskRex-owned voice identities (``majel``, ``james``,
  ``cole``) resolved to provider-specific voice IDs.
- ``providers`` -- concrete provider implementations (native, VoiceStudio).
- ``router`` -- ``SpeechRouter``, the single provider-resolution layer used
  by both the desktop voice pipeline and the authenticated mobile gateway.
- ``registry`` -- builds the default ``SpeechRouter`` from ``AppConfig``.
"""

from __future__ import annotations
