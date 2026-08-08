#!/usr/bin/env python3
"""Compile small semantic judgment tasks into one deterministic TwinClip report.

The model is allowed to describe observations and make bounded semantic
judgments. It is not allowed to submit derived scores, lane choices, bands, or
confidence values. Those values are computed here and then checked by the
existing final-report validator.
"""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from contracts import (  # noqa: E402
    ANALYSIS_VERSION,
    COMPILER_VERSION,
    DEFAULT_L_WEIGHT,
    DEFAULT_S_WEIGHT,
    SCHEMA_VERSION,
    analysis_id,
    canonical_json,
    default_s_weights,
    provenance_fingerprint,
    reference_bundle_hash,
    sha256_bytes,
    sha256_file,
)
from compute_scores import (  # noqa: E402
    LOGIC_CHECK_IDS,
    LOGIC_CHECK_STATES,
    compute_learning,
    compute_storyboard,
    compute_total,
    confidence_from_decisions,
    score_boundary_distance,
    score_band,
    score_interval,
    select_lane,
)


SEMANTIC_TASK_SCHEMA_VERSION = "twinclip-semantic-task-0.3"
SEMANTIC_RUN_SCHEMA_VERSION = "twinclip-semantic-run-0.3"
TASK_TYPES = {
    "observation",
    "evidence_linking",
    "teaching_point",
    "storyboard_node",
    "relationship",
    "logic_checklist",
    "adaptation",
    "candidate_check",
}
SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

DERIVED_KEYS = {
    "L",
    "S",
    "S_storyboard",
    "S_components",
    "T",
    "T_center",
    "T_range",
    "band",
    "formula_band",
    "provisional",
    "primary_lane",
    "primary_reference_lane",
    "lane_comparison",
    "coverage",
    "coverage_rate",
    "effective_coverage_rate",
    "innovation_rate",
    "surface_share",
    "surface_error_rate",
    "borrowing_summary",
    "confidence",
    "E",
    "M",
    "R",
    "M_components",
    "score_boundary_distance",
    "lane_margin",
    "level",
    "adaptation_diagnostic",
    "compensation_hit_rate",
    "adaptation_required",
    "adaptation_result",
    "primary_failure_dimension",
    "failure_id",
    "absence_verified",
    "manual_pending_count",
    "evidence_count",
    "depths",
    "function_score",
    "element_score",
    "support_score",
    "depth",
    "score",
    "scores",
    "overall_score",
    "l_score",
    "s_score",
    "t_score",
    "reference_bundle",
    "scoring_config",
    "analysis",
    "provenance",
    "execution",
    "multi_reference",
    "teaching_point_assessments",
    "storyboard_node_assessments",
    "relationship_assessments",
    "logic_assessment",
    "logic_checklist",
    "checklist_score",
    "logic_coherence",
    "selection_margin",
    "needs_manual_review",
    "candidate_matches",
    "report",
    "final_report",
    "draft_report",
    "creator_video",
}

OBSERVED_STATES = {"observed", "not_observed", "unclear"}
MINIMUM_STATES = {"met", "not_met", "unclear", "not_applicable"}
FUNCTION_STATES = {"landed", "not_landed", "unclear", "not_applicable"}
TRANSFORMATION_STATES = {"meaningful", "none", "unclear", "not_applicable"}
NODE_FUNCTION_SCORES = {"missing": 0, "fragment": 1, "understandable": 2, "complete": 3}
NODE_ELEMENT_SCORES = {"not_required": None, "missing": 0, "partial": 1, "correct": 2, "clear": 3}
NODE_SUPPORT_SCORES = {"contradictory": 0, "weak": 1, "supportive": 2, "especially_clear": 3}
RELATIONSHIP_SCORES = {"broken": 0, "jump": 1, "complete": 2, "convincing": 3}
EVIDENCE_CHANNELS = {"visual", "onscreen_text", "voiceover"}


class SemanticContractError(ValueError):
    """Raised when a semantic task attempts to cross the model/code boundary."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticContractError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SemanticContractError(f"JSON root must be an object: {path}")
    return value


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def write_json_atomic_no_overwrite(path: Path, value: Any) -> None:
    """Publish a JSON file atomically while failing if the target appeared."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_name, path)
        os.unlink(temporary_name)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def source_record(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise SemanticContractError(f"source is not readable: {path}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise SemanticContractError(f"source must be a regular file: {path}")
    return {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _walk_keys(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in DERIVED_KEYS:
                found.append(f"{path}.{key}")
            found.extend(_walk_keys(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_keys(child, f"{path}[{index}]"))
    return found


def _walk_field_names(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.append(f"{path}.{key}")
            found.extend(_walk_field_names(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_field_names(child, f"{path}[{index}]"))
    return found


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticContractError(f"{label} must be a non-empty string")
    return value


def _required_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SemanticContractError(f"{label} must be an array")
    return value


def _unique_strings(values: Any, label: str) -> list[str]:
    items = _required_list(values, label)
    result: list[str] = []
    for index, value in enumerate(items):
        result.append(_required_string(value, f"{label}[{index}]"))
    if len(result) != len(set(result)):
        raise SemanticContractError(f"{label} must not contain duplicates")
    return result


def lane_points(reference: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw = reference.get("teaching_points")
    if isinstance(raw, list):
        return {"DEFAULT": raw}
    if isinstance(raw, dict) and raw:
        result: dict[str, list[dict[str, Any]]] = {}
        for lane_id, points in raw.items():
            _required_string(lane_id, "reference lane id")
            if not isinstance(points, list) or not points:
                raise SemanticContractError(f"reference teaching_points lane {lane_id} must be a non-empty array")
            result[lane_id] = points
        return result
    raise SemanticContractError("reference_bundle.teaching_points must be a non-empty array or lane map")


def lane_relationships(reference: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return the relationship graph belonging to each reference lane."""
    lanes = lane_points(reference)
    raw_lanes = reference.get("lanes")
    result: dict[str, list[dict[str, Any]]] = {}
    for lane_id in lanes:
        lane = raw_lanes.get(lane_id) if isinstance(raw_lanes, dict) else None
        relationships = lane.get("relationships") if isinstance(lane, dict) else None
        if relationships is None:
            relationships = reference.get("relationships", [])
        if not isinstance(relationships, list):
            raise SemanticContractError(f"reference relationships for lane {lane_id} must be an array")
        result[lane_id] = relationships
    return result


def default_scoring_config(reference: dict[str, Any]) -> dict[str, Any]:
    nodes = reference.get("storyboard_nodes", [])
    relationships = [relationship for lane in lane_relationships(reference).values() for relationship in lane]
    return {
        "l_weight": DEFAULT_L_WEIGHT,
        "s_weight": DEFAULT_S_WEIGHT,
        "s_weights": default_s_weights(
            has_relationships=bool(relationships),
            has_required_elements=any(bool(node.get("required_elements")) for node in nodes if isinstance(node, dict)),
        ),
        "weights_version": "2.0-checklist-default",
        "calibration_registry": None,
    }


def default_anchor_placement() -> dict[str, Any]:
    return {
        "has_anchors": False,
        "anchor_set_id": None,
        "reference_bundle_id": None,
        "reference_bundle_version": None,
        "reference_bundle_hash": None,
        "weights_version": None,
        "lower_anchor": None,
        "upper_anchor": None,
        "anchor_band": None,
        "boundary_clarity": 0.5,
        "formula_conflict": False,
    }


def _digest(value: Any, label: str) -> str:
    value = _required_string(value, label)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
        raise SemanticContractError(f"{label} must be a SHA-256 digest")
    return value


def _task_identity(task: dict[str, Any], run: dict[str, Any]) -> None:
    task_run = task.get("run")
    if not isinstance(task_run, dict):
        raise SemanticContractError("semantic task must contain a run identity object")
    for key in ("run_id", "execution_context_id", "reference_bundle_hash", "creator_video_sha256"):
        if task_run.get(key) != run.get(key):
            raise SemanticContractError(f"semantic task run.{key} does not match the immutable run manifest")
    if run.get("experiment_id") is not None and task_run.get("experiment_id") != run.get("experiment_id"):
        raise SemanticContractError("semantic task run.experiment_id does not match the immutable run manifest")


def validate_task(task: dict[str, Any], run: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    label = str(path or "semantic task")
    top_level_derived = sorted(key for key in task if key in DERIVED_KEYS)
    if top_level_derived:
        raise SemanticContractError(
            f"{label} contains code-derived fields {', '.join(top_level_derived)} outside payload"
        )
    if task.get("schema_version") != SEMANTIC_TASK_SCHEMA_VERSION:
        raise SemanticContractError(f"{label}: schema_version must be {SEMANTIC_TASK_SCHEMA_VERSION}")
    task_type = _required_string(task.get("task_type"), f"{label}.task_type")
    if task_type not in TASK_TYPES:
        raise SemanticContractError(f"{label}: unsupported task_type {task_type}")
    task_id = _required_string(task.get("task_id"), f"{label}.task_id")
    if not SAFE_TASK_ID.fullmatch(task_id):
        raise SemanticContractError(f"{label}.task_id contains unsafe filename characters")
    _task_identity(task, run)
    payload = task.get("payload")
    if not isinstance(payload, dict):
        raise SemanticContractError(f"{label}.payload must be an object")
    derived = _walk_keys(payload)
    if derived:
        raise SemanticContractError(
            f"{label} contains code-derived fields {', '.join(derived)}; submit atomic semantic states only"
        )
    if task_type == "observation":
        observation_only_derived = _walk_field_names(payload)
        forbidden_observation_keys = {
            "creator_video",
            "storyboard_node_ids",
            "candidate_id",
        }
        forbidden = [
            key for key in observation_only_derived
            if key.rsplit(".", 1)[-1] in forbidden_observation_keys
        ]
        if forbidden:
            raise SemanticContractError(
                f"{label} contains code-owned observation fields {', '.join(forbidden)}"
            )
    result = copy.deepcopy(task)
    result["task_id"] = task_id
    result["task_type"] = task_type
    return result


def load_run_manifest(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("schema_version") != SEMANTIC_RUN_SCHEMA_VERSION:
        raise SemanticContractError(f"run manifest must use {SEMANTIC_RUN_SCHEMA_VERSION}")
    for key in (
        "run_id",
        "execution_context_id",
        "reference_bundle_hash",
        "creator_video_sha256",
        "model_id",
        "prompt_version",
        "extraction_version",
        "observation_method",
        "compiler_version",
        "task_schema_version",
    ):
        _required_string(value.get(key), f"run.{key}")
    _digest(value["reference_bundle_hash"], "run.reference_bundle_hash")
    _digest(value["creator_video_sha256"], "run.creator_video_sha256")
    if value["compiler_version"] != COMPILER_VERSION:
        raise SemanticContractError(f"run.compiler_version must be {COMPILER_VERSION}")
    if value["task_schema_version"] != SEMANTIC_TASK_SCHEMA_VERSION:
        raise SemanticContractError(f"run.task_schema_version must be {SEMANTIC_TASK_SCHEMA_VERSION}")
    source_hashes = value.get("source_hashes")
    if not isinstance(source_hashes, dict):
        raise SemanticContractError("run.source_hashes must be an object")
    for source_key in ("breakdown_video", "storyboard_pdf", "creator_video"):
        source = source_hashes.get(source_key)
        if not isinstance(source, dict):
            raise SemanticContractError(f"run.source_hashes.{source_key} must be an object")
        _required_string(source.get("path"), f"run.source_hashes.{source_key}.path")
        _digest(source.get("sha256"), f"run.source_hashes.{source_key}.sha256")
        if not isinstance(source.get("bytes"), int) or isinstance(source.get("bytes"), bool) or source["bytes"] < 0:
            raise SemanticContractError(f"run.source_hashes.{source_key}.bytes must be a non-negative integer")
    if value.get("fixed_evidence_hash") is not None:
        _digest(value.get("fixed_evidence_hash"), "run.fixed_evidence_hash")
        _required_string(value.get("fixed_evidence_path"), "run.fixed_evidence_path")
    elif value.get("fixed_evidence_path") is not None:
        raise SemanticContractError("run.fixed_evidence_path requires run.fixed_evidence_hash")
    if not isinstance(value.get("scoring_config"), dict):
        raise SemanticContractError("run.scoring_config must be an object owned by the run initializer")
    if not isinstance(value.get("anchor_placement"), dict):
        raise SemanticContractError("run.anchor_placement must be an object owned by the run initializer")
    _digest(value.get("scoring_config_hash"), "run.scoring_config_hash")
    _digest(value.get("anchor_placement_hash"), "run.anchor_placement_hash")
    if value["scoring_config_hash"] != sha256_bytes(canonical_json(value["scoring_config"]).encode("utf-8")):
        raise SemanticContractError("run.scoring_config_hash does not match run.scoring_config")
    if value["anchor_placement_hash"] != sha256_bytes(canonical_json(value["anchor_placement"]).encode("utf-8")):
        raise SemanticContractError("run.anchor_placement_hash does not match run.anchor_placement")
    if value.get("calibration_registry_sha256") is not None:
        _digest(value.get("calibration_registry_sha256"), "run.calibration_registry_sha256")
    if value.get("temperature") is not None and (
        not isinstance(value.get("temperature"), (int, float))
        or isinstance(value.get("temperature"), bool)
        or not math.isfinite(float(value.get("temperature")))
    ):
        raise SemanticContractError("run.temperature must be finite or null")
    if value.get("seed") is not None and (not isinstance(value.get("seed"), int) or isinstance(value.get("seed"), bool)):
        raise SemanticContractError("run.seed must be an integer or null")
    if value.get("video_id") is not None:
        _required_string(value.get("video_id"), "run.video_id")
    for key in ("round", "replicate_index"):
        if value.get(key) is not None and (
            not isinstance(value.get(key), int) or isinstance(value.get(key), bool) or value.get(key) <= 0
        ):
            raise SemanticContractError(f"run.{key} must be a positive integer or null")
    return value


def load_task_directory(semantic_dir: Path, run: dict[str, Any]) -> list[dict[str, Any]]:
    tasks_dir = semantic_dir / "tasks"
    if not tasks_dir.is_dir():
        raise SemanticContractError(f"semantic run is missing tasks directory: {tasks_dir}")
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(tasks_dir.glob("*.json")):
        task = validate_task(read_json(path), run, path=path)
        if task["task_id"] in seen:
            raise SemanticContractError(f"duplicate semantic task_id: {task['task_id']}")
        seen.add(task["task_id"])
        tasks.append(task)
    if not tasks:
        raise SemanticContractError(f"semantic run has no task files: {tasks_dir}")
    return tasks


def _tasks_by_type(tasks: list[dict[str, Any]], task_type: str) -> list[dict[str, Any]]:
    return [task for task in tasks if task["task_type"] == task_type]


def _one_task(tasks: list[dict[str, Any]], task_type: str) -> dict[str, Any]:
    matches = _tasks_by_type(tasks, task_type)
    if len(matches) != 1:
        raise SemanticContractError(f"semantic run requires exactly one {task_type} task, found {len(matches)}")
    return matches[0]


def _point_index(points: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise SemanticContractError(f"{label}[{index}] must be an object")
        point_id = _required_string(point.get("id"), f"{label}[{index}].id")
        if point_id in result:
            raise SemanticContractError(f"duplicate reference teaching point: {point_id}")
        result[point_id] = point
    return result


def _node_index(reference: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = reference.get("storyboard_nodes")
    if not isinstance(nodes, list) or not nodes:
        raise SemanticContractError("reference_bundle.storyboard_nodes must be a non-empty array")
    return _point_index(nodes, "reference_bundle.storyboard_nodes")


def _relationship_index(relationships: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(relationships, list):
        raise SemanticContractError("reference relationships must be an array")
    return _point_index(relationships, "reference relationships")


def _validate_evidence_shape(record: dict[str, Any], label: str) -> None:
    for key in (
        "id",
        "start_seconds",
        "end_seconds",
        "visual",
        "onscreen_text",
        "transcript",
        "source_channels",
        "observed_function",
        "coverage_scope",
    ):
        if key not in record:
            raise SemanticContractError(f"{label}.{key} is required")
    if not isinstance(record["start_seconds"], (int, float)) or isinstance(record["start_seconds"], bool):
        raise SemanticContractError(f"{label}.start_seconds must be numeric")
    if not isinstance(record["end_seconds"], (int, float)) or isinstance(record["end_seconds"], bool):
        raise SemanticContractError(f"{label}.end_seconds must be numeric")
    if float(record["end_seconds"]) <= float(record["start_seconds"]):
        raise SemanticContractError(f"{label} must have start_seconds < end_seconds")
    for key in ("visual", "onscreen_text", "transcript", "observed_function", "coverage_scope"):
        _required_string(record[key], f"{label}.{key}")
    channels = _unique_strings(record["source_channels"], f"{label}.source_channels")
    if not channels or set(channels) - EVIDENCE_CHANNELS:
        raise SemanticContractError(
            f"{label}.source_channels must contain one or more of {sorted(EVIDENCE_CHANNELS)}"
        )
    if record.get("evidence_scope") not in {"segment", "full_video"}:
        raise SemanticContractError(f"{label}.evidence_scope must be segment or full_video")
    if record.get("scope_complete") is not True:
        raise SemanticContractError(f"{label}.scope_complete must be true")


def _clarity_and_review(item: dict[str, Any], label: str) -> tuple[str, bool]:
    clarity = item.get("evidence_clarity")
    if clarity not in {"clear", "ambiguous", "unavailable"}:
        raise SemanticContractError(f"{label}.evidence_clarity is invalid")
    manual_review = item.get("manual_review")
    if not isinstance(manual_review, bool):
        raise SemanticContractError(f"{label}.manual_review must be boolean")
    if clarity in {"ambiguous", "unavailable"} and not manual_review:
        raise SemanticContractError(f"{label} must require manual review when evidence is {clarity}")
    return clarity, manual_review


def _ensure_ids(ids: Any, known: set[str], label: str) -> list[str]:
    result = _unique_strings(ids, label)
    unknown = set(result) - known
    if unknown:
        raise SemanticContractError(f"{label} references unknown evidence IDs: {sorted(unknown)}")
    return result


def _state(item: dict[str, Any], key: str, allowed: set[str], label: str) -> str:
    value = _required_string(item.get(key), f"{label}.{key}")
    if value not in allowed:
        raise SemanticContractError(f"{label}.{key} has invalid state {value}")
    return value


def _derive_depth(item: dict[str, Any], label: str) -> tuple[int, bool]:
    observed = _state(item, "observed_state", OBSERVED_STATES, label)
    minimum = _state(item, "minimum_evidence_state", MINIMUM_STATES, label)
    function = _state(item, "function_state", FUNCTION_STATES, label)
    transformation = _state(item, "transformation_state", TRANSFORMATION_STATES, label)
    clarity, manual_review = _clarity_and_review(item, label)
    unclear = "unclear" in {observed, minimum, function, transformation} or clarity != "clear"
    if unclear and not manual_review:
        raise SemanticContractError(f"{label} has uncertain semantic state without manual_review=true")
    if observed == "not_observed":
        return 0, manual_review
    if observed == "unclear" or minimum == "unclear" or function == "unclear" or transformation == "unclear":
        return 0, True
    if observed != "observed":
        raise SemanticContractError(f"{label}.observed_state is inconsistent")
    if minimum != "met" or function != "landed":
        return 1, manual_review
    if transformation == "meaningful":
        return 3, manual_review
    if transformation == "none":
        return 2, manual_review
    raise SemanticContractError(f"{label} has an invalid transformation state")


def _failure_candidate(item: dict[str, Any], allowed: set[str], label: str) -> str | None:
    value = item.get("failure_dimension_candidate")
    if value is not None and value not in allowed:
        raise SemanticContractError(f"{label}.failure_dimension_candidate must be one of {sorted(allowed)} or null")
    return value


def _absence_verified(depth: int, item: dict[str, Any], evidence: dict[str, dict[str, Any]], evidence_ids: list[str]) -> bool:
    return (
        depth == 0
        and item.get("observed_state") == "not_observed"
        and item.get("evidence_clarity") == "clear"
        and any(evidence[evidence_id].get("evidence_scope") == "full_video" for evidence_id in evidence_ids)
    )


def _failure_assignments(assessments: list[dict[str, Any]]) -> None:
    evidence_dimensions: dict[str, set[str]] = {}
    per_item: dict[str, set[str]] = {}
    for item in assessments:
        dimension = item.get("primary_failure_dimension")
        if not dimension:
            continue
        item_id = str(item.get("teaching_point_id") or item.get("storyboard_node_id") or item.get("relationship_id"))
        per_item.setdefault(item_id, set()).add(dimension)
        for evidence_id in item.get("evidence_ids", []):
            evidence_dimensions.setdefault(evidence_id, set()).add(dimension)
    conflicts = {item_id: sorted(values) for item_id, values in per_item.items() if len(values) > 1}
    if conflicts:
        raise SemanticContractError(f"one judgment cannot claim multiple primary failure dimensions: {conflicts}")
    evidence_conflicts = {evidence_id: sorted(values) for evidence_id, values in evidence_dimensions.items() if len(values) > 1}
    if evidence_conflicts:
        raise SemanticContractError(f"one evidence record cannot carry failures in multiple dimensions: {evidence_conflicts}")
    counters: dict[str, int] = {}
    for item in assessments:
        dimension = item.get("primary_failure_dimension")
        if not dimension:
            item["failure_id"] = None
            continue
        counters[dimension] = counters.get(dimension, 0) + 1
        item["failure_id"] = f"FAIL-{dimension}-{counters[dimension]:03d}"


def _merge_observation(
    observation_task: dict[str, Any],
    linking_task: dict[str, Any],
    creator_video: Path,
    duration: float,
    node_ids: set[str],
) -> dict[str, dict[str, Any]]:
    payload = observation_task["payload"]
    records = _required_list(payload.get("evidence_records"), "observation.payload.evidence_records")
    links = _required_list(linking_task["payload"].get("links"), "evidence_linking.payload.links")
    link_map: dict[str, list[str]] = {}
    for index, link in enumerate(links):
        if not isinstance(link, dict):
            raise SemanticContractError(f"evidence_linking.payload.links[{index}] must be an object")
        evidence_id = _required_string(link.get("evidence_id"), f"evidence link {index}.evidence_id")
        if evidence_id in link_map:
            raise SemanticContractError(f"duplicate evidence link: {evidence_id}")
        link_map[evidence_id] = _unique_strings(link.get("storyboard_node_ids", []), f"evidence link {evidence_id}.storyboard_node_ids")
        if set(link_map[evidence_id]) - node_ids:
            raise SemanticContractError(f"evidence link {evidence_id} references an unknown Storyboard node")
    result: dict[str, dict[str, Any]] = {}
    creator = str(creator_video.expanduser().resolve())
    for index, raw in enumerate(records):
        if not isinstance(raw, dict):
            raise SemanticContractError(f"observation evidence {index} must be an object")
        record = copy.deepcopy(raw)
        evidence_id = _required_string(record.get("id"), f"observation evidence {index}.id")
        if evidence_id in result:
            raise SemanticContractError(f"duplicate evidence id: {evidence_id}")
        _validate_evidence_shape(record, f"observation evidence {evidence_id}")
        if record.get("observation_mode") != "blind":
            raise SemanticContractError(f"observation evidence {evidence_id} must be blind")
        if record.get("human_confirmation") not in {"not_required", "confirmed"}:
            raise SemanticContractError(f"observation evidence {evidence_id} must be unconfirmed blind evidence")
        if record.get("candidate_id") is not None:
            raise SemanticContractError(f"observation evidence {evidence_id} cannot have a candidate_id")
        if float(record["end_seconds"]) > duration + 0.02:
            raise SemanticContractError(f"observation evidence {evidence_id} exceeds the creator duration")
        linked_nodes = link_map.get(evidence_id, [])
        if record.get("evidence_scope") == "full_video":
            linked_nodes = sorted(node_ids)
            if not math.isclose(float(record["start_seconds"]), 0.0, abs_tol=0.02) or not math.isclose(
                float(record["end_seconds"]), duration, abs_tol=0.02
            ):
                raise SemanticContractError(f"full_video evidence {evidence_id} must span the creator duration")
        record["creator_video"] = creator
        record["storyboard_node_ids"] = linked_nodes
        record.pop("candidate_id", None)
        result[evidence_id] = record
    unknown_links = set(link_map) - set(result)
    if unknown_links:
        raise SemanticContractError(f"evidence links reference unknown evidence IDs: {sorted(unknown_links)}")
    return result


def _merge_candidates(
    task: dict[str, Any] | None,
    evidence: dict[str, dict[str, Any]],
    creator_video: Path,
    node_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if task is None:
        return [], {}
    payload = task["payload"]
    candidates = _required_list(payload.get("candidates"), "candidate_check.payload.candidates")
    guided_raw = _required_list(payload.get("guided_evidence", []), "candidate_check.payload.guided_evidence")
    guided: dict[str, dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    point_ids: set[str] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise SemanticContractError(f"candidate {index} must be an object")
        candidate_id = _required_string(candidate.get("candidate_id"), f"candidate {index}.candidate_id")
        point_id = _required_string(candidate.get("teaching_point_id"), f"candidate {candidate_id}.teaching_point_id")
        status = candidate.get("status")
        if status not in {"manual_pending", "confirmed", "rejected"}:
            raise SemanticContractError(f"candidate {candidate_id}.status is invalid")
        evidence_ids = _unique_strings(candidate.get("evidence_ids"), f"candidate {candidate_id}.evidence_ids")
        if not evidence_ids:
            raise SemanticContractError(f"candidate {candidate_id} must name guided evidence IDs")
        _required_string(candidate.get("reason"), f"candidate {candidate_id}.reason")
        point_ids.add(point_id)
        result.append(copy.deepcopy(candidate))
    for index, raw in enumerate(guided_raw):
        if not isinstance(raw, dict):
            raise SemanticContractError(f"guided evidence {index} must be an object")
        record = copy.deepcopy(raw)
        evidence_id = _required_string(record.get("id"), f"guided evidence {index}.id")
        if evidence_id in evidence or evidence_id in guided:
            raise SemanticContractError(f"guided evidence ID collides with another evidence record: {evidence_id}")
        _validate_evidence_shape(record, f"guided evidence {evidence_id}")
        candidate_id = _required_string(record.get("candidate_id"), f"guided evidence {evidence_id}.candidate_id")
        if record.get("observation_mode") != "guided":
            raise SemanticContractError(f"guided evidence {evidence_id} must use observation_mode=guided")
        expected_confirmation = next((item["status"] for item in result if item["candidate_id"] == candidate_id), None)
        if expected_confirmation is None:
            raise SemanticContractError(f"guided evidence {evidence_id} references unknown candidate {candidate_id}")
        confirmation = {"manual_pending": "pending", "confirmed": "confirmed", "rejected": "rejected"}[expected_confirmation]
        if record.get("human_confirmation") != confirmation:
            raise SemanticContractError(f"guided evidence {evidence_id} confirmation must be {confirmation}")
        linked_nodes = _unique_strings(record.get("storyboard_node_ids", []), f"guided evidence {evidence_id}.storyboard_node_ids")
        if set(linked_nodes) - node_ids:
            raise SemanticContractError(f"guided evidence {evidence_id} references an unknown Storyboard node")
        record["creator_video"] = str(creator_video.expanduser().resolve())
        guided[evidence_id] = record
    candidate_ids = {item["candidate_id"] for item in result}
    for item in result:
        item["evidence_ids"] = [evidence_id for evidence_id in item["evidence_ids"] if evidence_id in guided]
        if not item["evidence_ids"]:
            raise SemanticContractError(f"candidate {item['candidate_id']} has no matching guided evidence")
    if set(guided) - {evidence_id for item in result for evidence_id in item["evidence_ids"]}:
        raise SemanticContractError("guided evidence must be referenced by a candidate")
    if len(candidate_ids) != len(result):
        raise SemanticContractError("candidate IDs must be unique")
    return result, guided


def _teaching_assessments(
    task: dict[str, Any],
    points: dict[str, dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    lane_id: str,
) -> list[dict[str, Any]]:
    payload = task["payload"]
    if payload.get("lane_id") != lane_id:
        raise SemanticContractError(f"teaching task {task['task_id']} lane_id does not match its reference lane")
    raw_items = _required_list(payload.get("judgments"), f"teaching task {lane_id}.payload.judgments")
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise SemanticContractError(f"teaching task {lane_id} judgment {index} must be an object")
        point_id = _required_string(raw.get("teaching_point_id"), f"teaching judgment {index}.teaching_point_id")
        if point_id not in points:
            raise SemanticContractError(f"teaching task {lane_id} references unknown point {point_id}")
        if point_id in by_id:
            raise SemanticContractError(f"duplicate teaching judgment {point_id} in lane {lane_id}")
        evidence_ids = _ensure_ids(raw.get("evidence_ids"), set(evidence), f"teaching judgment {point_id}.evidence_ids")
        depth, manual_review = _derive_depth(raw, f"teaching judgment {point_id}")
        clarity, explicit_manual = _clarity_and_review(raw, f"teaching judgment {point_id}")
        manual_review = manual_review or explicit_manual
        failure = _failure_candidate(raw, {"L", None}, f"teaching judgment {point_id}")
        if failure and not evidence_ids:
            raise SemanticContractError(f"teaching judgment {point_id} cannot assign a failure without evidence")
        if depth == 0 and raw.get("observed_state") == "not_observed" and clarity == "clear" and not any(
            evidence[evidence_id].get("evidence_scope") == "full_video" for evidence_id in evidence_ids
        ):
            raise SemanticContractError(f"teaching judgment {point_id} needs full_video evidence for a clear absence")
        item = {
            "teaching_point_id": point_id,
            "depth": depth,
            "evidence_ids": evidence_ids,
            "reason": _required_string(raw.get("reason"), f"teaching judgment {point_id}.reason"),
            "done_well": raw.get("done_well"),
            "missing_or_misused": raw.get("missing_or_misused"),
            "manual_review": manual_review,
            "evidence_clarity": clarity,
            "absence_verified": _absence_verified(depth, raw, evidence, evidence_ids),
            "primary_failure_dimension": failure,
            "failure_id": None,
            "adaptation_required": "no",
            "adaptation_result": "not_needed",
        }
        by_id[point_id] = item
    if set(by_id) != set(points):
        missing = sorted(set(points) - set(by_id))
        extra = sorted(set(by_id) - set(points))
        raise SemanticContractError(f"teaching task {lane_id} must cover every point exactly once; missing={missing}, extra={extra}")
    return [by_id[point_id] for point_id in points]


def _node_assessments(task: dict[str, Any], nodes: dict[str, dict[str, Any]], evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    raw_items = _required_list(task["payload"].get("judgments"), "storyboard task.payload.judgments")
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise SemanticContractError(f"Storyboard judgment {index} must be an object")
        node_id = _required_string(raw.get("storyboard_node_id"), f"Storyboard judgment {index}.storyboard_node_id")
        if node_id not in nodes:
            raise SemanticContractError(f"Storyboard task references unknown node {node_id}")
        if node_id in by_id:
            raise SemanticContractError(f"duplicate Storyboard judgment {node_id}")
        evidence_ids = _ensure_ids(raw.get("evidence_ids"), set(evidence), f"Storyboard judgment {node_id}.evidence_ids")
        clarity, manual_review = _clarity_and_review(raw, f"Storyboard judgment {node_id}")
        function_state = _state(raw, "function_state", set(NODE_FUNCTION_SCORES), f"Storyboard judgment {node_id}")
        element_state = _state(raw, "element_state", set(NODE_ELEMENT_SCORES), f"Storyboard judgment {node_id}")
        support_state = _state(raw, "support_state", set(NODE_SUPPORT_SCORES), f"Storyboard judgment {node_id}")
        has_required = bool(nodes[node_id].get("required_elements"))
        if has_required and element_state == "not_required":
            raise SemanticContractError(f"Storyboard judgment {node_id} cannot mark required elements not_required")
        if not has_required and element_state != "not_required":
            raise SemanticContractError(f"Storyboard judgment {node_id} must mark absent required elements not_required")
        failure = _failure_candidate(raw, {"S", None}, f"Storyboard judgment {node_id}")
        if failure and not evidence_ids:
            raise SemanticContractError(f"Storyboard judgment {node_id} cannot assign a failure without evidence")
        function_score = NODE_FUNCTION_SCORES[function_state]
        element_score = NODE_ELEMENT_SCORES[element_state]
        support_score = NODE_SUPPORT_SCORES[support_state]
        positive = any(value is not None and value > 0 for value in (function_score, element_score, support_score))
        absence_verified = not positive and clarity == "clear" and any(
            evidence[evidence_id].get("evidence_scope") == "full_video" for evidence_id in evidence_ids
        )
        if not positive and clarity == "clear" and not absence_verified:
            raise SemanticContractError(f"Storyboard judgment {node_id} needs full_video evidence for a clear absence")
        by_id[node_id] = {
            "storyboard_node_id": node_id,
            "function_score": function_score,
            "element_score": element_score,
            "support_score": support_score,
            "evidence_ids": evidence_ids,
            "reason": _required_string(raw.get("reason"), f"Storyboard judgment {node_id}.reason"),
            "manual_review": manual_review,
            "evidence_clarity": clarity,
            "absence_verified": absence_verified,
            "primary_failure_dimension": failure,
            "failure_id": None,
        }
    if set(by_id) != set(nodes):
        raise SemanticContractError(f"Storyboard task must cover every node exactly once; missing={sorted(set(nodes)-set(by_id))}")
    return [by_id[node_id] for node_id in nodes]


def _relationship_assessments(
    task: dict[str, Any], relationships: dict[str, dict[str, Any]], evidence: dict[str, dict[str, Any]], lane_id: str
) -> list[dict[str, Any]]:
    if task["payload"].get("lane_id") != lane_id:
        raise SemanticContractError(f"relationship task {task['task_id']} lane_id does not match its reference lane")
    raw_items = _required_list(task["payload"].get("judgments"), "relationship task.payload.judgments")
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise SemanticContractError(f"relationship judgment {index} must be an object")
        relationship_id = _required_string(raw.get("relationship_id"), f"relationship judgment {index}.relationship_id")
        if relationship_id not in relationships:
            raise SemanticContractError(f"relationship task references unknown relationship {relationship_id}")
        if relationship_id in by_id:
            raise SemanticContractError(f"duplicate relationship judgment {relationship_id}")
        evidence_ids = _ensure_ids(raw.get("evidence_ids"), set(evidence), f"relationship judgment {relationship_id}.evidence_ids")
        clarity, manual_review = _clarity_and_review(raw, f"relationship judgment {relationship_id}")
        state = _state(raw, "logic_state", set(RELATIONSHIP_SCORES), f"relationship judgment {relationship_id}")
        failure = _failure_candidate(raw, {"S", None}, f"relationship judgment {relationship_id}")
        if failure and not evidence_ids:
            raise SemanticContractError(f"relationship judgment {relationship_id} cannot assign a failure without evidence")
        score = RELATIONSHIP_SCORES[state]
        positive = score > 0
        if positive and not evidence_ids:
            raise SemanticContractError(f"relationship judgment {relationship_id} needs evidence")
        absence_verified = not positive and clarity == "clear" and any(
            evidence[evidence_id].get("evidence_scope") == "full_video" for evidence_id in evidence_ids
        )
        if not positive and clarity == "clear" and not absence_verified:
            raise SemanticContractError(f"relationship judgment {relationship_id} needs full_video evidence for a clear absence")
        by_id[relationship_id] = {
            "relationship_id": relationship_id,
            "score": score,
            "evidence_ids": evidence_ids,
            "reason": _required_string(raw.get("reason"), f"relationship judgment {relationship_id}.reason"),
            "manual_review": manual_review,
            "evidence_clarity": clarity,
            "absence_verified": absence_verified,
            "primary_failure_dimension": failure,
            "failure_id": None,
        }
    if set(by_id) != set(relationships):
        raise SemanticContractError(f"relationship task must cover every relationship exactly once; missing={sorted(set(relationships)-set(by_id))}")
    return [by_id[relationship_id] for relationship_id in relationships]


def _logic_checklist_assessments(
    task: dict[str, Any], evidence: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate five independent sales-logic checks from one small task."""
    raw_items = _required_list(task["payload"].get("checks"), "logic_checklist task.payload.checks")
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise SemanticContractError(f"logic checklist item {index} must be an object")
        check_id = _required_string(raw.get("check_id"), f"logic checklist item {index}.check_id")
        if check_id not in LOGIC_CHECK_IDS:
            raise SemanticContractError(f"logic checklist item {check_id} has an invalid check_id")
        if check_id in by_id:
            raise SemanticContractError(f"duplicate logic checklist item {check_id}")
        state = _state(raw, "state", LOGIC_CHECK_STATES, f"logic checklist item {check_id}")
        evidence_ids = _ensure_ids(raw.get("evidence_ids"), set(evidence), f"logic checklist item {check_id}.evidence_ids")
        clarity, manual_review = _clarity_and_review(raw, f"logic checklist item {check_id}")
        if state == "unclear" and not manual_review:
            raise SemanticContractError(f"logic checklist item {check_id} must require manual review when unclear")
        if state == "met":
            if not evidence_ids:
                raise SemanticContractError(f"logic checklist item {check_id} needs evidence when met")
            if any(evidence[evidence_id].get("evidence_scope") == "full_video" for evidence_id in evidence_ids):
                raise SemanticContractError(f"logic checklist item {check_id} cannot use full_video evidence for a positive check")
        absence_verified = state == "not_met" and clarity == "clear" and any(
            evidence[evidence_id].get("evidence_scope") == "full_video" for evidence_id in evidence_ids
        )
        if state == "not_met" and clarity == "clear" and not absence_verified:
            raise SemanticContractError(f"logic checklist item {check_id} needs full_video evidence to verify a clear negative")
        # A positive semantic state is not enough to score when its evidence
        # is ambiguous or unavailable. Keep the item conservative until a
        # reviewer supplies clear evidence, just like teaching-point depth.
        score = 1 if state == "met" and clarity == "clear" else 0
        by_id[check_id] = {
            "check_id": check_id,
            "state": state,
            "score": score,
            "evidence_ids": evidence_ids,
            "reason": _required_string(raw.get("reason"), f"logic checklist item {check_id}.reason"),
            "manual_review": manual_review,
            "evidence_clarity": clarity,
            "absence_verified": absence_verified,
        }
    if set(by_id) != set(LOGIC_CHECK_IDS):
        raise SemanticContractError(
            f"logic checklist must cover exactly {list(LOGIC_CHECK_IDS)}; "
            f"missing={sorted(set(LOGIC_CHECK_IDS)-set(by_id))}, extra={sorted(set(by_id)-set(LOGIC_CHECK_IDS))}"
        )
    return [by_id[check_id] for check_id in LOGIC_CHECK_IDS]


def _adaptation_assessments(task: dict[str, Any], points: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    raw_items = _required_list(task["payload"].get("judgments"), "adaptation task.payload.judgments")
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise SemanticContractError(f"adaptation judgment {index} must be an object")
        point_id = _required_string(raw.get("teaching_point_id"), f"adaptation judgment {index}.teaching_point_id")
        if point_id not in points:
            raise SemanticContractError(f"adaptation task references unknown point {point_id}")
        if point_id in result:
            raise SemanticContractError(f"duplicate adaptation judgment {point_id}")
        applicability = _state(raw, "applicability_state", {"not_required", "required", "unclear"}, f"adaptation judgment {point_id}")
        compensation = _state(raw, "compensation_state", {"not_needed", "successful", "partial", "failed", "pending"}, f"adaptation judgment {point_id}")
        clarity, manual_review = _clarity_and_review(raw, f"adaptation judgment {point_id}")
        failure = _failure_candidate(raw, {"A", None}, f"adaptation judgment {point_id}")
        if applicability == "not_required" and compensation != "not_needed":
            raise SemanticContractError(f"adaptation judgment {point_id}: not_required requires not_needed")
        if applicability == "required" and compensation == "not_needed":
            raise SemanticContractError(f"adaptation judgment {point_id}: required cannot be not_needed")
        if applicability == "unclear" and compensation != "pending":
            raise SemanticContractError(f"adaptation judgment {point_id}: unclear requires pending")
        if failure and applicability != "required":
            raise SemanticContractError(f"adaptation judgment {point_id}: A failure requires applicability_state=required")
        result[point_id] = {
            "required": applicability,
            "result": compensation,
            "manual_review": manual_review,
            "evidence_clarity": clarity,
            "primary_failure_dimension": failure,
            "reason": _required_string(raw.get("reason"), f"adaptation judgment {point_id}.reason"),
        }
    if set(result) != set(points):
        raise SemanticContractError(f"adaptation task must cover every selected reference point exactly once; missing={sorted(set(points)-set(result))}")
    return result


def _derived_confidence(
    decisions: list[dict[str, Any]],
    pending_candidates: int,
    *,
    has_anchors: bool,
    boundary_clarity: float,
    score_boundary_distance_value: float | None = None,
    lane_margin: float | None = None,
) -> dict[str, Any]:
    clear_supported = sum(
        item.get("evidence_clarity") == "clear"
        and (bool(item.get("evidence_ids")) or item.get("absence_verified") is True)
        for item in decisions
    )
    decision_count = len(decisions)
    manual_count = sum(bool(item.get("manual_review")) for item in decisions)
    try:
        return confidence_from_decisions(
            clear_supported=clear_supported,
            decision_count=decision_count,
            manual_review_count=manual_count,
            pending_candidates=pending_candidates,
            has_anchors=has_anchors,
            boundary_clarity=boundary_clarity,
            score_boundary_distance_value=score_boundary_distance_value,
            lane_margin=lane_margin,
        )
    except ValueError as exc:
        raise SemanticContractError(str(exc)) from exc


def _adaptation_summary(values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts = {"successful": 0, "partial": 0, "failed": 0, "pending": 0, "unclear": 0}
    required_count = 0
    for item in values.values():
        if item["required"] == "required":
            required_count += 1
            counts[item["result"]] += 1
        elif item["required"] == "unclear":
            counts["unclear"] += 1
    confirmed = counts["successful"] + counts["partial"] + counts["failed"]
    if counts["unclear"] or counts["pending"]:
        status = "unknown"
    elif counts["failed"] and not counts["successful"]:
        status = "mismatch"
    elif counts["failed"] or counts["partial"]:
        status = "conditional"
    else:
        status = "aligned"
    return {
        "required_count": required_count,
        "successful_count": counts["successful"],
        "partial_count": counts["partial"],
        "failed_count": counts["failed"],
        "pending_count": counts["pending"],
        "unclear_count": counts["unclear"],
        "compensation_hit_rate": counts["successful"] / confirmed if confirmed else None,
        "status": status,
        "summary": f"Derived from {required_count} point-level adaptation judgments; status={status}.",
    }


def _next_actions(points: list[dict[str, Any]], teaching: list[dict[str, Any]]) -> list[str]:
    point_by_id = {point["id"]: point for point in points}
    actions: list[str] = []
    for item in teaching:
        if item["depth"] < 2:
            point = point_by_id[item["teaching_point_id"]]
            actions.append(f"加强教学点“{point.get('name', item['teaching_point_id'])}”的最低证据和功能闭环。")
        if len(actions) == 3:
            break
    return actions or ["保持现有机制，并用更清晰的证据强化转化闭环。"]


def _build_scoring_config(run: dict[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(run["scoring_config"])
    for key in ("l_weight", "s_weight", "s_weights", "weights_version", "calibration_registry"):
        if key not in config:
            raise SemanticContractError(f"scoring config missing {key}")
    return config


def _build_anchor_placement(run: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(run["anchor_placement"])


def compile_report(
    *,
    reference_path: Path,
    breakdown_video: Path,
    storyboard_pdf: Path,
    creator_video: Path,
    semantic_dir: Path,
    duration: float,
    interval: float,
    max_frames: int,
) -> dict[str, Any]:
    reference = copy.deepcopy(read_json(reference_path))
    if reference.get("status") != "locked":
        raise SemanticContractError("a scored reference bundle must have status=locked")
    breakdown = source_record(breakdown_video)
    storyboard = source_record(storyboard_pdf)
    creator = source_record(creator_video)
    expected_source_hashes = reference.get("source_content_hashes")
    if not isinstance(expected_source_hashes, dict):
        raise SemanticContractError("reference bundle must contain source_content_hashes")
    for key, actual in (("breakdown_video", breakdown), ("storyboard_pdf", storyboard)):
        expected = expected_source_hashes.get(key)
        if not isinstance(expected, dict) or expected.get("sha256") != actual["sha256"] or expected.get("bytes") != actual["bytes"]:
            raise SemanticContractError(f"reference source identity does not match current {key}")
    bundle_hash = reference.get("content_hash")
    if bundle_hash != reference_bundle_hash(reference):
        raise SemanticContractError("reference bundle content_hash does not match its locked graph")
    reference["source_inputs"] = {
        "breakdown_video": str(breakdown_video.expanduser().resolve()),
        "storyboard_pdf": str(storyboard_pdf.expanduser().resolve()),
    }
    run = load_run_manifest(semantic_dir / "run.json")
    if run["reference_bundle_hash"] != bundle_hash or run["creator_video_sha256"] != creator["sha256"]:
        raise SemanticContractError("semantic run identity does not match the reference or creator video")
    expected_run_sources = {
        "breakdown_video": breakdown,
        "storyboard_pdf": storyboard,
        "creator_video": creator,
    }
    for source_key, actual in expected_run_sources.items():
        recorded = run["source_hashes"][source_key]
        if (
            recorded.get("path") != actual["path"]
            or recorded.get("sha256") != actual["sha256"]
            or recorded.get("bytes") != actual["bytes"]
        ):
            raise SemanticContractError(f"semantic run source identity does not match current {source_key}")
    if run.get("fixed_evidence_hash") is not None:
        fixed_evidence = source_record(Path(run["fixed_evidence_path"]))
        if fixed_evidence["sha256"] != run["fixed_evidence_hash"]:
            raise SemanticContractError("fixed evidence changed after semantic run initialization")
    for sidecar_name in ("scoring-config.json", "anchor-placement.json"):
        if (semantic_dir / sidecar_name).exists():
            raise SemanticContractError(
                f"{sidecar_name} is a mutable sidecar; pass locked configuration to semantic_run.py init"
            )
    tasks = load_task_directory(semantic_dir, run)
    node_map = _node_index(reference)
    lanes = lane_points(reference)
    relationships_by_lane = lane_relationships(reference)
    observation_task = _one_task(tasks, "observation")
    linking_task = _one_task(tasks, "evidence_linking")
    node_task = _one_task(tasks, "storyboard_node")
    logic_task = _one_task(tasks, "logic_checklist")
    adaptation_task = _one_task(tasks, "adaptation")
    evidence = _merge_observation(observation_task, linking_task, creator_video, duration, set(node_map))
    candidate_tasks = _tasks_by_type(tasks, "candidate_check")
    if len(candidate_tasks) > 1:
        raise SemanticContractError("at most one candidate_check task is allowed")
    candidates, guided = _merge_candidates(candidate_tasks[0] if candidate_tasks else None, evidence, creator_video, set(node_map))
    evidence.update(guided)
    node_assessments = _node_assessments(node_task, node_map, evidence)
    logic_check_assessments = _logic_checklist_assessments(logic_task, evidence)
    relationship_assessments_by_lane: dict[str, list[dict[str, Any]]] = {}
    relationship_maps = {
        lane_id: _relationship_index(relationships)
        for lane_id, relationships in relationships_by_lane.items()
    }
    relationship_tasks = _tasks_by_type(tasks, "relationship")
    if len(relationship_tasks) != len(lanes):
        raise SemanticContractError(
            f"semantic run requires one relationship task per reference lane, found {len(relationship_tasks)} for {len(lanes)} lanes"
        )
    for lane_id in lanes:
        relationship_task = _one_task(
            [task for task in relationship_tasks if task["payload"].get("lane_id") == lane_id],
            "relationship",
        )
        relationship_assessments_by_lane[lane_id] = _relationship_assessments(
            relationship_task, relationship_maps[lane_id], evidence, lane_id
        )
    adaptation_by_point = _adaptation_assessments(
        adaptation_task,
        {point_id: point for points in lanes.values() for point_id, point in _point_index(points, "reference teaching points").items()},
    )
    point_assessments_by_lane: dict[str, list[dict[str, Any]]] = {}
    for lane_id, points in lanes.items():
        point_assessments_by_lane[lane_id] = _teaching_assessments(
            _one_task([task for task in tasks if task["task_type"] == "teaching_point" and task["payload"].get("lane_id") == lane_id], "teaching_point"),
            _point_index(points, f"reference teaching_points[{lane_id}]"),
            evidence,
            lane_id,
        )
    # Adaptation is evaluated against every lane point. The task has one item
    # per point; merge only the items used by the selected lane below.
    for lane_id, assessments in point_assessments_by_lane.items():
        for assessment in assessments:
            adaptation = adaptation_by_point.get(assessment["teaching_point_id"])
            if adaptation is None:
                raise SemanticContractError(f"missing adaptation judgment for {assessment['teaching_point_id']}")
            assessment["adaptation_required"] = {"not_required": "no", "required": "yes", "unclear": "unclear"}[adaptation["required"]]
            assessment["adaptation_result"] = adaptation["result"]
            if adaptation["primary_failure_dimension"]:
                if assessment["primary_failure_dimension"] is not None:
                    raise SemanticContractError(f"point {assessment['teaching_point_id']} has both L and A failure candidates")
                assessment["primary_failure_dimension"] = "A"
                assessment["manual_review"] = assessment["manual_review"] or adaptation["manual_review"]
                if adaptation["evidence_clarity"] != "clear":
                    assessment["evidence_clarity"] = adaptation["evidence_clarity"]
            elif adaptation["manual_review"]:
                assessment["manual_review"] = True
    all_failure_assessments = (
        [item for items in point_assessments_by_lane.values() for item in items]
        + node_assessments
        + [item for items in relationship_assessments_by_lane.values() for item in items]
    )
    _failure_assignments(all_failure_assessments)
    s_weights = _build_scoring_config(run)
    anchor_placement = _build_anchor_placement(run)
    if s_weights.get("calibration_registry") is not None:
        registry_path = Path(str(s_weights["calibration_registry"])).expanduser().resolve()
        if run.get("calibration_registry_sha256") != sha256_file(registry_path):
            raise SemanticContractError("calibration registry changed after semantic run initialization")
    lane_summaries: dict[str, dict[str, Any]] = {}
    for lane_id, points in lanes.items():
        assessments = point_assessments_by_lane[lane_id]
        depths = [item["depth"] for item in assessments]
        l_score, l_stats = compute_learning(depths)
        if l_score is None:
            raise SemanticContractError(f"reference lane {lane_id} has no teaching points")
        relationship_assessments = relationship_assessments_by_lane[lane_id]
        checklist_scores = {item["check_id"]: item["score"] for item in logic_check_assessments}
        try:
            s_score, s_stats = compute_storyboard(
                node_assessments,
                checklist_scores,
                s_weights=s_weights["s_weights"],
            )
            t_score = compute_total(
                l_score,
                s_score,
                l_weight=float(s_weights["l_weight"]),
                s_weight=float(s_weights["s_weight"]),
            )
        except ValueError as exc:
            raise SemanticContractError(str(exc)) from exc
        lane_summaries[lane_id] = {
            "label": reference.get("lanes", {}).get(lane_id, {}).get("label", lane_id) if isinstance(reference.get("lanes"), dict) else lane_id,
            "L": l_score,
            "S_storyboard": s_score,
            "T_center": t_score,
            "effective_coverage_rate": l_stats["effective_coverage_rate"],
            "coverage_rate": l_stats["coverage_rate"],
            "depths": depths,
            "S_components": s_stats,
        }
    lane_selection = select_lane(lane_summaries)
    primary_lane = lane_selection["chosen_lane"]
    t_candidates = [lane_id for lane_id in lane_summaries if math.isclose(
        lane_summaries[lane_id]["effective_coverage_rate"],
        lane_summaries[primary_lane]["effective_coverage_rate"],
        abs_tol=1e-9,
    ) and math.isclose(lane_summaries[lane_id]["T_center"], lane_summaries[primary_lane]["T_center"], abs_tol=1e-9)]
    selected_points = lanes[primary_lane]
    selected_teaching = point_assessments_by_lane[primary_lane]
    relationship_assessments = relationship_assessments_by_lane[primary_lane]
    s_score = lane_summaries[primary_lane]["S_storyboard"]
    s_stats = copy.deepcopy(lane_summaries[primary_lane]["S_components"])
    selected_point_ids = {point["id"] for point in selected_points}
    selected_candidates = [item for item in candidates if item.get("teaching_point_id") in selected_point_ids]
    selected_candidate_ids = {item["candidate_id"] for item in selected_candidates}
    selected_guided = {evidence_id: record for evidence_id, record in guided.items() if record.get("candidate_id") in selected_candidate_ids}
    final_evidence = {**{key: value for key, value in evidence.items() if key not in guided}, **selected_guided}
    positive_logic_evidence_ids = sorted({
        evidence_id
        for item in logic_check_assessments
        if item["score"] > 0
        for evidence_id in item["evidence_ids"]
    })
    negative_logic_evidence_ids = sorted({
        evidence_id
        for item in logic_check_assessments
        if item["score"] == 0
        for evidence_id in item["evidence_ids"]
    })
    logic_score = sum(item["score"] for item in logic_check_assessments) / len(logic_check_assessments) * 3
    logic_positive = logic_score > 0
    logic_clarity = "clear" if all(item["evidence_clarity"] == "clear" for item in logic_check_assessments) else "ambiguous"
    logic_manual = any(item["manual_review"] for item in logic_check_assessments)
    logic_evidence_ids = positive_logic_evidence_ids if logic_positive else negative_logic_evidence_ids
    logic_assessment = {
        "score": logic_score,
        "checklist": copy.deepcopy(logic_check_assessments),
        "checklist_mean": logic_score / 3,
        "relationship_ids": list(relationship_maps[primary_lane]),
        "evidence_ids": logic_evidence_ids,
        "reason": "Derived deterministically from five independently judged sales-logic checks.",
        "manual_review": logic_manual,
        "evidence_clarity": logic_clarity,
        "absence_verified": not logic_positive and all(item["absence_verified"] for item in logic_check_assessments),
        "primary_failure_dimension": None,
        "failure_id": None,
    }
    decisions = selected_teaching + node_assessments + relationship_assessments + logic_check_assessments
    pending_count = sum(item.get("status") == "manual_pending" for item in selected_candidates)
    t_center = lane_summaries[primary_lane]["T_center"]
    confidence = _derived_confidence(
        decisions,
        pending_count,
        has_anchors=bool(anchor_placement.get("has_anchors")),
        boundary_clarity=anchor_placement.get("boundary_clarity", 0.5),
        score_boundary_distance_value=score_boundary_distance(t_center),
        lane_margin=lane_selection.get("margin"),
    )
    formula_band = score_band(t_center)
    selected_adaptation = {item["teaching_point_id"]: adaptation_by_point[item["teaching_point_id"]] for item in selected_teaching}
    adaptation = _adaptation_summary(selected_adaptation)
    for item in selected_teaching:
        adaptation_reason = selected_adaptation[item["teaching_point_id"]]["reason"]
        if item.get("missing_or_misused") is None and item["depth"] < 2:
            item["missing_or_misused"] = adaptation_reason
        if item.get("done_well") is None and item["depth"] >= 2:
            item["done_well"] = item["reason"]
    review_pending = bool(
        pending_count
        or any(item["manual_review"] for item in decisions)
        or confidence["level"] == "low"
        or lane_selection["needs_manual_review"]
        or anchor_placement.get("formula_conflict") is True
    )
    scores = {
        "L": lane_summaries[primary_lane]["L"],
        "S": s_score,
        "T_center": t_center,
        "T_range": score_interval(t_center, confidence["level"]),
        "formula_band": formula_band,
        "band": formula_band,
        "provisional": not bool(anchor_placement.get("has_anchors")),
    }
    if anchor_placement.get("has_anchors"):
        scores["band"] = anchor_placement.get("anchor_band")
    provenance = {
        "analysis_version": ANALYSIS_VERSION,
        "observation_method": run["observation_method"],
        "model_id": run["model_id"],
        "prompt_version": run["prompt_version"],
        "extraction_version": run["extraction_version"],
        "compiler_version": COMPILER_VERSION,
        "scoring_config_hash": run["scoring_config_hash"],
        "anchor_placement_hash": run["anchor_placement_hash"],
        "calibration_registry_sha256": run.get("calibration_registry_sha256"),
        "reference_bundle_hash": bundle_hash,
        "media_preparation": {"manifest_schema_version": "1.2", "interval_seconds": interval, "max_frames": max_frames},
        "source_hashes": {"breakdown_video": breakdown, "storyboard_pdf": storyboard, "creator_video": creator},
    }
    provenance["method_fingerprint"] = provenance_fingerprint(provenance)
    analysis = {
        "creator_videos": [str(creator_video.expanduser().resolve())],
        "media_durations": {str(creator_video.expanduser().resolve()): duration},
        "evidence_records": list(final_evidence.values()),
        "teaching_point_assessments": selected_teaching,
        "storyboard_node_assessments": node_assessments,
        "logic_assessment": logic_assessment,
        "relationship_assessments": relationship_assessments,
        "candidate_matches": selected_candidates,
        "S_components": s_stats,
        "scores": scores,
        "coverage": {
            "coverage_rate": lane_summaries[primary_lane]["coverage_rate"],
            "effective_coverage_rate": lane_summaries[primary_lane]["effective_coverage_rate"],
            "innovation_rate": sum(depth == 3 for depth in lane_summaries[primary_lane]["depths"]) / len(selected_teaching),
            "surface_share": sum(depth == 1 for depth in lane_summaries[primary_lane]["depths"]) / len(selected_teaching),
            "surface_error_rate": sum(depth == 1 for depth in lane_summaries[primary_lane]["depths"]) / max(sum(depth >= 1 for depth in lane_summaries[primary_lane]["depths"]), 1),
        },
        "borrowing_summary": {
            "missing": sum(depth == 0 for depth in lane_summaries[primary_lane]["depths"]),
            "surface": sum(depth == 1 for depth in lane_summaries[primary_lane]["depths"]),
            "effective": sum(depth == 2 for depth in lane_summaries[primary_lane]["depths"]),
            "innovative": sum(depth == 3 for depth in lane_summaries[primary_lane]["depths"]),
        },
        "confidence": confidence,
        "anchor_placement": anchor_placement,
        "lane_selection": lane_selection,
        "review_status": "pending" if review_pending else "completed",
        "adaptation_diagnostic": adaptation,
        "why_not_higher": next((f"教学点“{point.get('name', point['id'])}”尚未达到功能性迁移。" for point, item in zip(selected_points, selected_teaching) if item["depth"] < 3), "当前各教学点均达到较高迁移深度。"),
        "why_not_lower": next((f"教学点“{point.get('name', point['id'])}”已经出现功能性迁移。" for point, item in zip(selected_points, selected_teaching) if item["depth"] >= 2), "当前没有足够的功能性迁移证据。"),
        "next_actions": _next_actions(selected_points, selected_teaching),
        "provenance": provenance,
        "analysis_id": analysis_id(bundle_hash, creator["sha256"], provenance["method_fingerprint"]),
        "execution": {
            "run_id": run["run_id"],
            "execution_context_id": run["execution_context_id"],
            "temperature": run.get("temperature"),
            "seed": run.get("seed"),
            "task_ids": sorted(task["task_id"] for task in tasks),
        },
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "reference_bundle": copy.deepcopy(reference),
        "scoring_config": s_weights,
        "analysis": analysis,
    }
    if len(lanes) > 1:
        report["multi_reference"] = {
            "run_mode": "deterministic_multi_reference",
            "primary_reference_lane": primary_lane,
            "selection_rule": "effective coverage, then T, then declared lane order",
            "storyboard_scope": "shared reference-bundle Storyboard nodes and relationships",
            "lane_comparison": lane_summaries,
            "selection_margin": {
                "value": lane_selection["margin"],
                "basis": lane_selection["margin_basis"],
                "threshold": 0.05,
                "needs_manual_review": lane_selection["needs_manual_review"],
            },
            "selection_tie": len(t_candidates) > 1,
            "tie_breaker": "declared_lane_order" if len(t_candidates) > 1 else None,
        }
    return report


def stability_result(report: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Project a compiled report into the repeat-experiment result shape."""
    analysis = report["analysis"]
    multi_reference = report.get("multi_reference")
    if isinstance(multi_reference, dict):
        primary_lane = multi_reference["primary_reference_lane"]
        lane_comparison = copy.deepcopy(multi_reference["lane_comparison"])
        for lane in lane_comparison.values():
            lane["S"] = lane.pop("S_storyboard")
    else:
        primary_lane = "DEFAULT"
        point_depths = [item["depth"] for item in analysis["teaching_point_assessments"]]
        lane_comparison = {
            "DEFAULT": {
                "L": analysis["scores"]["L"],
                "S": analysis["scores"]["S"],
                "T_center": analysis["scores"]["T_center"],
                "effective_coverage_rate": analysis["coverage"]["effective_coverage_rate"],
                "coverage_rate": analysis["coverage"]["coverage_rate"],
                "depths": point_depths,
            }
        }
    execution = analysis["execution"]
    return {
        "schema_version": "twinclip-stability-run-0.3",
        "compiler_version": COMPILER_VERSION,
        "run": {
            "run_id": execution["run_id"],
            "experiment_id": run.get("experiment_id"),
            "video_id": run.get("video_id"),
            "round": run.get("round"),
            "replicate_index": run.get("replicate_index"),
            "execution_context_id": execution["execution_context_id"],
            "fixed_evidence_hash": run.get("fixed_evidence_hash"),
        },
        "reference_bundle_hash": report["reference_bundle"]["content_hash"],
        "scoring_config_hash": analysis["provenance"].get("scoring_config_hash"),
        "anchor_placement_hash": analysis["provenance"].get("anchor_placement_hash"),
        "primary_lane": primary_lane,
        "lane_selection": copy.deepcopy(analysis.get("lane_selection", {})),
        "lane_comparison": lane_comparison,
        "scores": copy.deepcopy(analysis["scores"]),
        "confidence": copy.deepcopy(analysis["confidence"]),
        "logic_assessment": copy.deepcopy(analysis["logic_assessment"]),
        "teaching_points": copy.deepcopy(analysis["teaching_point_assessments"]),
        "storyboard_nodes": copy.deepcopy(analysis["storyboard_node_assessments"]),
        "relationships": copy.deepcopy(analysis["relationship_assessments"]),
        "evidence_records": copy.deepcopy(analysis["evidence_records"]),
        "candidate_matches": copy.deepcopy(analysis["candidate_matches"]),
        "manual_pending_count": sum(item.get("status") == "manual_pending" for item in analysis["candidate_matches"]),
    }
