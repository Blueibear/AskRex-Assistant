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


def build_microphone_picker_devices(
    *,
    devices: list[dict] | None = None,
    hostapis: list[dict] | None = None,
) -> list[dict[str, object]]:
    """Build physical-microphone choices without changing PortAudio IDs.

    Windows exposes one endpoint through several PortAudio host APIs. A
    normalized name is only merged into one alias group across host APIs
    when it appears at most once per host API, since that is the only case
    where PortAudio's name/host-API pair unambiguously identifies a single
    alias to preferred-canonical mapping. A normalized name that repeats
    within any single host API is ambiguous: there is no reliable way to
    pair those duplicates with a same-named entry on another host API, so
    every instance of that name stays its own canonical choice rather than
    risking a merge that silently drops a distinct physical device.
    Explicit system-audio loopbacks are not microphone choices.
    """
    resolved_devices, resolved_hostapis = _load_audio_inventory(devices, hostapis)

    eligible: list[tuple[int, dict, str, str]] = []
    per_host_name_counts: dict[tuple[str, str], int] = {}
    for index, device in enumerate(resolved_devices):
        if int(device.get("max_input_channels", 0) or 0) < 1:
            continue
        name = str(device.get("name", "")).strip()
        normalized_name = _normalize_device_name(name)
        if not normalized_name or _LOOPBACK_INPUT_NAME_RE.search(name):
            continue
        hostapi_name = _device_hostapi_name(device, resolved_hostapis)
        eligible.append((index, device, normalized_name, hostapi_name))
        host_key = (normalized_name, hostapi_name.casefold())
        per_host_name_counts[host_key] = per_host_name_counts.get(host_key, 0) + 1

    # Cross-host alias merging requires an unambiguous 1:1 pairing between
    # host APIs. A name that repeats within one host API breaks that
    # pairing for every host API carrying the name, so none of its
    # instances are merged.
    ambiguous_names = {
        normalized_name
        for (normalized_name, _hostapi_key), count in per_host_name_counts.items()
        if count > 1
    }

    grouped: dict[tuple[str, object], list[tuple[int, dict, str]]] = {}
    for index, device, normalized_name, hostapi_name in eligible:
        group_key: tuple[str, object]
        if normalized_name in ambiguous_names:
            group_key = ("device", index)
        else:
            group_key = ("alias", normalized_name)
        grouped.setdefault(group_key, []).append((index, device, hostapi_name))

    groups = list(grouped.values())
    name_group_counts: dict[str, int] = {}
    for candidates in groups:
        normalized_name = _normalize_device_name(str(candidates[0][1].get("name", "")))
        name_group_counts[normalized_name] = name_group_counts.get(normalized_name, 0) + 1

    choices: list[dict[str, object]] = []
    conflict_positions: dict[tuple[str, str], int] = {}
    for candidates in groups:
        index, device, hostapi_name = max(
            candidates,
            key=lambda candidate: (
                _device_hostapi_priority(candidate[1], resolved_hostapis),
                -candidate[0],
            ),
        )
        name = str(device.get("name", "")).strip()
        alias_count = len(candidates)
        normalized_name = _normalize_device_name(name)
        conflict_count = name_group_counts[normalized_name]
        if alias_count > 1:
            label = (
                f"{name} (preferred via {hostapi_name}; "
                f"{alias_count} Windows audio entries)"
            )
        elif conflict_count > 1:
            position_key = (normalized_name, hostapi_name.casefold())
            conflict_positions[position_key] = conflict_positions.get(position_key, 0) + 1
            label = f"{name} ({hostapi_name} {conflict_positions[position_key]})"
        else:
            label = name
        choices.append(
            {
                "index": index,
                "name": label,
                "max_input_channels": int(device.get("max_input_channels", 0) or 0),
                "max_output_channels": int(device.get("max_output_channels", 0) or 0),
                "host_api": hostapi_name,
                "alias_count": alias_count,
            }
        )

    return sorted(choices, key=lambda choice: str(choice["name"]).casefold())


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
