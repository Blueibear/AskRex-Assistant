#!/usr/bin/env python3
"""Verify one copy of the S35 mobile-gateway speech contract fixture.

``tests/mobile_api/contract_vectors.json`` is the only artifact the backend
repository and the AskRex mobile repository are expected to hold identically.
This script is the portable form of the five-step synchronization procedure in
``docs/voice/S35_MOBILE_GATEWAY_SPEECH_CONTRACT.md``, so either repository can
verify its own copy without a hand-carried whole-file digest.

Usage:
    python scripts/check_speech_contract_vectors.py
    python scripts/check_speech_contract_vectors.py --vectors <path>
    python scripts/check_speech_contract_vectors.py --vectors <path> --no-doc

The fixture path may also come from ``CANONICAL_SPEECH_VECTORS_PATH``. Use
``--no-doc`` in a checkout that holds only the fixture (the mobile repository
keeps its copy at ``tests/contract/contract_vectors.json`` and has no copy of
the contract document).

Exit status is 0 when every check passes and 1 otherwise. The report always
prints the recomputed digests, so a mismatch names the true value instead of
requiring a second tool.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import wave
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VECTORS_PATH = REPO_ROOT / "tests" / "mobile_api" / "contract_vectors.json"
DEFAULT_DOC_PATH = REPO_ROOT / "docs" / "voice" / "S35_MOBILE_GATEWAY_SPEECH_CONTRACT.md"
VECTORS_PATH_ENV = "CANONICAL_SPEECH_VECTORS_PATH"

# The canonical wire contract. These constants are pinned against the contract
# document and the fixture itself by
# tests/mobile_api/test_contract_vectors.py::TestPortableSynchronizationChecker,
# so this checker cannot drift into being a third contract.
WIRE_CONTRACT_VERSION = "2026-09-16.s35.1"
UPLOAD_KEYS = frozenset({"request_id", "transcript", "response", "status", "toolUsed", "ttsBase64"})
PLAYBACK_KEYS = frozenset({"request_id", "audio_url", "voice", "requested_voice"})
UPLOAD_STATUS = "completed"
PLAYBACK_DATA_URI_PREFIX = "data:audio/wav;base64,"
AUDIO_VECTOR_BASE64 = "UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQQAAACAgICA"
AUDIO_VECTOR_SHA256 = "93bef78ec2fb0694560ab8a8cb26c1d914799f6a11db73335d3e0f74ab3fc2ae"
AUDIO_VECTOR_BYTES = 48

# Shapes that appeared in earlier S35 mailbox traffic and are superseded.
SUPERSEDED_FIELDS = ("tool_used", "tts_base64", "tts_mime_type", "audio_base64", "mime_type")

# Identity lines the contract document must keep equal to the constants above.
DOCUMENTED_IDENTITY = (
    ("wire_contract_version", WIRE_CONTRACT_VERSION),
    ("audio_vector_base64", AUDIO_VECTOR_BASE64),
    ("audio_vector_sha256", AUDIO_VECTOR_SHA256),
    ("audio_vector_bytes", str(AUDIO_VECTOR_BYTES)),
)


def canonical_json_text(parsed: Any) -> str:
    """Return the one canonical serialization a synchronized copy must equal."""
    return json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"


def doc_identity(doc_text: str, key: str) -> str | None:
    """Read one value from the document's machine-readable identity block."""
    match = re.search(rf"^{re.escape(key)}:[ \t]*(\S+)$", doc_text, re.MULTILINE)
    return match.group(1) if match else None


def audio_vector_sha256(raw: bytes) -> str | None:
    """Recompute the decoded audio-vector digest from a fixture's raw bytes."""
    try:
        parsed = json.loads(raw.decode("utf-8"))
        encoded = parsed["http"]["voice_response"]["ttsBase64"]
        return hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest()
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        return None


def file_digests(raw: bytes) -> dict[str, str]:
    """Return the raw and LF-normalized whole-file digests.

    The LF-normalized digest is the only whole-file digest worth comparing
    across checkouts; see "Fixture file identity" in the contract document.
    """
    return {
        "raw": hashlib.sha256(raw).hexdigest(),
        "lf_normalized": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
    }


def _check_contract_version(parsed: Any) -> list[str]:
    actual = parsed.get("contract_version") if isinstance(parsed, dict) else None
    if actual != WIRE_CONTRACT_VERSION:
        return [f"contract_version is {actual!r}; expected {WIRE_CONTRACT_VERSION!r}"]
    return []


def _check_shape(name: str, shape: Any, expected: frozenset[str]) -> list[str]:
    if not isinstance(shape, dict):
        return [f"fixture has no 'http.{name}' object"]
    failures: list[str] = []
    if set(shape) != set(expected):
        failures.append(f"http.{name} keys are {sorted(shape)}; expected {sorted(expected)}")
    for field in SUPERSEDED_FIELDS:
        if field in shape:
            failures.append(f"http.{name} carries superseded field {field!r}")
    return failures


def _check_field_sets(parsed: Any) -> list[str]:
    http = parsed.get("http") if isinstance(parsed, dict) else None
    if not isinstance(http, dict):
        return ["fixture has no 'http' object"]
    failures = _check_shape("voice_response", http.get("voice_response"), UPLOAD_KEYS)
    failures += _check_shape("tts_response", http.get("tts_response"), PLAYBACK_KEYS)
    upload = http.get("voice_response")
    if isinstance(upload, dict) and upload.get("status") != UPLOAD_STATUS:
        failures.append(
            f"http.voice_response.status is {upload.get('status')!r}; "
            f"expected the conversational {UPLOAD_STATUS!r}"
        )
    return failures


def _check_decodable_wav(decoded: bytes) -> list[str]:
    if decoded[:4] != b"RIFF" or decoded[8:12] != b"WAVE":
        return ["the audio vector is not a RIFF/WAVE container (placeholder or base64 text?)"]
    try:
        with wave.open(io.BytesIO(decoded), "rb") as wav_file:
            frames = wav_file.getnframes()
            if frames <= 0:
                return ["the audio vector contains no frames"]
            if not wav_file.readframes(frames):
                return ["the audio vector's frames could not be read"]
    except wave.Error as exc:
        return [f"the audio vector is not a decodable WAV: {exc}"]
    return []


def _check_audio_vector(parsed: Any) -> list[str]:
    http = parsed.get("http") if isinstance(parsed, dict) else {}
    http = http if isinstance(http, dict) else {}
    upload = http.get("voice_response") if isinstance(http.get("voice_response"), dict) else {}
    playback = http.get("tts_response") if isinstance(http.get("tts_response"), dict) else {}

    failures: list[str] = []
    encoded = upload.get("ttsBase64")
    if not isinstance(encoded, str):
        failures.append("http.voice_response.ttsBase64 is missing or is not a string")
        encoded = None
    else:
        if encoded.startswith("data:") or ";base64," in encoded:
            failures.append("http.voice_response.ttsBase64 must be bare base64, not a data URI")
        if encoded != AUDIO_VECTOR_BASE64:
            failures.append("http.voice_response.ttsBase64 is not the canonical audio vector")

    url = playback.get("audio_url")
    if not isinstance(url, str) or not url.startswith(PLAYBACK_DATA_URI_PREFIX):
        failures.append(f"http.tts_response.audio_url must start with {PLAYBACK_DATA_URI_PREFIX!r}")
    elif encoded is not None and url[len(PLAYBACK_DATA_URI_PREFIX) :] != encoded:
        failures.append("upload ttsBase64 and playback audio_url must carry the same audio vector")

    if encoded is None:
        return failures
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        failures.append(f"the audio vector is not valid base64: {exc}")
        return failures
    if len(decoded) != AUDIO_VECTOR_BYTES:
        failures.append(
            f"the audio vector decodes to {len(decoded)} bytes; expected {AUDIO_VECTOR_BYTES}"
        )
    digest = hashlib.sha256(decoded).hexdigest()
    if digest != AUDIO_VECTOR_SHA256:
        failures.append(
            f"the decoded audio vector hashes to {digest}; expected {AUDIO_VECTOR_SHA256}"
        )
    return failures + _check_decodable_wav(decoded)


def _check_file_bytes(raw: bytes, text: str, parsed: Any) -> list[str]:
    failures: list[str] = []
    if b"\r" in raw:
        failures.append("the fixture bytes contain CR; a synchronized copy must materialize LF")
    if not raw.endswith(b"\n"):
        failures.append("the fixture must end with exactly one newline")
    elif raw.endswith(b"\n\n"):
        failures.append("the fixture must not end with a blank line")
    if text != canonical_json_text(parsed):
        failures.append(
            "the fixture is not canonical "
            "json.dumps(obj, indent=2, ensure_ascii=False) + '\\n' text"
        )
    return failures


def _check_document_agrees(doc_text: str) -> list[str]:
    failures: list[str] = []
    for key, expected in DOCUMENTED_IDENTITY:
        recorded = doc_identity(doc_text, key)
        if recorded is None:
            failures.append(f"the contract document is missing the '{key}' identity line")
        elif recorded != expected:
            failures.append(
                f"the contract document records {key} = {recorded}; expected {expected}"
            )
    return failures


def check_fixture(raw: bytes, doc_text: str | None = None) -> list[str]:
    """Return every reason *raw* is not the canonical fixture (empty == OK)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"the fixture is not valid UTF-8: {exc}"]
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return [f"the fixture is not valid JSON: {exc}"]

    failures = _check_contract_version(parsed)
    failures += _check_field_sets(parsed)
    failures += _check_audio_vector(parsed)
    failures += _check_file_bytes(raw, text, parsed)
    if doc_text is not None:
        failures += _check_document_agrees(doc_text)
    return failures


def _resolve_vectors_path(argument: str | None) -> Path:
    return Path(argument or os.environ.get(VECTORS_PATH_ENV) or DEFAULT_VECTORS_PATH)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a copy of the S35 mobile-gateway speech contract fixture."
    )
    parser.add_argument(
        "--vectors",
        default=None,
        help=f"path to contract_vectors.json (default: ${VECTORS_PATH_ENV} or the backend copy)",
    )
    parser.add_argument(
        "--doc",
        default=None,
        help="path to S35_MOBILE_GATEWAY_SPEECH_CONTRACT.md",
    )
    parser.add_argument(
        "--no-doc",
        action="store_true",
        help="skip the document cross-check (for a checkout that holds only the fixture)",
    )
    args = parser.parse_args(argv)

    vectors_path = _resolve_vectors_path(args.vectors)
    if not vectors_path.is_file():
        print(f"ERROR: contract fixture not found: {vectors_path}")
        return 1
    raw = vectors_path.read_bytes()

    doc_text: str | None = None
    if not args.no_doc:
        doc_path = Path(args.doc) if args.doc else DEFAULT_DOC_PATH
        if doc_path.is_file():
            doc_text = doc_path.read_text(encoding="utf-8")
        elif args.doc:
            print(f"ERROR: contract document not found: {doc_path}")
            return 1

    failures = check_fixture(raw, doc_text)
    digests = file_digests(raw)
    print(f"fixture: {vectors_path}")
    print(f"wire_contract_version: {WIRE_CONTRACT_VERSION}")
    print(f"audio_vector_sha256: {audio_vector_sha256(raw) or '<unreadable>'}")
    print(f"audio_vector_bytes: {AUDIO_VECTOR_BYTES}")
    print(f"file_sha256_raw: {digests['raw']}")
    print(f"file_sha256_lf_normalized: {digests['lf_normalized']}")
    if failures:
        print("FAIL: this copy is not the canonical S35 speech contract fixture:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("OK: this copy matches the canonical S35 speech contract fixture.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
