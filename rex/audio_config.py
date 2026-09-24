"""CLI utilities for inspecting and selecting audio devices.

This module now uses rex_config.json for persistence instead of .env.
"""

from __future__ import annotations

# Load .env before accessing any environment variables
from utils.env_loader import load as _load_env  # noqa: E402

_load_env()

import argparse  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from importlib import import_module  # noqa: E402
from importlib.util import find_spec  # noqa: E402

_SOUNDDEVICE_UNSET = object()
sd = _SOUNDDEVICE_UNSET

from rex.assistant_errors import AudioDeviceError  # noqa: E402
from rex.config_manager import load_config, save_config  # noqa: E402
from rex.logging_utils import get_logger  # noqa: E402

logger = get_logger(__name__)


def _load_sounddevice():
    global sd
    if sd is not _SOUNDDEVICE_UNSET:
        return sd
    if find_spec("sounddevice") is None:
        sd = None
        return None
    try:
        sd = import_module("sounddevice")
    except ImportError:
        sd = None
    return sd


def _require_sounddevice():
    module = _load_sounddevice()
    if module is None:
        raise AudioDeviceError("The 'sounddevice' package is required for audio device selection.")
    return module


def list_devices() -> list[dict]:
    sounddevice = _require_sounddevice()
    try:
        return sounddevice.query_devices()  # type: ignore[no-any-return]
    except Exception as exc:
        raise AudioDeviceError(f"Failed to query audio devices: {exc}") from exc


def _normalize_device_name(name: str) -> str:
    value = re.sub(r"^default\s*-\s*", "", name.strip(), flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def build_audio_device_diagnostic(
    device_kind: str,
    error: BaseException | str,
) -> dict[str, object]:
    """Build a stable, user-actionable diagnostic for voice audio failures."""
    normalized_kind = "speaker" if device_kind == "speaker" else "microphone"
    detail = str(error).strip() or "audio device unavailable"
    if normalized_kind == "speaker":
        code = "speaker_unavailable"
        user_message = (
            "Speaker output unavailable. Reconnect or select an output device in Voice settings, "
            "then check your operating-system sound output."
        )
    else:
        code = "microphone_unavailable"
        user_message = (
            "Microphone unavailable. Reconnect or select a microphone in Voice settings, then "
            "check your operating-system microphone permissions."
        )
    return {
        "event": "audio_device_error",
        "code": code,
        "device_kind": normalized_kind,
        "error": detail,
        "user_message": user_message,
    }


_HOSTAPI_INPUT_PRIORITY = {
    # DirectSound/MME are the most reliable shared-mode choices for the
    # blocking ``sounddevice.rec`` path used by wake-word capture on
    # Windows. WASAPI can fail intermittently when Chromium has touched
    # the same Bluetooth/USB device, and WDM-KS is often exclusive.
    "windows directsound": 40,
    "mme": 30,
    "windows wasapi": 20,
    "windows wdm ks": 10,
}

_LOOPBACK_INPUT_NAME_RE = re.compile(r"\b(?:loopback|stereo\s+mix|what\s+u\s+hear)\b", re.IGNORECASE)
# MME sometimes truncates a long Windows endpoint name. A short shared
# prefix, however, is common enough to be an unsafe alias signal. Keep this
# deliberately above generic names such as "Microphone (Studio" while
# admitting the Windows-reported C922 and Bose Flex truncations from TEST-011.
_MINIMUM_MME_ALIAS_PREFIX_LENGTH = 24


def _load_audio_inventory(
    devices: list[dict] | None,
    hostapis: list[dict] | None,
) -> tuple[list[dict], list[dict]]:
    sounddevice = None
    if devices is None:
        sounddevice = _require_sounddevice()
        try:
            devices = sounddevice.query_devices()
        except Exception as exc:
            raise AudioDeviceError(f"Failed to query audio devices: {exc}") from exc

    if hostapis is not None:
        return devices, hostapis

    if sounddevice is None:
        sounddevice = _require_sounddevice()
    try:
        return devices, sounddevice.query_hostapis()
    except Exception:
        return devices, []


def _device_name_match_score(requested: str, candidate: str) -> int | None:
    if candidate == requested:
        return 100
    if candidate in requested or requested in candidate:
        return 80

    overlap = len(set(requested.split()) & set(candidate.split()))
    return 50 + overlap if overlap >= 3 else None


def _device_hostapi_priority(device: dict, hostapis: list[dict]) -> int:
    hostapi_index = device.get("hostapi", -1)
    if isinstance(hostapi_index, bool) or not isinstance(hostapi_index, int):
        return 0
    if not 0 <= hostapi_index < len(hostapis):
        return 0
    hostapi_name = _normalize_device_name(str(hostapis[hostapi_index].get("name", "")))
    return _HOSTAPI_INPUT_PRIORITY.get(hostapi_name, 0)


def _device_hostapi_name(device: dict, hostapis: list[dict]) -> str:
    """Return the human-readable PortAudio host API name when available."""
    hostapi_index = device.get("hostapi", -1)
    if isinstance(hostapi_index, bool) or not isinstance(hostapi_index, int):
        return "Unknown audio API"
    if not 0 <= hostapi_index < len(hostapis):
        return "Unknown audio API"
    name = str(hostapis[hostapi_index].get("name", "")).strip()
    return name or "Unknown audio API"


def _truncated_mme_name_candidates(
    normalized_name: str,
    hostapi_name: str,
    eligible: list[tuple[int, dict, str, str]],
) -> list[str]:
    """Return possible full names for a truncated Windows MME entry.

    Some Windows MME device names are cut off by PortAudio while the same
    driver exposes a complete name through another host API.  A prefix match
    is presentation evidence only, not proof of physical identity. The caller
    must present the result as a possible alias, never as identity:
    PortAudio offers no physical-device identifier.  Keeping one item per
    host-API occurrence also lets the label explain a same-name collision
    without silently treating it as one physical device.
    """
    if (
        _normalize_device_name(hostapi_name) != "mme"
        or len(normalized_name) < _MINIMUM_MME_ALIAS_PREFIX_LENGTH
    ):
        return []

    return [
        str(device.get("name", "")).strip()
        for _, device, candidate_name, candidate_hostapi in eligible
        if _normalize_device_name(candidate_hostapi) != "mme"
        and candidate_name.startswith(normalized_name)
        and candidate_name != normalized_name
    ]


def _build_device_picker_choices(
    resolved_devices: list[dict],
    resolved_hostapis: list[dict],
    *,
    channel_field: str,
    exclude_name_re: re.Pattern[str] | None = None,
    clarify_truncated_mme_aliases: bool = False,
) -> list[dict[str, object]]:
    """Shared duplicate-safe picker logic for input/output device choices.

    PortAudio does not expose a stable physical-device identifier, so a
    shared name string is never sufficient proof that two entries are the
    same hardware: distinct devices can coincide on a name, including one
    occurrence apiece on different host APIs. Collapsing entries on that
    unproven assumption would silently remove a distinct canonical
    PortAudio index from the picker.

    Every eligible device therefore keeps its own choice and its own exact
    canonical index; no entries are ever merged. When a normalized name
    repeats (whether on the same host API or across different host APIs),
    each occurrence is instead labeled with its host API and a stable
    per-host position so the entries stay distinguishable without hiding
    any canonical index.
    """
    eligible: list[tuple[int, dict, str, str]] = []
    name_counts: dict[str, int] = {}
    for index, device in enumerate(resolved_devices):
        if int(device.get(channel_field, 0) or 0) < 1:
            continue
        name = str(device.get("name", "")).strip()
        normalized_name = _normalize_device_name(name)
        if not normalized_name:
            continue
        if exclude_name_re is not None and exclude_name_re.search(name):
            continue
        hostapi_name = _device_hostapi_name(device, resolved_hostapis)
        eligible.append((index, device, normalized_name, hostapi_name))
        name_counts[normalized_name] = name_counts.get(normalized_name, 0) + 1

    # The key includes the matched full name so multiple truncated MME
    # occurrences receive deterministic labels while still keeping their
    # canonical PortAudio indices independently selectable.
    choices: list[dict[str, object]] = []
    position_counts: dict[tuple[str, str], int] = {}
    for index, device, normalized_name, hostapi_name in eligible:
        name = str(device.get("name", "")).strip()
        matched_names = _truncated_mme_name_candidates(
            normalized_name, hostapi_name, eligible
        )
        distinct_matched_names = set(matched_names)
        if clarify_truncated_mme_aliases and len(distinct_matched_names) == 1:
            matched_full_name = next(iter(distinct_matched_names))
            position_key = (normalized_name, matched_full_name.casefold())
            position_counts[position_key] = position_counts.get(position_key, 0) + 1
            collision_detail = (
                f" of {len(matched_names)} same-named host-API entries"
                if len(matched_names) > 1
                else ""
            )
            label = (
                f"{matched_full_name} (MME input, possible shortened-name alias"
                f"{collision_detail}; separate device {position_counts[position_key]})"
            )
        elif clarify_truncated_mme_aliases and distinct_matched_names:
            label = f"{name} (MME input, shortened name is ambiguous; separate device)"
        elif len(distinct_matched_names) == 1:
            matched_full_name = next(iter(distinct_matched_names))
            position_key = (normalized_name, matched_full_name.casefold())
            position_counts[position_key] = position_counts.get(position_key, 0) + 1
            label = (
                f"{matched_full_name} (MME shortened-name hint "
                f"{position_counts[position_key]})"
            )
        elif distinct_matched_names:
            label = f"{name} (MME shortened-name hint ambiguous)"
        elif name_counts[normalized_name] > 1:
            position_key = (normalized_name, hostapi_name.casefold())
            position_counts[position_key] = position_counts.get(position_key, 0) + 1
            label = f"{name} ({hostapi_name} {position_counts[position_key]})"
        else:
            label = name
        choices.append(
            {
                "index": index,
                "name": label,
                "max_input_channels": int(device.get("max_input_channels", 0) or 0),
                "max_output_channels": int(device.get("max_output_channels", 0) or 0),
                "host_api": hostapi_name,
                "alias_count": 1,
            }
        )

    return sorted(choices, key=lambda choice: str(choice["name"]).casefold())


def build_microphone_picker_devices(
    *,
    devices: list[dict] | None = None,
    hostapis: list[dict] | None = None,
) -> list[dict[str, object]]:
    """Build physical-microphone choices without changing PortAudio IDs.

    Windows commonly exposes one physical microphone through several
    PortAudio host APIs (MME, DirectSound, WASAPI, WDM-KS) using the same
    or a near-identical name; see `_build_device_picker_choices` for why
    that never justifies merging canonical indices. Explicit system-audio
    loopbacks are not microphone choices.
    """
    resolved_devices, resolved_hostapis = _load_audio_inventory(devices, hostapis)
    return _build_device_picker_choices(
        resolved_devices,
        resolved_hostapis,
        channel_field="max_input_channels",
        exclude_name_re=_LOOPBACK_INPUT_NAME_RE,
        clarify_truncated_mme_aliases=True,
    )


def build_speaker_picker_devices(
    *,
    devices: list[dict] | None = None,
    hostapis: list[dict] | None = None,
) -> list[dict[str, object]]:
    """Build physical-speaker/output choices without changing PortAudio IDs.

    Windows commonly exposes one physical speaker/headphone output through
    several PortAudio host APIs (MME, DirectSound, WASAPI, WDM-KS) using
    the same or a near-identical name; see `_build_device_picker_choices`
    for why that never justifies merging canonical indices (TEST-003).
    """
    resolved_devices, resolved_hostapis = _load_audio_inventory(devices, hostapis)
    return _build_device_picker_choices(
        resolved_devices,
        resolved_hostapis,
        channel_field="max_output_channels",
    )


def _input_device_candidate_score(
    requested: str,
    device: dict,
    hostapis: list[dict],
) -> int | None:
    if int(device.get("max_input_channels", 0) or 0) < 1:
        return None
    candidate = _normalize_device_name(str(device.get("name", "")))
    if not candidate:
        return None
    name_score = _device_name_match_score(requested, candidate)
    if name_score is None:
        return None
    return name_score + _device_hostapi_priority(device, hostapis)


def resolve_input_device_index_by_name(
    device_name: str | None,
    *,
    devices: list[dict] | None = None,
    hostapis: list[dict] | None = None,
) -> int | None:
    """Resolve a browser/OS microphone label to a sounddevice input index.

    Chromium exposes stable human-readable labels but not PortAudio indices.
    Match the label against input-capable devices and prefer Windows DirectSound,
    then MME, WASAPI, and WDM-KS when multiple host APIs expose the same
    physical microphone.
    """
    requested = _normalize_device_name(device_name or "")
    if not requested:
        return None

    resolved_devices, resolved_hostapis = _load_audio_inventory(devices, hostapis)
    candidates = [
        (score, index)
        for index, device in enumerate(resolved_devices)
        if (score := _input_device_candidate_score(requested, device, resolved_hostapis))
        is not None
    ]
    if not candidates:
        raise AudioDeviceError(
            f"Selected microphone is unavailable to the wake-word backend: {device_name}"
        )
    return max(candidates)[1]


def get_selected_input_device_index(config: dict) -> int | None:
    """Get selected input device index from config dict.

    Args:
        config: Configuration dict (from config_manager.load_config)

    Returns:
        Device index or None
    """
    return config.get("audio", {}).get("input_device_index")  # type: ignore[no-any-return]


def set_selected_input_device_index(config: dict, index: int | None) -> dict:
    """Set selected input device index in config dict.

    Args:
        config: Configuration dict
        index: Device index or None

    Returns:
        Updated config dict
    """
    if "audio" not in config:
        config["audio"] = {}
    config["audio"]["input_device_index"] = index
    return config


def get_selected_output_device_index(config: dict) -> int | None:
    """Get selected output device index from config dict.

    Args:
        config: Configuration dict (from config_manager.load_config)

    Returns:
        Device index or None
    """
    return config.get("audio", {}).get("output_device_index")  # type: ignore[no-any-return]


def set_selected_output_device_index(config: dict, index: int | None) -> dict:
    """Set selected output device index in config dict.

    Args:
        config: Configuration dict
        index: Device index or None

    Returns:
        Updated config dict
    """
    if "audio" not in config:
        config["audio"] = {}
    config["audio"]["output_device_index"] = index
    return config


def test_input_device(device_id: int) -> None:
    """Functionally probe an input device without persisting a selection."""
    devices = list_devices()
    if device_id < 0 or device_id >= len(devices):
        raise AudioDeviceError(f"Invalid input device ID: {device_id}")

    device = devices[device_id]
    if device["max_input_channels"] < 1:
        raise AudioDeviceError(f"Device {device_id} has no input channels.")

    try:
        sounddevice = _require_sounddevice()
        with sounddevice.InputStream(device=device_id, blocksize=0):
            pass
    except Exception as exc:
        raise AudioDeviceError(f"Failed to open input device {device_id}: {exc}") from exc


def test_output_device(device_id: int) -> None:
    """Functionally probe an output device without persisting a selection."""
    devices = list_devices()
    if device_id < 0 or device_id >= len(devices):
        raise AudioDeviceError(f"Invalid output device ID: {device_id}")

    device = devices[device_id]
    if device["max_output_channels"] < 1:
        raise AudioDeviceError(f"Device {device_id} has no output channels.")

    try:
        sounddevice = _require_sounddevice()
        with sounddevice.OutputStream(device=device_id, blocksize=0):
            pass
    except Exception as exc:
        raise AudioDeviceError(f"Failed to open output device {device_id}: {exc}") from exc


def select_input(device_id: int, *, config: dict | None = None) -> None:
    """Select and persist input device to rex_config.json.

    Args:
        device_id: Device index to select

    Raises:
        AudioDeviceError: If device is invalid or cannot be opened
    """
    test_input_device(device_id)

    if config is None:
        config = load_config()
    config = set_selected_input_device_index(config, device_id)
    save_config(config)
    logger.info(f"Selected input device {device_id}, saved to config")


def select_output(device_id: int, *, config: dict | None = None) -> None:
    """Select and persist output device to rex_config.json.

    Args:
        device_id: Device index to select

    Raises:
        AudioDeviceError: If device is invalid or cannot be opened
    """
    test_output_device(device_id)

    if config is None:
        config = load_config()
    config = set_selected_output_device_index(config, device_id)
    save_config(config)
    logger.info(f"Selected output device {device_id}, saved to config")


def _format_devices() -> str:
    devices = list_devices()
    rows = [" ID | Name                           | In | Out"]
    rows.append("-" * 50)
    for idx, device in enumerate(devices):
        rows.append(
            f"{idx:2d} | {device['name'][:30]:<30} | {device['max_input_channels']:2d} | {device['max_output_channels']:2d}"
        )
    return "\n".join(rows)


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure audio devices for Rex.")
    parser.add_argument("--list", action="store_true", help="List available audio devices")
    parser.add_argument(
        "--set-input", type=int, metavar="INDEX", help="Persist default input device"
    )
    parser.add_argument(
        "--set-output", type=int, metavar="INDEX", help="Persist default output device"
    )
    parser.add_argument("--show", action="store_true", help="Show current configured devices")

    args = parser.parse_args(argv)

    try:
        if args.list:
            print(_format_devices())
            return 0

        config = None
        if args.set_input is not None or args.set_output is not None:
            config = load_config()

        if args.set_input is not None:
            select_input(args.set_input, config=config)
            print(f"Input device set to index {args.set_input}")

        if args.set_output is not None:
            select_output(args.set_output, config=config)
            print(f"Output device set to index {args.set_output}")

        if args.show:
            config = load_config()
            input_idx = get_selected_input_device_index(config)
            output_idx = get_selected_output_device_index(config)
            print("Configured Audio Devices:")
            print(f"  Input Device Index : {input_idx}")
            print(f"  Output Device Index: {output_idx}")

        if not any([args.list, args.set_input is not None, args.set_output is not None, args.show]):
            parser.print_help()
            return 1

        return 0
    except AudioDeviceError as exc:
        logger.error("Audio error: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    """Entry point used by unit tests to invoke the CLI."""

    return cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
