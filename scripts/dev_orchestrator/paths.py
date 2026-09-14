from itertools import combinations
from pathlib import Path


def paths_overlap(first: Path, second: Path) -> bool:
    left = first.resolve()
    right = second.resolve()
    return left == right or left in right.parents or right in left.parents


def validate_runtime_paths(
    coordination: Path,
    backend: Path,
    mobile: Path,
    frozen: Path,
) -> None:
    named = {
        "coordination": coordination.resolve(),
        "backend": backend.resolve(),
        "mobile": mobile.resolve(),
        "frozen": frozen.resolve(),
    }
    for (left_name, left), (right_name, right) in combinations(named.items(), 2):
        if paths_overlap(left, right):
            raise ValueError(f"path overlap is unsafe: {left_name}={left} and {right_name}={right}")
