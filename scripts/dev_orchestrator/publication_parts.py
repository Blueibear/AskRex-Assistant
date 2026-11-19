"""Lossless, deterministic partitions of a canonical redacted Git diff.

Callers own the repository, scope, path inventory and review authority. This
module only groups complete hunks or complete non-hunk file records. Repeated
file headers make a partial file readable without counting its hunks twice.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

_HUNK = re.compile(r"@@ -[0-9]+(?:,[0-9]+)? \+[0-9]+(?:,[0-9]+)? @@(?: .*)?\Z")
_PART_KEYS = {"first_unit", "next_unit", "text", "sha256", "characters"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _line_text(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    return line[:-1] if line.endswith("\n") else line


def _lines(text: str) -> Iterator[str]:
    start = 0
    while True:
        end = text.find("\n", start)
        if end < 0:
            if start < len(text):
                yield text[start:]
            return
        yield text[start : end + 1]
        start = end + 1


@dataclass(frozen=True)
class _File:
    start: int
    end: int
    header_end: int
    first_unit: int
    next_unit: int


@dataclass(frozen=True)
class _Unit:
    file_index: int | None
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class _Layout:
    files: tuple[_File, ...]
    units: tuple[_Unit, ...]


def _parse(text: str) -> _Layout:
    if not isinstance(text, str):
        raise ValueError("canonical diff must be text")
    if not text:
        return _Layout((), (_Unit(None, "empty", 0, 0),))
    starts: list[int] = []
    hunks: dict[int, list[int]] = {}
    offset = 0
    for line in _lines(text):
        value = _line_text(line)
        if value.startswith("diff --git "):
            if len(value) == len("diff --git "):
                raise ValueError("Git diff file header is empty")
            starts.append(offset)
            hunks[offset] = []
        elif value.startswith("diff --"):
            raise ValueError("only non-combined Git diff records are supported")
        elif not starts:
            raise ValueError("canonical diff has an unsupported preamble")
        elif value.startswith("@@"):
            if _HUNK.fullmatch(value) is None:
                raise ValueError("Git diff hunk header is malformed or combined")
            hunks[starts[-1]].append(offset)
        offset += len(line)
    files: list[_File] = []
    units: list[_Unit] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        positions = hunks[start]
        first = len(units)
        if positions:
            header_end = positions[0]
            header_lines = [_line_text(line) for line in _lines(text[start:header_end])]
            if not any(line.startswith("--- ") for line in header_lines) or not any(
                line.startswith("+++ ") for line in header_lines
            ):
                raise ValueError("Git diff hunk lacks its original file header")
            for number, position in enumerate(positions):
                next_position = positions[number + 1] if number + 1 < len(positions) else end
                units.append(_Unit(index, "hunk", position, next_position))
        else:
            header_end = end
            units.append(_Unit(index, "record", start, end))
        files.append(_File(start, end, header_end, first, len(units)))
    return _Layout(tuple(files), tuple(units))


def diff_layout(text: str) -> dict:
    """Return exact source offsets and hashes; offsets count Unicode characters."""
    layout = _parse(text)
    return {
        "schema_version": 1,
        "sha256": _sha(text),
        "characters": len(text),
        "unit_count": len(layout.units),
        "files": [
            {
                "index": index,
                "start": item.start,
                "end": item.end,
                "header_end": item.header_end,
                "first_unit": item.first_unit,
                "next_unit": item.next_unit,
                "sha256": _sha(text[item.start : item.end]),
                "header_sha256": _sha(text[item.start : item.header_end]),
            }
            for index, item in enumerate(layout.files)
        ],
        "units": [
            {
                "index": index,
                "file_index": item.file_index,
                "kind": item.kind,
                "start": item.start,
                "end": item.end,
                "sha256": _sha(text[item.start : item.end]),
            }
            for index, item in enumerate(layout.units)
        ],
    }


def _range(layout: _Layout, first_unit: int, next_unit: int) -> None:
    if (
        type(first_unit) is not int
        or type(next_unit) is not int
        or not 0 <= first_unit < next_unit <= len(layout.units)
    ):
        raise ValueError("part must select a nonempty contiguous range of complete units")


def _pieces(text: str, layout: _Layout, first_unit: int, next_unit: int) -> list[str]:
    _range(layout, first_unit, next_unit)
    pieces: list[str] = []
    previous_file = None
    for unit in layout.units[first_unit:next_unit]:
        if unit.kind == "hunk" and unit.file_index != previous_file:
            file = layout.files[unit.file_index]
            pieces.append(text[file.start : file.header_end])
        pieces.append(text[unit.start : unit.end])
        previous_file = unit.file_index
    return pieces


def render_part(text: str, first_unit: int, next_unit: int) -> str:
    """Render exact whole units, repeating their file header only when needed."""
    return "".join(_pieces(text, _parse(text), first_unit, next_unit))


def select_part(text: str, first_unit: int, max_chars: int) -> dict:
    """Select the longest contiguous complete-unit prefix within the given bound."""
    layout = _parse(text)
    _range(layout, first_unit, first_unit + 1 if type(first_unit) is int else 0)
    if type(max_chars) is not int or max_chars < 0:
        raise ValueError("part character limit must be a nonnegative integer")
    pieces: list[str] = []
    characters = 0
    previous_file = None
    next_unit = first_unit
    for unit in layout.units[first_unit:]:
        extra: list[str] = []
        if unit.kind == "hunk" and unit.file_index != previous_file:
            file = layout.files[unit.file_index]
            extra.append(text[file.start : file.header_end])
        extra.append(text[unit.start : unit.end])
        size = sum(map(len, extra))
        if characters + size > max_chars:
            break
        pieces.extend(extra)
        characters += size
        previous_file = unit.file_index
        next_unit += 1
    if next_unit == first_unit:
        raise ValueError("one complete diff unit and its file header exceed the part limit")
    selected = "".join(pieces)
    return {
        "first_unit": first_unit,
        "next_unit": next_unit,
        "text": selected,
        "sha256": _sha(selected),
        "characters": characters,
    }


def validate_parts(text: str, parts: list[dict]) -> None:
    """Require exact sequential and exhaustive coverage of the canonical diff."""
    layout = _parse(text)
    if not isinstance(parts, list) or not parts:
        raise ValueError("complete diff coverage requires at least one explicit review part")
    next_unit = 0
    reconstructed: list[str] = []
    previous_file = None
    for part in parts:
        if not isinstance(part, dict) or set(part) != _PART_KEYS:
            raise ValueError("review part has an unexpected schema")
        _range(layout, part["first_unit"], part["next_unit"])
        if part["first_unit"] != next_unit:
            raise ValueError("review parts are missing, duplicated or reordered")
        expected = "".join(_pieces(text, layout, part["first_unit"], part["next_unit"]))
        if (
            part["text"] != expected
            or part["sha256"] != _sha(expected)
            or type(part["characters"]) is not int
            or part["characters"] != len(expected)
        ):
            raise ValueError("review part text, digest or character count differs")
        for unit in layout.units[part["first_unit"] : part["next_unit"]]:
            if unit.kind == "hunk" and unit.file_index != previous_file:
                file = layout.files[unit.file_index]
                reconstructed.append(text[file.start : file.header_end])
            reconstructed.append(text[unit.start : unit.end])
            previous_file = unit.file_index
        next_unit = part["next_unit"]
    if next_unit != len(layout.units) or "".join(reconstructed) != text:
        raise ValueError("review parts do not reconstruct the complete original diff")
