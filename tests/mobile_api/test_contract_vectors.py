"""Cross-repository contract-vector tests.

``contract_vectors.json`` is the shared wire contract for issue #323 — an
identical copy lives in the mobile repository (``tests/contract/``) and both
test suites validate against it, so field-casing drift, ``auth`` vs
``authenticate`` drift, missing required fields, token-in-URL regressions,
client ``user_id`` regressions, and fake ``verified`` statuses fail on
whichever side introduced them.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import wave
from pathlib import Path

import pytest

from tests.mobile_api.conftest import (
    auth_header,
    create_user,
    login_tokens,
    paired_login_tokens,
    parse_sse_events,
)

VECTORS_PATH = Path(__file__).parent / "contract_vectors.json"
_REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DOC_PATH = _REPO_ROOT / "docs" / "voice" / "S35_MOBILE_GATEWAY_SPEECH_CONTRACT.md"
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")
_CAMEL_CASE = re.compile(r"^[a-z][a-zA-Z0-9]*$")


@pytest.fixture(scope="module")
def vectors() -> dict:
    return json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract_doc() -> str:
    return CONTRACT_DOC_PATH.read_text(encoding="utf-8")


def _doc_identity(doc_text: str, key: str) -> str:
    """Read one value from the document's machine-readable identity block."""
    match = re.search(rf"^{re.escape(key)}:[ \t]*(\S+)$", doc_text, re.MULTILINE)
    assert match, f"{CONTRACT_DOC_PATH.name} is missing the '{key}' identity line"
    return match.group(1)


def _doc_json_examples(doc_text: str) -> list[dict]:
    """Parse every fenced ``json`` example in the contract document."""
    blocks = re.findall(r"^```json\n(.*?)^```$", doc_text, re.MULTILINE | re.DOTALL)
    assert blocks, f"{CONTRACT_DOC_PATH.name} declares no JSON response examples"
    return [json.loads(block) for block in blocks]


def _doc_canonical_source_paths(doc_text: str) -> list[str]:
    """Return the repository paths listed in the 'Canonical sources' table."""
    section = re.search(r"^## Canonical sources\n(.*?)^## ", doc_text, re.MULTILINE | re.DOTALL)
    assert section, f"{CONTRACT_DOC_PATH.name} is missing its 'Canonical sources' table"
    paths = re.findall(r"^\|[^|\n]+\|[ \t]*`([^`]+)`[ \t]*\|$", section.group(1), re.MULTILINE)
    assert paths, "the 'Canonical sources' table lists no paths"
    return paths


def _assert_snake_case_keys(value, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert _SNAKE_CASE.fullmatch(key), f"non-snake_case key {key!r} at {path}"
            _assert_snake_case_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_snake_case_keys(child, f"{path}[{index}]")


class TestVectorHygiene:
    def test_wire_keys_are_snake_case_except_documented_voice_upload_fields(self, vectors) -> None:
        """S35 preserves only the documented camelCase upload response fields."""
        http = dict(vectors["http"])
        voice_response = dict(http.pop("voice_response"))
        _assert_snake_case_keys(http)
        _assert_snake_case_keys(vectors["websocket"])
        assert set(voice_response) == {
            "request_id", "transcript", "response", "status", "toolUsed", "ttsBase64"
        }
        assert _CAMEL_CASE.fullmatch("toolUsed")
        assert _CAMEL_CASE.fullmatch("ttsBase64")

    def test_auth_frame_type_is_auth_not_authenticate(self, vectors) -> None:
        assert vectors["websocket"]["auth_frame"]["type"] == "auth"

    def test_ws_url_contains_no_token(self, vectors) -> None:
        url_path = vectors["websocket"]["url_path"]
        assert "?" not in url_path
        assert "token" not in url_path.lower()

    def test_chat_request_has_no_client_identity_fields(self, vectors) -> None:
        for shape in (vectors["http"]["chat_request"], vectors["websocket"]["chat_frame"]):
            for forbidden in ("user_id", "role", "permissions", "risk", "approval"):
                assert forbidden not in shape

    def test_normal_chat_status_is_completed(self, vectors) -> None:
        assert vectors["statuses"]["normal_chat"] == "completed"
        assert vectors["http"]["chat_response"]["status"] == "completed"
        assert vectors["websocket"]["message_done"]["status"] == "completed"

    def test_close_codes(self, vectors) -> None:
        from rex.mobile_api import websocket as ws

        codes = vectors["websocket"]["close_codes"]
        assert codes["unauthenticated"] == ws.CLOSE_UNAUTHENTICATED == 4401
        assert codes["forbidden"] == ws.CLOSE_FORBIDDEN == 4403
        assert codes["auth_timeout"] == ws.CLOSE_AUTH_TIMEOUT == 4408
        assert codes["rate_limited"] == ws.CLOSE_RATE_LIMITED == 4429

    def test_fixture_audio_vectors_are_valid_decodable_wav(self, vectors) -> None:
        """The fixture audio fields must be real decodable audio, not the
        literal placeholder text ``"<base64-audio>"``."""
        for pointer, field in (
            (vectors["http"]["voice_response"], "ttsBase64"),
        ):
            raw_value = pointer[field]
            assert raw_value != "<base64-audio>"
            decoded = base64.b64decode(raw_value, validate=True)
            assert decoded[:4] == b"RIFF"
            assert decoded[8:12] == b"WAVE"
            with wave.open(io.BytesIO(decoded), "rb") as wav_file:
                assert wav_file.getnframes() > 0
                assert wav_file.getnchannels() >= 1
                assert wav_file.getframerate() > 0
                assert wav_file.readframes(wav_file.getnframes())

    def test_playback_audio_url_embeds_the_wav_mime_and_bytes(self, vectors) -> None:
        url = vectors["http"]["tts_response"]["audio_url"]
        assert url.startswith("data:audio/wav;base64,")
        raw = base64.b64decode(url.split(",", 1)[1], validate=True)
        assert raw[:4] == b"RIFF"
        with wave.open(io.BytesIO(raw), "rb") as wav_file:
            assert wav_file.getnframes() > 0
            assert wav_file.readframes(wav_file.getnframes())


class TestCanonicalContractDocument:
    """``docs/voice/S35_MOBILE_GATEWAY_SPEECH_CONTRACT.md`` must stay equal to
    the fixture and the route serializers, so the two repositories cannot be
    pointed at two different wire contracts."""

    def test_document_records_the_fixture_audio_sha256(self, vectors, contract_doc) -> None:
        decoded = base64.b64decode(vectors["http"]["voice_response"]["ttsBase64"], validate=True)
        actual = hashlib.sha256(decoded).hexdigest()
        documented = _doc_identity(contract_doc, "audio_vector_sha256")
        assert actual == documented, (
            "The canonical contract document records the wrong fixture audio digest. "
            f"Recorded {documented}; the decoded fixture bytes hash to {actual}."
        )
        assert len(decoded) == int(_doc_identity(contract_doc, "audio_vector_bytes"))

    def test_document_records_the_fixture_audio_vector_verbatim(
        self, vectors, contract_doc
    ) -> None:
        documented = _doc_identity(contract_doc, "audio_vector_base64")
        assert vectors["http"]["voice_response"]["ttsBase64"] == documented

    def test_upload_and_playback_share_one_audio_vector(self, vectors) -> None:
        upload_audio = vectors["http"]["voice_response"]["ttsBase64"]
        playback_audio = vectors["http"]["tts_response"]["audio_url"].split(",", 1)[1]
        assert upload_audio == playback_audio

    def test_document_declares_the_fixture_contract_version(self, vectors, contract_doc) -> None:
        assert _doc_identity(contract_doc, "wire_contract_version") == vectors["contract_version"]

    def test_fixture_file_is_canonical_json_text(self) -> None:
        """A synchronized copy must be regenerable byte-for-byte.

        ``read_text`` normalizes newlines, so this assertion holds in CRLF and
        LF checkouts alike; ``.gitattributes`` pins the checked-out bytes to LF.
        """
        text = VECTORS_PATH.read_text(encoding="utf-8")
        canonical = json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"
        assert text == canonical

    def test_document_supersedes_the_snake_case_speech_fields(self, vectors, contract_doc) -> None:
        superseded_upload = ("tool_used", "tts_base64", "tts_mime_type")
        superseded_playback = ("audio_base64", "mime_type")
        for name in superseded_upload + superseded_playback:
            assert name in contract_doc, f"{name} must be listed as superseded"
            assert name not in vectors["http"]["voice_response"]
            assert name not in vectors["http"]["tts_response"]

    def test_fixture_upload_status_is_the_conversational_completed(self, vectors) -> None:
        from rex.mobile_api.chat import STATUS_COMPLETED

        assert vectors["http"]["voice_response"]["status"] == STATUS_COMPLETED == "completed"
        assert STATUS_COMPLETED not in ("attempted", "verified", "failed")

    def test_document_revision_identity_matches_the_prose_header(self, contract_doc) -> None:
        """One revision string, recorded once in prose and once machine-readably."""
        declared = _doc_identity(contract_doc, "document_revision")
        header = re.search(r"^Document revision: `([^`]+)`\.", contract_doc, re.MULTILINE)
        assert header, "the document must open with a 'Document revision:' line"
        assert header.group(1) == declared

    def test_document_json_examples_match_the_fixture_field_sets(
        self, vectors, contract_doc
    ) -> None:
        """The document's two response examples are the fixture's two shapes.

        This is the check that makes "one wire contract" mechanical: a prose
        edit that adds, drops, or re-cases a key in either example fails here
        instead of reaching the mobile repository as a third contract.
        """
        examples = _doc_json_examples(contract_doc)
        upload = [ex for ex in examples if "transcript" in ex]
        playback = [ex for ex in examples if "audio_url" in ex]
        assert len(upload) == 1, "expected exactly one documented upload response example"
        assert len(playback) == 1, "expected exactly one documented playback response example"
        assert set(upload[0]) == set(vectors["http"]["voice_response"])
        assert set(playback[0]) == set(vectors["http"]["tts_response"])
        assert upload[0]["status"] == vectors["http"]["voice_response"]["status"] == "completed"
        assert upload[0]["toolUsed"] is None

    def test_document_canonical_source_paths_exist(self, contract_doc) -> None:
        """Every canonical path the document names must be a real file here."""
        paths = _doc_canonical_source_paths(contract_doc)
        for relative in paths:
            assert (_REPO_ROOT / relative).is_file(), f"canonical source {relative} does not exist"
        assert "tests/mobile_api/contract_vectors.json" in paths
        assert "rex/mobile_api/routes/voice.py" in paths

    def test_fixture_playback_mime_is_the_adapter_mime_not_a_hand_written_string(
        self, vectors
    ) -> None:
        """The fixture's data-URI media type comes from the executable source."""
        from rex.mobile_api.voice import TextToSpeechAdapter

        url = vectors["http"]["tts_response"]["audio_url"]
        media_type = url.split(":", 1)[1].split(";", 1)[0]
        assert media_type == TextToSpeechAdapter(provider="xtts").mime_type() == "audio/wav"
        assert TextToSpeechAdapter(provider="edge-tts").mime_type() == "audio/mpeg"

    def test_fixture_end_of_line_is_pinned_to_lf(self) -> None:
        """``.gitattributes`` must keep both checkouts byte-identical."""
        attributes = (_REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        assert re.search(
            r"^tests/mobile_api/contract_vectors\.json[ \t]+text[ \t]+eol=lf[ \t]*$",
            attributes,
            re.MULTILINE,
        ), ".gitattributes must pin tests/mobile_api/contract_vectors.json to eol=lf"

    def test_document_states_upload_inline_tts_is_not_directly_playable(
        self, vectors, contract_doc
    ) -> None:
        """The upload vector must stay bare base64, and the document must say so."""
        tts_base64 = vectors["http"]["voice_response"]["ttsBase64"]
        assert not tts_base64.startswith("data:")
        assert ";base64," not in tts_base64
        assert "not directly playable" in contract_doc
        assert "TextToSpeechAdapter.mime_type()" in contract_doc
        assert "/mobile/tts/playback" in contract_doc


class TestEventBuilderConformance:
    """The backend event builders must produce exactly the vector shapes."""

    def test_token_event(self, vectors) -> None:
        from rex.mobile_api import events as mev

        built = mev.token_event("m", ".")
        assert set(built.keys()) == set(vectors["websocket"]["token"].keys())

    def test_message_done_event(self, vectors) -> None:
        from rex.mobile_api import events as mev

        built = mev.message_done_event("m", "c", "text")
        assert set(built.keys()) == set(vectors["websocket"]["message_done"].keys())
        assert built["status"] == "completed"

    def test_error_event(self, vectors) -> None:
        from rex.mobile_api import events as mev

        built = mev.error_event("BACKEND_UNAVAILABLE", "msg", message_id="m", retryable=True)
        assert set(built.keys()) == set(vectors["websocket"]["error"].keys())

    def test_ack_event(self, vectors) -> None:
        from rex.mobile_api import events as mev

        built = mev.ack_event("m", "2026-07-15T00:00:00+00:00")
        assert set(built.keys()) == set(vectors["websocket"]["ack"].keys())

    def test_auth_ok_event(self, vectors) -> None:
        from rex.mobile_api import events as mev

        user = dict(vectors["websocket"]["auth_ok"]["user"])
        built = mev.auth_ok_event("s", user)
        assert set(built.keys()) == set(vectors["websocket"]["auth_ok"].keys())

    def test_auth_error_event(self, vectors) -> None:
        from rex.mobile_api import events as mev

        built = mev.auth_error_event("AUTH_TOKEN_EXPIRED", "Access token expired.")
        assert set(built.keys()) == set(vectors["websocket"]["auth_error"].keys())

    def test_pong_event(self, vectors) -> None:
        from rex.mobile_api import events as mev

        built = mev.pong_event("2026-07-15T00:00:00+00:00")
        assert set(built.keys()) == set(vectors["websocket"]["pong"].keys())


class TestLiveResponseConformance:
    """Real backend responses carry exactly the documented required fields."""

    def test_login_and_refresh_and_session(self, client, vectors) -> None:
        create_user("james", "pw-123456")
        login_body = login_tokens(client, "james", "pw-123456")
        assert set(login_body.keys()) == set(vectors["http"]["login_response"].keys())
        assert set(login_body["user"].keys()) == set(
            vectors["http"]["login_response"]["user"].keys()
        )
        assert login_body["token_type"] == "Bearer"

        refresh = client.post(
            "/mobile/auth/refresh", json={"refresh_token": login_body["refresh_token"]}
        )
        assert refresh.status_code == 200
        assert set(refresh.get_json().keys()) == set(vectors["http"]["refresh_response"].keys())

        session = client.get(
            "/mobile/auth/session",
            headers=auth_header(refresh.get_json()["access_token"]),
        )
        assert session.status_code == 200
        assert set(session.get_json().keys()) == set(vectors["http"]["session_response"].keys())

    def test_nested_error_envelope(self, client, vectors) -> None:
        response = client.post(
            "/mobile/auth/login",
            json={"username": "ghost", "password": "wrong-pass"},  # pragma: allowlist secret
        )
        assert response.status_code == 401
        body = response.get_json()
        assert set(body.keys()) == {"error"}
        assert set(body["error"].keys()) == set(vectors["http"]["error_envelope"]["error"].keys())

    def test_chat_request_vector_is_accepted_and_response_conforms(self, client, vectors) -> None:
        create_user("james", "pw-123456")
        tokens = paired_login_tokens(client, "james", "pw-123456")
        response = client.post(
            "/mobile/chat",
            json=vectors["http"]["chat_request"],
            headers=auth_header(tokens["access_token"]),
        )
        assert response.status_code == 200
        assert set(response.get_json().keys()) == set(vectors["http"]["chat_response"].keys())

    def test_sse_grammar_conforms(self, client, vectors) -> None:
        create_user("james", "pw-123456")
        tokens = paired_login_tokens(client, "james", "pw-123456")
        chat_request = dict(vectors["http"]["chat_request"])
        chat_request["message_id"] = "5f8f1f2a-9999-4999-8999-999999999999"
        response = client.post(
            "/mobile/chat/stream",
            json=chat_request,
            headers=auth_header(tokens["access_token"]),
        )
        assert response.mimetype == vectors["http"]["sse"]["content_type"]
        events = parse_sse_events(response.data)
        assert events[-1]["type"] in vectors["http"]["sse"]["terminal_events"]
        for event in events:
            _assert_snake_case_keys(event)

    def test_ws_chat_frame_vector_is_accepted(self, client, vectors, services) -> None:
        from rex.mobile_api.websocket import MobileWebSocketServer
        from tests.mobile_api.test_chat_websocket import FakeWs

        create_user("james", "pw-123456")
        tokens = paired_login_tokens(client, "james", "pw-123456")
        auth_frame = dict(vectors["websocket"]["auth_frame"])
        auth_frame["access_token"] = tokens["access_token"]
        ws = FakeWs([json.dumps(auth_frame), json.dumps(vectors["websocket"]["chat_frame"])])
        MobileWebSocketServer(services).handle(ws, "10.1.1.1")
        types = [e["type"] for e in ws.sent]
        assert types[0] == "auth_ok"
        assert "ack" in types
        assert types[-1] == "message_done"
        for event in ws.sent:
            _assert_snake_case_keys(event)

    def test_tts_response_conforms(self, client, vectors) -> None:
        create_user("james", "pw-123456")
        tokens = paired_login_tokens(client, "james", "pw-123456")
        response = client.post(
            "/mobile/tts/playback",
            json={"text": vectors["http"]["tts_request"]["text"]},
            headers=auth_header(tokens["access_token"]),
        )
        assert response.status_code == 200
        assert set(response.get_json().keys()) == set(vectors["http"]["tts_response"].keys())

    def test_voice_response_conforms(self, client, vectors) -> None:
        import io
        import struct
        import wave

        create_user("james", "pw-123456")
        tokens = paired_login_tokens(client, "james", "pw-123456")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16_000)
            wav_file.writeframes(struct.pack("<16000h", *([0] * 16_000)))
        response = client.post(
            "/mobile/voice/upload",
            data={
                "audio": (io.BytesIO(buffer.getvalue()), "rec.wav", "audio/wav"),
                "mode": "mobile_voice",
            },
            headers=auth_header(tokens["access_token"]),
            content_type="multipart/form-data",
        )
        assert response.status_code == 200
        assert set(response.get_json().keys()) == set(vectors["http"]["voice_response"].keys())

    def test_capabilities_conform_and_unimplemented_stay_false(self, client, vectors) -> None:
        response = client.get("/mobile/capabilities")
        body = response.get_json()
        vector = vectors["http"]["capabilities_response"]
        assert set(body.keys()) == set(vector.keys())
        assert set(body["features"].keys()) == set(vector["features"].keys())
        assert body["api_version"] == vectors["api_version"]
        for feature in ("live_voice", "notifications", "approvals", "home_assistant"):
            assert body["features"][feature] is False
