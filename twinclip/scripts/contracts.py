#!/usr/bin/env python3
"""Shared TwinClip contract constants and deterministic identities."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.5"
CALIBRATION_REGISTRY_SCHEMA_VERSION = "1.1"
PREPARE_MANIFEST_SCHEMA_VERSION = "1.2"
QA_HISTORY_SCHEMA_VERSION = "1.2"
ANALYSIS_VERSION = "1.5"
COMPILER_VERSION = "twinclip-compiler-0.2"
TOLERANCE = 0.02
MIN_L_WEIGHT = 0.50
DEFAULT_L_WEIGHT = 0.70
DEFAULT_S_WEIGHT = 0.30
DEFAULT_S_WEIGHTS = {
    "logic": 0.35,
    "function": 0.30,
    "elements": 0.25,
    "support": 0.10,
}
BANDS = [
    (0, 19, "未采纳"),
    (20, 39, "表层模仿"),
    (40, 59, "单点机制迁移"),
    (60, 79, "多点结构化迁移"),
    (80, 100, "二次创新"),
]
BAND_LABELS = tuple(label for _, _, label in BANDS)
VALID_CLARITY = {"clear", "ambiguous", "unavailable"}
VALID_EVIDENCE_SCOPES = {"segment", "full_video"}
VALID_FAILURES = {None, "L", "S", "A"}
VALID_ADAPTATION_REQUIRED = {"yes", "no", "unclear"}
VALID_ADAPTATION_RESULTS = {"not_needed", "successful", "partial", "failed", "pending"}
VALID_REVIEW_STATUS = {"pending", "completed"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reference_bundle_payload(reference: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(reference)
    payload.pop("content_hash", None)
    # Paths are runtime bindings. Path-free source content identities remain in
    # the payload so changing the breakdown or Storyboard changes the bundle.
    payload.pop("source_inputs", None)
    return payload


def reference_bundle_hash(reference: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(reference_bundle_payload(reference)).encode("utf-8"))


def registry_hash(registry: dict[str, Any]) -> str:
    payload = copy.deepcopy(registry)
    payload.pop("content_hash", None)
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def provenance_payload(provenance: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(provenance)
    payload.pop("method_fingerprint", None)
    payload.pop("source_hashes", None)
    return payload


def provenance_fingerprint(provenance: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(provenance_payload(provenance)).encode("utf-8"))


def analysis_id(reference_hash: str, creator_hash: str, method_fingerprint: str) -> str:
    payload = {
        "reference_bundle_hash": reference_hash,
        "creator_video_sha256": creator_hash,
        "method_fingerprint": method_fingerprint,
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def band_bounds(label: str) -> tuple[int, int] | None:
    for low, high, name in BANDS:
        if name == label:
            return low, high
    return None


def round_half_up(value: float) -> int:
    """Round a non-negative score deterministically instead of using banker's rounding."""
    return int(math.floor(value + 0.5))


def default_s_weights(*, has_relationships: bool, has_required_elements: bool) -> dict[str, float]:
    weights = dict(DEFAULT_S_WEIGHTS)
    if not has_relationships:
        weights["logic"] = 0.0
    if not has_required_elements:
        weights["elements"] = 0.0
    active_sum = sum(weights.values())
    if active_sum <= 0:
        raise ValueError("at least one S dimension must remain active")
    return {key: value / active_sum for key, value in weights.items()}
