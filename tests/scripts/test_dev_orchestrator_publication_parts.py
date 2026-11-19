import hashlib
from copy import deepcopy

import pytest

from scripts.dev_orchestrator.publication_parts import (
    diff_layout,
    render_part,
    select_part,
    validate_parts,
)

HEADER = (
    "diff --git a/one.txt b/one.txt\nindex 1234567..abcdef0 100644\n--- a/one.txt\n+++ b/one.txt\n"
)
HUNKS = (
    "@@ -1 +1 @@\n-old\n+new\n",
    "@@ -12,2 +12,2 @@ section\n keep\n-remove\n+replace\n",
    "@@ -25 +25 @@\n-before\n\\ No newline at end of file\n+after\n\\ No newline at end of file\n",
)
MODE = "diff --git a/mode b/mode\nold mode 100644\nnew mode 100755\n"
BINARY = "diff --git a/image.bin b/image.bin\nindex 1111111..2222222 100644\nBinary files a/image.bin and b/image.bin differ\n"
EMPTY_FILE = "diff --git a/empty b/empty\nnew file mode 100644\nindex 0000000..e69de29\n"


def _parts(text: str, limits: list[int]) -> list[dict]:
    result = []
    first = 0
    for limit in limits:
        part = select_part(text, first, limit)
        result.append(part)
        first = part["next_unit"]
        if first == diff_layout(text)["unit_count"]:
            break
    return result


def test_complete_hunks_reconstruct_one_file_across_parts() -> None:
    text = HEADER + "".join(HUNKS)
    parts = _parts(text, [len(HEADER + hunk) for hunk in HUNKS])
    assert [part["text"] for part in parts] == [HEADER + hunk for hunk in HUNKS]
    assert len("".join(part["text"] for part in parts)) > len(text)
    validate_parts(text, parts)
    layout = diff_layout(text)
    assert layout["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert layout["files"][0]["header_end"] == len(HEADER)
    assert [text[unit["start"] : unit["end"]] for unit in layout["units"]] == list(HUNKS)


def test_greedy_selection_spends_header_once_and_crosses_file_boundary() -> None:
    text = HEADER + "".join(HUNKS) + MODE + BINARY
    first_text = HEADER + "".join(HUNKS[:2])
    first = select_part(text, 0, len(first_text))
    assert first["text"] == first_text
    assert first["next_unit"] == 2
    second_text = HEADER + HUNKS[2] + MODE
    second = select_part(text, 2, len(second_text))
    assert second["text"] == second_text
    assert second["next_unit"] == 4
    third = select_part(text, 4, len(BINARY))
    validate_parts(text, [first, second, third])
    assert render_part(text, 0, 5) == text


@pytest.mark.parametrize("record", [MODE, BINARY, EMPTY_FILE])
def test_non_hunk_file_records_are_atomic(record: str) -> None:
    with pytest.raises(ValueError, match="complete diff unit"):
        select_part(record, 0, len(record) - 1)
    part = select_part(record, 0, len(record))
    assert part["text"] == record
    assert diff_layout(record)["units"][0]["kind"] == "record"
    validate_parts(record, [part])


def test_large_file_is_split_only_between_complete_hunks() -> None:
    hunks = [f"@@ -{n + 1} +{n + 1} @@\n-old\n+" + "x" * 51_716 + "\n" for n in range(5)]
    text = HEADER + "".join(hunks)
    assert len(text) > 228_000
    first = select_part(text, 0, 120_000)
    second = select_part(text, first["next_unit"], 120_000)
    third = select_part(text, second["next_unit"], 120_000)
    assert [part["next_unit"] for part in (first, second, third)] == [2, 4, 5]
    validate_parts(text, [first, second, third])
    assert all(part["characters"] <= 120_000 for part in (first, second, third))


def test_one_over_budget_hunk_is_refused_without_partial_selection() -> None:
    text = HEADER + "@@ -1 +1 @@\n-old\n+" + "x" * 144_411 + "\n"
    with pytest.raises(ValueError, match="complete diff unit"):
        select_part(text, 0, len(text) - 1)
    assert select_part(text, 0, len(text))["text"] == text


def test_empty_diff_still_requires_one_explicit_review_unit() -> None:
    layout = diff_layout("")
    assert layout["unit_count"] == 1
    assert layout["files"] == []
    part = select_part("", 0, 0)
    assert part["first_unit"] == 0 and part["next_unit"] == 1
    assert part["text"] == "" and part["characters"] == 0
    validate_parts("", [part])
    with pytest.raises(ValueError):
        validate_parts("", [])


@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
def test_exact_endings_unicode_and_no_newline_markers_are_retained(ending: str) -> None:
    text = HEADER + HUNKS[2]
    text = text.replace("after", "caf\u00e9\u2028diff --git is file content")
    if ending == "\r\n":
        text = text.replace("\n", "\r\n")
    elif ending == "":
        text = text[:-1]
    part = select_part(text, 0, len(text))
    assert part["text"] == text
    assert part["characters"] == len(text)
    assert part["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert diff_layout(text)["unit_count"] == 1
    validate_parts(text, [part])


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "reordered",
        "range",
        "text",
        "digest",
        "characters",
        "header",
        "extra",
    ],
)
def test_invalid_part_coverage_or_representation_is_rejected(mutation: str) -> None:
    text = HEADER + "".join(HUNKS)
    parts = _parts(text, [len(HEADER + hunk) for hunk in HUNKS])
    changed = deepcopy(parts)
    if mutation == "missing":
        changed.pop()
    elif mutation == "duplicate":
        changed.insert(1, deepcopy(changed[0]))
    elif mutation == "reordered":
        changed.reverse()
    elif mutation == "range":
        changed[1]["first_unit"] = 0
    elif mutation == "text":
        changed[1]["text"] = changed[1]["text"].replace("replace", "injected")
    elif mutation == "digest":
        changed[1]["sha256"] = "0" * 64
    elif mutation == "characters":
        changed[1]["characters"] -= 1
    elif mutation == "header":
        changed[1]["text"] = changed[1]["text"].replace("b/one.txt", "b/other.txt")
    else:
        changed[1]["accepted"] = True
    if mutation in {"text", "header"}:
        changed[1]["sha256"] = hashlib.sha256(changed[1]["text"].encode()).hexdigest()
        changed[1]["characters"] = len(changed[1]["text"])
    with pytest.raises(ValueError):
        validate_parts(text, changed)


@pytest.mark.parametrize(
    "text",
    [
        "not a Git diff\n",
        "diff --cc file\n",
        "diff --git \n",
        HEADER + "@@@ -1,1 -1,1 +1,1 @@@\n",
        HEADER + "@@ malformed @@\n",
        "diff --git a/one.txt b/one.txt\n@@ -1 +1 @@\n-old\n+new\n",
    ],
)
def test_unsupported_or_malformed_structure_fails_closed(text: str) -> None:
    with pytest.raises(ValueError):
        diff_layout(text)


@pytest.mark.parametrize("first,next_unit", [(True, 1), (0, False), (-1, 1), (0, 0), (0, 4)])
def test_part_ranges_require_actual_integers_and_complete_units(first, next_unit) -> None:
    with pytest.raises(ValueError):
        render_part(HEADER + HUNKS[0], first, next_unit)


@pytest.mark.parametrize("limit", [True, -1, 1.5, "100"])
def test_part_limits_are_explicit_nonnegative_integers(limit) -> None:
    with pytest.raises(ValueError):
        select_part(HEADER + HUNKS[0], 0, limit)
