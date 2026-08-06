#!/usr/bin/env python3
"""Reusable structural helpers for TwinClip report validation."""

from __future__ import annotations

import json
import math
from pathlib import Path
import stat
from typing import Any

from contracts import (  # noqa: E402
    BANDS,
    TOLERANCE,
    round_half_up,
    sha256_file,
)


MAX_REPORT_BYTES = 20 * 1024 * 1024


def is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def close(actual: Any, expected: float, tolerance: float = TOLERANCE) -> bool:
    return is_number(actual) and math.isclose(float(actual), expected, abs_tol=tolerance)


def non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def require_mapping(parent: dict[str, Any], key: str, path: str, errors: list[str]) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        errors.append(f"{path}.{key} must be an object")
        return {}
    return value


def require_list(parent: dict[str, Any], key: str, path: str, errors: list[str]) -> list[Any]:
    value = parent.get(key)
    if not isinstance(value, list):
        errors.append(f"{path}.{key} must be an array")
        return []
    return value


def validate_weight_group(values: list[Any], label: str, errors: list[str]) -> bool:
    if not values or not all(is_number(value) and float(value) >= 0 for value in values):
        errors.append(f"{label} weights must be finite non-negative numbers")
        return False
    if not math.isclose(sum(float(value) for value in values), 1.0, abs_tol=1e-6):
        errors.append(f"{label} weights must sum to 1")
        return False
    return True


def valid_string_list(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        if not non_empty_string(item):
            errors.append(f"{label}[{index}] must be a non-empty string")
            continue
        result.append(item)
    return result


def validate_id_list(
    value: Any, label: str, known: set[str], errors: list[str], *, allow_empty: bool = True
) -> list[str]:
    items = valid_string_list(value, label, errors)
    if not allow_empty and not items:
        errors.append(f"{label} must not be empty")
    if len(items) != len(set(items)):
        errors.append(f"{label} must not contain duplicate ids")
    unknown = set(items) - known
    if unknown:
        errors.append(f"{label} references unknown ids: {sorted(unknown)}")
    return items


def collect_ids(items: list[Any], label: str, errors: list[str]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    ids: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        item_id = item.get("id")
        if not non_empty_string(item_id):
            errors.append(f"{label}[{index}].id must be a non-empty string")
            continue
        if item_id in by_id:
            errors.append(f"duplicate {label} id: {item_id}")
            continue
        ids.append(item_id)
        by_id[item_id] = item
    return ids, by_id


def validate_range(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list) or len(value) != 2 or not all(is_number(item) for item in value):
        errors.append(f"{label} must be a finite [start, end] pair")
        return
    if float(value[0]) < 0 or float(value[1]) <= float(value[0]):
        errors.append(f"{label} must satisfy 0 <= start < end")


def validate_regular_file(value: Any, label: str, errors: list[str]) -> str | None:
    if not non_empty_string(value):
        errors.append(f"{label} must be a non-empty file path")
        return None
    path = Path(value).expanduser()
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        errors.append(f"{label} is not readable: {exc}")
        return None
    if not stat.S_ISREG(mode):
        errors.append(f"{label} must reference a regular file")
        return None
    return str(path.resolve())


def validate_source_hash(
    value: Any,
    expected_path: str,
    label: str,
    errors: list[str],
    hash_cache: dict[str, str],
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    path = validate_regular_file(value.get("path"), f"{label}.path", errors)
    if path and path != expected_path:
        errors.append(f"{label}.path must equal the bound source path")
    digest = value.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        errors.append(f"{label}.sha256 must be a lowercase SHA-256 digest")
        return
    if path:
        try:
            actual = hash_cache.get(path)
            if actual is None:
                actual = sha256_file(path)
                hash_cache[path] = actual
            if actual != digest:
                errors.append(f"{label}.sha256 does not match the current file content")
            size = value.get("bytes")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                errors.append(f"{label}.bytes must be a non-negative integer")
            elif size != Path(path).stat().st_size:
                errors.append(f"{label}.bytes does not match the current file size")
        except OSError as exc:
            errors.append(f"{label} cannot be verified: {exc}")


def validate_content_identity(
    value: Any,
    expected_path: str,
    label: str,
    errors: list[str],
    hash_cache: dict[str, str],
) -> None:
    """Validate a path-free content identity embedded in the reference graph."""
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    if "path" in value:
        errors.append(f"{label} must not contain a runtime path")
    digest = value.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        errors.append(f"{label}.sha256 must be a lowercase SHA-256 digest")
        return
    size = value.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        errors.append(f"{label}.bytes must be a non-negative integer")
    try:
        actual = hash_cache.get(expected_path)
        if actual is None:
            actual = sha256_file(expected_path)
            hash_cache[expected_path] = actual
        if actual != digest:
            errors.append(f"{label}.sha256 does not match the current file content")
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0 and size != Path(expected_path).stat().st_size:
            errors.append(f"{label}.bytes does not match the current file size")
    except OSError as exc:
        errors.append(f"{label} cannot be verified: {exc}")


def read_json_file(path: str, label: str, errors: list[str]) -> dict[str, Any] | None:
    try:
        if Path(path).stat().st_size > MAX_REPORT_BYTES:
            errors.append(f"{label} exceeds {MAX_REPORT_BYTES} bytes")
            return None
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{label} cannot be read: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} must contain a JSON object")
        return None
    return value


def eligible_evidence(record: dict[str, Any]) -> bool:
    if record.get("observation_mode") == "blind":
        confirmation_ok = record.get("human_confirmation") in {"not_required", "confirmed"}
    else:
        confirmation_ok = record.get("human_confirmation") == "confirmed"
    fields = ("visual", "onscreen_text", "transcript", "observed_function")
    has_observation = any(record.get(field) != "unknown" for field in fields)
    return (
        confirmation_ok
        and has_observation
        and non_empty_string(record.get("observed_function"))
        and record.get("observed_function") != "unknown"
    )


def score_band(center: float) -> str:
    rounded = min(100, max(0, round_half_up(center)))
    for low, high, label in BANDS:
        if low <= rounded <= high:
            return label
    raise AssertionError("unreachable")


def band_index(label: str) -> int:
    return next(index for index, (_, _, name) in enumerate(BANDS) if name == label)


def expected_confidence(e_value: float, m_value: float, r_value: float) -> str:
    if e_value >= 0.85 and m_value == 1 and r_value <= 0.10:
        return "high"
    if e_value >= 0.65 and m_value >= 0.5 and r_value <= 0.30:
        return "medium"
    return "low"
