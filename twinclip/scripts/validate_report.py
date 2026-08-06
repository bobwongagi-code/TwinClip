#!/usr/bin/env python3
"""Validate TwinClip reports and recompute every deterministic metric.

The validator is deliberately strict. A report is a final deliverable only when
its evidence, shared L/S observations, calibration metadata, and review state
are all internally consistent. Use ``--allow-draft`` for intermediate reports
that still contain pending guided candidates or manual review work.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import stat
import sys
from typing import Any


SCHEMA_VERSION = "1.1"
TOLERANCE = 0.02
MIN_L_WEIGHT = 0.50
MAX_REPORT_BYTES = 20 * 1024 * 1024
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
VALID_CLARITY = {"clear", "ambiguous", "unavailable"}
VALID_FAILURES = {None, "L", "S", "A"}
VALID_ADAPTATION_REQUIRED = {"yes", "no", "unclear"}
VALID_ADAPTATION_RESULTS = {"not_needed", "successful", "partial", "failed", "pending"}
VALID_REVIEW_STATUS = {"pending", "completed"}


def is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def close(actual: Any, expected: float, tolerance: float = TOLERANCE) -> bool:
    return is_number(actual) and math.isclose(
        float(actual), expected, abs_tol=tolerance
    )


def non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def require_mapping(
    parent: dict[str, Any], key: str, path: str, errors: list[str]
) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        errors.append(f"{path}.{key} must be an object")
        return {}
    return value


def require_list(
    parent: dict[str, Any], key: str, path: str, errors: list[str]
) -> list[Any]:
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
    return confirmation_ok and has_observation and non_empty_string(record.get("observed_function")) and record.get("observed_function") != "unknown"


def score_band(center: float) -> str:
    rounded = min(100, max(0, int(round(center))))
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


def validate_report(
    data: dict[str, Any], *, allow_draft: bool = False
) -> tuple[list[str], list[str], dict[str, float]]:
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, float] = {}

    if not isinstance(data, dict):
        return ["report root must be an object"], warnings, metrics
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be '{SCHEMA_VERSION}'")

    reference = require_mapping(data, "reference_bundle", "$", errors)
    config = require_mapping(data, "scoring_config", "$", errors)
    analysis = require_mapping(data, "analysis", "$", errors)

    for key in ("id", "version"):
        if not non_empty_string(reference.get(key)):
            errors.append(f"reference_bundle.{key} must be a non-empty string")
    source_inputs = require_mapping(reference, "source_inputs", "reference_bundle", errors)
    for key in ("breakdown_video", "storyboard_pdf"):
        validate_regular_file(source_inputs.get(key), f"reference_bundle.source_inputs.{key}", errors)

    if reference.get("skeleton_mode") not in {"storyboard", "merged"}:
        errors.append("reference_bundle.skeleton_mode must be storyboard or merged")
    if reference.get("status") != "locked":
        errors.append("a scored report requires reference_bundle.status=locked")
    if reference.get("score_ready") is not True:
        errors.append("a scored report requires reference_bundle.score_ready=true")

    nodes = require_list(reference, "storyboard_nodes", "reference_bundle", errors)
    points = require_list(reference, "teaching_points", "reference_bundle", errors)
    relationships = require_list(reference, "relationships", "reference_bundle", errors)
    if not nodes:
        errors.append("reference_bundle.storyboard_nodes must not be empty")
    if not points:
        errors.append("reference_bundle.teaching_points must not be empty")
    node_ids, node_by_id = collect_ids(nodes, "reference_bundle.storyboard_nodes", errors)
    point_ids, point_by_id = collect_ids(points, "reference_bundle.teaching_points", errors)
    node_id_set = set(node_ids)
    point_id_set = set(point_ids)

    any_required_elements = False
    node_point_ids: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for node_id, node in node_by_id.items():
        for key in ("label", "function"):
            if not non_empty_string(node.get(key)):
                errors.append(f"Storyboard node {node_id}.{key} must be a non-empty string")
        required = valid_string_list(node.get("required_elements"), f"Storyboard node {node_id}.required_elements", errors)
        if required:
            any_required_elements = True
        validate_range(node.get("source_range"), f"Storyboard node {node_id}.source_range", errors)

    for point_id, point in point_by_id.items():
        if point.get("source_type") not in {"breakdown_explicit", "user_provided"}:
            errors.append(f"teaching point {point_id} source_type must be breakdown_explicit or user_provided")
        if not non_empty_string(point.get("source_locator")):
            errors.append(f"teaching point {point_id} source_locator must be a non-empty string")
        for key in (
            "stage",
            "name",
            "content_function",
            "core_meaning",
            "persuasion_element",
            "evidence_method",
            "logical_role",
        ):
            if not non_empty_string(point.get(key)):
                errors.append(f"teaching point {point_id}.{key} must be a non-empty string")
        valid_string_list(point.get("allowed_substitutions"), f"teaching point {point_id}.allowed_substitutions", errors)
        valid_string_list(point.get("minimum_evidence"), f"teaching point {point_id}.minimum_evidence", errors)
        valid_string_list(point.get("false_positive_guards"), f"teaching point {point_id}.false_positive_guards", errors)
        links = validate_id_list(point.get("storyboard_node_ids"), f"teaching point {point_id}.storyboard_node_ids", node_id_set, errors)
        for node_id in links:
            node_point_ids.setdefault(node_id, set()).add(point_id)
        ranges = point.get("source_ranges")
        if not isinstance(ranges, list):
            errors.append(f"teaching point {point_id}.source_ranges must be an array")
        else:
            for index, source_range in enumerate(ranges):
                validate_range(source_range, f"teaching point {point_id}.source_ranges[{index}]", errors)

    relationship_ids: list[str] = []
    relationship_by_id: dict[str, dict[str, Any]] = {}
    for index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            errors.append(f"reference_bundle.relationships[{index}] must be an object")
            continue
        relationship_id = relationship.get("id")
        if not non_empty_string(relationship_id):
            errors.append(f"reference_bundle.relationships[{index}].id must be a non-empty string")
            continue
        if relationship_id in relationship_by_id:
            errors.append(f"duplicate relationship id: {relationship_id}")
            continue
        relationship_ids.append(relationship_id)
        relationship_by_id[relationship_id] = relationship
        if not non_empty_string(relationship.get("type")) or not non_empty_string(relationship.get("description")):
            errors.append(f"relationship {relationship_id} requires type and description")
        validate_id_list(relationship.get("from_node_ids"), f"relationship {relationship_id}.from_node_ids", node_id_set, errors, allow_empty=False)
        validate_id_list(relationship.get("to_node_ids"), f"relationship {relationship_id}.to_node_ids", node_id_set, errors, allow_empty=False)
        validate_id_list(relationship.get("teaching_point_ids"), f"relationship {relationship_id}.teaching_point_ids", point_id_set, errors)
    relationship_id_set = set(relationship_ids)

    l_weight = config.get("l_weight")
    s_weight = config.get("s_weight")
    t_weights_valid = validate_weight_group([l_weight, s_weight], "T", errors)
    if is_number(l_weight) and float(l_weight) < MIN_L_WEIGHT:
        errors.append(f"scoring_config.l_weight must be at least {MIN_L_WEIGHT:.2f}")
    s_weights = require_mapping(config, "s_weights", "scoring_config", errors)
    s_weight_values = [s_weights.get(key) for key in ("logic", "function", "elements", "support")]
    s_weights_valid = validate_weight_group(s_weight_values, "S", errors)
    if any_required_elements and is_number(s_weights.get("elements")) and float(s_weights.get("elements")) <= 0:
        errors.append("S elements weight must be positive when any node has required elements")
    if not any_required_elements and is_number(s_weights.get("elements")) and not math.isclose(float(s_weights.get("elements")), 0.0, abs_tol=1e-6):
        errors.append("S elements weight must be 0 when no node has required elements")
    if not non_empty_string(config.get("weights_version")):
        errors.append("scoring_config.weights_version must be a non-empty string")
    calibration_registry_path: str | None = None
    if config.get("calibration_registry") is not None:
        calibration_registry_path = validate_regular_file(
            config.get("calibration_registry"),
            "scoring_config.calibration_registry",
            errors,
        )

    creator_videos_raw = analysis.get("creator_videos")
    if not isinstance(creator_videos_raw, list) or not creator_videos_raw:
        errors.append("analysis.creator_videos must contain at least one video path")
        creator_videos_raw = []
    creator_videos: list[str] = []
    for index, video in enumerate(creator_videos_raw):
        path = validate_regular_file(video, f"analysis.creator_videos[{index}]", errors)
        if path:
            creator_videos.append(path)
    if len(creator_videos) != len(set(creator_videos)):
        errors.append("analysis.creator_videos must not contain duplicates")
    if "creator_video" in analysis:
        errors.append("analysis.creator_video is obsolete; use analysis.creator_videos")
    creator_video_set = set(creator_videos)

    media_durations = require_mapping(analysis, "media_durations", "analysis", errors)
    for video in creator_videos:
        duration = media_durations.get(video)
        if not is_number(duration) or float(duration) <= 0:
            errors.append(f"analysis.media_durations must contain a positive duration for {video}")

    evidence_records = require_list(analysis, "evidence_records", "analysis", errors)
    evidence: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(evidence_records):
        if not isinstance(record, dict):
            errors.append(f"analysis.evidence_records[{index}] must be an object")
            continue
        evidence_id = record.get("id")
        if not non_empty_string(evidence_id):
            errors.append(f"analysis.evidence_records[{index}].id must be a non-empty string")
            continue
        if evidence_id in evidence:
            errors.append(f"duplicate evidence id: {evidence_id}")
            continue
        evidence[evidence_id] = record
        creator_video = validate_regular_file(record.get("creator_video"), f"evidence {evidence_id}.creator_video", errors)
        if creator_video and creator_video not in creator_video_set:
            errors.append(f"evidence {evidence_id} references a video outside analysis.creator_videos")
        for key in ("visual", "onscreen_text", "transcript", "observed_function", "coverage_scope"):
            if not non_empty_string(record.get(key)):
                errors.append(f"evidence {evidence_id}.{key} must be a non-empty string")
        if not non_empty_string(record.get("observed_function")) or record.get("observed_function") == "unknown":
            errors.append(f"evidence {evidence_id}.observed_function must be independently recognizable")
        validate_range([record.get("start_seconds"), record.get("end_seconds")], f"evidence {evidence_id}.time_range", errors)
        if creator_video and creator_video in media_durations and is_number(record.get("end_seconds")):
            if float(record["end_seconds"]) > float(media_durations[creator_video]) + TOLERANCE:
                errors.append(f"evidence {evidence_id}.end_seconds exceeds the creator video duration")
        if record.get("observation_mode") not in {"blind", "guided"}:
            errors.append(f"evidence {evidence_id} has invalid observation_mode")
        if record.get("human_confirmation") not in {"not_required", "pending", "confirmed", "rejected"}:
            errors.append(f"evidence {evidence_id} has invalid human_confirmation")
        if not isinstance(record.get("scope_complete"), bool):
            errors.append(f"evidence {evidence_id}.scope_complete must be boolean")
        candidate_id = record.get("candidate_id")
        if record.get("observation_mode") == "guided" and not non_empty_string(candidate_id):
            errors.append(f"guided evidence {evidence_id} must name its candidate_id")
        if record.get("observation_mode") == "blind" and candidate_id is not None:
            errors.append(f"blind evidence {evidence_id} must not name a candidate_id")
    evidence_id_set = set(evidence)

    candidate_matches = require_list(analysis, "candidate_matches", "analysis", errors)
    candidate_by_id: dict[str, dict[str, Any]] = {}
    candidate_evidence_owner: dict[str, str] = {}
    confirmed_candidate_point: dict[str, str] = {}
    pending_candidates = 0
    candidate_fingerprints: set[tuple[str, tuple[str, ...]]] = set()
    for index, candidate in enumerate(candidate_matches):
        if not isinstance(candidate, dict):
            errors.append(f"analysis.candidate_matches[{index}] must be an object")
            continue
        candidate_id = candidate.get("candidate_id")
        if not non_empty_string(candidate_id):
            errors.append(f"candidate_matches[{index}].candidate_id must be a non-empty string")
            continue
        if candidate_id in candidate_by_id:
            errors.append(f"duplicate candidate_id: {candidate_id}")
            continue
        candidate_by_id[candidate_id] = candidate
        status = candidate.get("status")
        if status not in {"manual_pending", "confirmed", "rejected"}:
            errors.append(f"candidate {candidate_id} has invalid status")
        if status == "manual_pending":
            pending_candidates += 1
        point_id = candidate.get("teaching_point_id")
        if not isinstance(point_id, str) or point_id not in point_id_set:
            errors.append(f"candidate {candidate_id} links unknown teaching point: {point_id}")
            point_id = None
        candidate_evidence_ids = validate_id_list(candidate.get("evidence_ids"), f"candidate {candidate_id}.evidence_ids", evidence_id_set, errors, allow_empty=False)
        fingerprint = (point_id or "", tuple(sorted(candidate_evidence_ids)))
        if fingerprint in candidate_fingerprints:
            errors.append(f"duplicate candidate match fingerprint: {candidate_id}")
        candidate_fingerprints.add(fingerprint)
        if not non_empty_string(candidate.get("reason")):
            errors.append(f"candidate {candidate_id}.reason must be a non-empty string")
        expected_confirmation = {"manual_pending": "pending", "confirmed": "confirmed", "rejected": "rejected"}.get(status)
        for evidence_id in candidate_evidence_ids:
            record = evidence.get(evidence_id)
            if record is None:
                continue
            if record.get("observation_mode") != "guided":
                errors.append(f"candidate evidence {evidence_id} must use observation_mode=guided")
            if record.get("candidate_id") != candidate_id:
                errors.append(f"candidate evidence {evidence_id} is not bound to {candidate_id}")
            if expected_confirmation and record.get("human_confirmation") != expected_confirmation:
                errors.append(f"candidate evidence {evidence_id} confirmation must be {expected_confirmation}")
            if evidence_id in candidate_evidence_owner and candidate_evidence_owner[evidence_id] != candidate_id:
                errors.append(f"guided evidence {evidence_id} belongs to multiple candidates")
            candidate_evidence_owner[evidence_id] = candidate_id
            if status == "confirmed" and point_id:
                confirmed_candidate_point[evidence_id] = point_id

    decisions: list[dict[str, Any]] = []
    failure_evidence_dimensions: dict[str, set[str]] = {}
    failure_ids: set[str] = set()

    def validate_decision(
        decision: dict[str, Any],
        label: str,
        positive: bool,
        absent: bool,
        allowed_point_ids: set[str] | None = None,
    ) -> list[str]:
        if not non_empty_string(decision.get("reason")):
            errors.append(f"{label}.reason must be a non-empty string")
        evidence_ids = validate_id_list(decision.get("evidence_ids"), f"{label}.evidence_ids", evidence_id_set, errors)
        eligible_ids: list[str] = []
        for evidence_id in evidence_ids:
            record = evidence.get(evidence_id)
            if record is None:
                continue
            if not eligible_evidence(record):
                errors.append(f"{label} uses evidence that is not scoring-eligible: {evidence_id}")
                continue
            if record.get("observation_mode") == "guided":
                point_id = confirmed_candidate_point.get(evidence_id)
                if allowed_point_ids is not None and point_id not in allowed_point_ids:
                    errors.append(f"{label} uses guided evidence {evidence_id} from an unrelated teaching point")
            eligible_ids.append(evidence_id)
        if positive and not eligible_ids:
            errors.append(f"{label} has a positive score without eligible evidence")
        if decision.get("evidence_clarity") not in VALID_CLARITY:
            errors.append(f"{label} has invalid evidence_clarity")
        clarity = decision.get("evidence_clarity")
        if clarity == "clear" and not eligible_ids:
            errors.append(f"{label} claims clear evidence without eligible evidence")
        if clarity in {"ambiguous", "unavailable"} and decision.get("manual_review") is not True:
            errors.append(f"{label} with {clarity} evidence must require manual_review")
        if absent and clarity == "clear":
            if decision.get("absence_verified") is not True:
                errors.append(f"{label} claims clear absence without absence_verified=true")
            if not any(evidence.get(evidence_id, {}).get("scope_complete") is True for evidence_id in eligible_ids):
                errors.append(f"{label} requires a complete inspected scope to prove absence")
        if decision.get("absence_verified") is True and not absent:
            errors.append(f"{label}.absence_verified is only valid for a zero-score absence")
        if not isinstance(decision.get("manual_review"), bool):
            errors.append(f"{label}.manual_review must be boolean")
        failure_dimension = decision.get("primary_failure_dimension")
        if failure_dimension not in VALID_FAILURES:
            errors.append(f"{label} has invalid primary_failure_dimension")
        failure_id = decision.get("failure_id")
        if failure_dimension is None:
            if failure_id is not None:
                errors.append(f"{label}.failure_id must be null when there is no primary failure")
        elif not non_empty_string(failure_id):
            errors.append(f"{label}.failure_id is required when a primary failure is recorded")
        else:
            if failure_id in failure_ids:
                errors.append(f"failure_id is duplicated: {failure_id}")
            failure_ids.add(failure_id)
            for evidence_id in evidence_ids:
                failure_evidence_dimensions.setdefault(evidence_id, set()).add(failure_dimension)
        decisions.append(decision)
        return eligible_ids

    teaching_assessments = require_list(analysis, "teaching_point_assessments", "analysis", errors)
    teaching_by_id: dict[str, dict[str, Any]] = {}
    depths: list[int] = []
    for index, assessment in enumerate(teaching_assessments):
        if not isinstance(assessment, dict):
            errors.append(f"teaching_point_assessments[{index}] must be an object")
            continue
        point_id = assessment.get("teaching_point_id")
        if not isinstance(point_id, str) or point_id not in point_id_set:
            errors.append(f"teaching-point assessment has unknown teaching_point_id: {point_id}")
            continue
        if point_id in teaching_by_id:
            errors.append(f"duplicate teaching-point assessment: {point_id}")
            continue
        teaching_by_id[point_id] = assessment
        for key in ("done_well", "missing_or_misused"):
            if not non_empty_string(assessment.get(key)):
                errors.append(f"teaching point {point_id}.{key} must be a non-empty string")
        depth = assessment.get("depth")
        if not isinstance(depth, int) or isinstance(depth, bool) or depth not in range(4):
            errors.append(f"teaching point {point_id} depth must be an integer 0-3")
            continue
        depths.append(depth)
        validate_decision(assessment, f"teaching point {point_id}", depth > 0, depth == 0, {point_id})
        required = assessment.get("adaptation_required")
        result = assessment.get("adaptation_result")
        if required not in VALID_ADAPTATION_REQUIRED:
            errors.append(f"teaching point {point_id} has invalid adaptation_required")
        if result not in VALID_ADAPTATION_RESULTS:
            errors.append(f"teaching point {point_id} has invalid adaptation_result")
        if required == "no" and result != "not_needed":
            errors.append(f"teaching point {point_id}: adaptation_required=no requires not_needed")
        if required == "yes" and result == "not_needed":
            errors.append(f"teaching point {point_id}: adaptation_required=yes cannot be not_needed")
        if required == "unclear" and result != "pending":
            errors.append(f"teaching point {point_id}: adaptation_required=unclear requires pending")

    if set(teaching_by_id) != point_id_set:
        errors.append("teaching-point assessments must cover every teaching point exactly once")

    node_assessments = require_list(analysis, "storyboard_node_assessments", "analysis", errors)
    node_assessment_by_id: dict[str, dict[str, Any]] = {}
    function_scores: list[int] = []
    element_scores: list[int] = []
    support_scores: list[int] = []
    for index, assessment in enumerate(node_assessments):
        if not isinstance(assessment, dict):
            errors.append(f"storyboard_node_assessments[{index}] must be an object")
            continue
        node_id = assessment.get("storyboard_node_id")
        if not isinstance(node_id, str) or node_id not in node_id_set:
            errors.append(f"Storyboard-node assessment has unknown storyboard_node_id: {node_id}")
            continue
        if node_id in node_assessment_by_id:
            errors.append(f"duplicate Storyboard-node assessment: {node_id}")
            continue
        node_assessment_by_id[node_id] = assessment
        required_elements = node_by_id[node_id].get("required_elements")
        required_element_count = len(required_elements) if isinstance(required_elements, list) else 0
        element_value = assessment.get("element_score")
        if required_element_count and element_value is None:
            errors.append(f"Storyboard node {node_id} cannot hide required elements with element_score=null")
        if not required_element_count and element_value is not None:
            errors.append(f"Storyboard node {node_id} must use element_score=null without required elements")
        values: dict[str, int | None] = {}
        for key in ("function_score", "element_score", "support_score"):
            value = assessment.get(key)
            if key == "element_score" and value is None:
                values[key] = None
            elif not isinstance(value, int) or isinstance(value, bool) or value not in range(4):
                errors.append(f"Storyboard node {node_id} {key} must be 0-3 or the allowed null")
                values[key] = 0
            else:
                values[key] = value
        function_scores.append(int(values["function_score"] or 0))
        support_scores.append(int(values["support_score"] or 0))
        if values["element_score"] is not None:
            element_scores.append(int(values["element_score"] or 0))
        positive = any(value is not None and value > 0 for value in values.values())
        validate_decision(
            assessment,
            f"Storyboard node {node_id}",
            positive,
            not positive,
            set(node_point_ids.get(node_id, set())),
        )

    if set(node_assessment_by_id) != node_id_set:
        errors.append("Storyboard-node assessments must cover every node exactly once")
    if any_required_elements and len(element_scores) != sum(bool(node_by_id[node_id].get("required_elements")) for node_id in node_ids):
        errors.append("every node with required elements must have a non-null element_score")
    if s_weights_valid and not any_required_elements and not math.isclose(float(s_weights.get("elements", 0)), 0.0, abs_tol=1e-6):
        errors.append("S cannot assign weight to a missing element dimension")

    logic = require_mapping(analysis, "logic_assessment", "analysis", errors)
    logic_score = logic.get("score")
    if not isinstance(logic_score, int) or isinstance(logic_score, bool) or logic_score not in range(4):
        errors.append("logic_assessment.score must be an integer 0-3")
        logic_score = 0
    logic_relationship_ids = validate_id_list(logic.get("relationship_ids", []), "logic_assessment.relationship_ids", relationship_id_set, errors)
    validate_decision(logic, "logic assessment", logic_score > 0, logic_score == 0, point_id_set)

    relationship_assessments = require_list(analysis, "relationship_assessments", "analysis", errors)
    relationship_assessment_by_id: dict[str, dict[str, Any]] = {}
    relationship_scores: list[int] = []
    for index, assessment in enumerate(relationship_assessments):
        if not isinstance(assessment, dict):
            errors.append(f"relationship_assessments[{index}] must be an object")
            continue
        relationship_id = assessment.get("relationship_id")
        if not isinstance(relationship_id, str) or relationship_id not in relationship_id_set:
            errors.append(f"relationship assessment has unknown relationship_id: {relationship_id}")
            continue
        if relationship_id in relationship_assessment_by_id:
            errors.append(f"duplicate relationship assessment: {relationship_id}")
            continue
        relationship_assessment_by_id[relationship_id] = assessment
        score = assessment.get("score")
        if not isinstance(score, int) or isinstance(score, bool) or score not in range(4):
            errors.append(f"relationship {relationship_id} score must be an integer 0-3")
            score = 0
        relationship_scores.append(score)
        allowed_points = set(relationship_by_id[relationship_id].get("teaching_point_ids", []))
        validate_decision(assessment, f"relationship {relationship_id}", score > 0, score == 0, allowed_points)
    if set(relationship_assessment_by_id) != relationship_id_set:
        errors.append("relationship assessments must cover every reference relationship exactly once")
    if relationship_scores:
        if logic_relationship_ids != relationship_ids:
            errors.append("logic_assessment.relationship_ids must cover every relationship")
        relationship_expected = int(round(sum(relationship_scores) / len(relationship_scores)))
        if logic_score != relationship_expected:
            errors.append(f"logic_assessment.score must equal the rounded relationship mean: {relationship_expected}")
    elif logic_relationship_ids:
        errors.append("logic_assessment.relationship_ids must be empty when no relationships exist")

    for evidence_id, dimensions in failure_evidence_dimensions.items():
        if len(dimensions) > 1:
            errors.append(f"evidence {evidence_id} is assigned failures in multiple dimensions: {sorted(dimensions)}")

    for point_id, point in point_by_id.items():
        assessment = teaching_by_id.get(point_id)
        if not assessment or assessment.get("depth", 0) <= 0:
            continue
        linked_nodes = [node_id for node_id in point.get("storyboard_node_ids", []) if node_id in node_assessment_by_id]
        if linked_nodes and all(
            all((node_assessment_by_id[node_id].get(key) or 0) == 0 for key in ("function_score", "element_score", "support_score"))
            for node_id in linked_nodes
        ):
            errors.append(f"teaching point {point_id} claims adoption while every linked Storyboard node is absent")

    scores = require_mapping(analysis, "scores", "analysis", errors)
    coverage = require_mapping(analysis, "coverage", "analysis", errors)
    borrowing = require_mapping(analysis, "borrowing_summary", "analysis", errors)
    score_values_available = len(depths) == len(point_ids) and len(function_scores) == len(node_ids) and len(support_scores) == len(node_ids)
    if score_values_available:
        l_expected = 100 * sum(depths) / (3 * len(depths))
        metrics["L"] = l_expected
        if not close(scores.get("L"), l_expected):
            errors.append(f"scores.L must equal {l_expected:.2f}")
        counts = {"missing": depths.count(0), "surface": depths.count(1), "effective": depths.count(2), "innovative": depths.count(3)}
        for key, expected in counts.items():
            if borrowing.get(key) != expected:
                errors.append(f"borrowing_summary.{key} must equal {expected}")
        adopted = sum(depth >= 1 for depth in depths)
        coverage_expected = {
            "coverage_rate": adopted / len(depths),
            "effective_coverage_rate": sum(depth >= 2 for depth in depths) / len(depths),
            "innovation_rate": depths.count(3) / len(depths),
            "surface_error_rate": depths.count(1) / adopted if adopted else 0.0,
        }
        for key, expected in coverage_expected.items():
            if not close(coverage.get(key), expected):
                errors.append(f"coverage.{key} must equal {expected:.4f}")

        elements_expected = sum(element_scores) / len(element_scores) / 3 * 100 if element_scores else 0.0
        dimension_scores = {
            "logic": float(logic_score) / 3 * 100,
            "function": sum(function_scores) / len(function_scores) / 3 * 100,
            "elements": elements_expected,
            "support": sum(support_scores) / len(support_scores) / 3 * 100,
        }
        if s_weights_valid:
            s_expected = sum(dimension_scores[key] * float(s_weights[key]) for key in ("logic", "function", "elements", "support"))
            metrics["S"] = s_expected
            if not close(scores.get("S"), s_expected):
                errors.append(f"scores.S must equal {s_expected:.2f}")
            if t_weights_valid and is_number(l_weight) and is_number(s_weight):
                t_expected = l_expected * float(l_weight) + s_expected * float(s_weight)
                metrics["T"] = t_expected
                if not close(scores.get("T_center"), t_expected):
                    errors.append(f"scores.T_center must equal {t_expected:.2f}")

    t_center = scores.get("T_center")
    if not is_number(t_center) or not 0 <= float(t_center) <= 100:
        errors.append("scores.T_center must be a finite number in 0-100")
    t_range = scores.get("T_range")
    if not isinstance(t_range, list) or len(t_range) != 2 or not all(is_number(value) and 0 <= float(value) <= 100 for value in t_range) or (len(t_range) == 2 and float(t_range[0]) > float(t_range[1])):
        errors.append("scores.T_range must be an ordered [low, high] pair in 0-100")
    elif is_number(t_center) and not (float(t_range[0]) <= float(t_center) <= float(t_range[1])):
        errors.append("scores.T_range must contain scores.T_center")
    if scores.get("formula_band") not in {label for _, _, label in BANDS}:
        errors.append("scores.formula_band is invalid")
    elif is_number(t_center) and scores.get("formula_band") != score_band(float(t_center)):
        errors.append("scores.formula_band must follow scores.T_center")

    valid_band_names = {label for _, _, label in BANDS}
    if scores.get("band") not in valid_band_names:
        errors.append("scores.band is invalid")
    if not isinstance(scores.get("provisional"), bool):
        errors.append("scores.provisional must be boolean")

    anchor = require_mapping(analysis, "anchor_placement", "analysis", errors)
    has_anchors = anchor.get("has_anchors")
    if not isinstance(has_anchors, bool):
        errors.append("anchor_placement.has_anchors must be boolean")
        has_anchors = False
    formula_conflict = anchor.get("formula_conflict")
    if not isinstance(formula_conflict, bool):
        errors.append("anchor_placement.formula_conflict must be boolean")
        formula_conflict = False
    boundary_clarity = anchor.get("boundary_clarity")
    if boundary_clarity not in {0, 0.5, 1}:
        errors.append("anchor_placement.boundary_clarity must be 0, 0.5, or 1")
        boundary_clarity = 0
    if not has_anchors:
        if calibration_registry_path is not None:
            errors.append("scoring_config.calibration_registry must be null without anchors")
        if anchor.get("anchor_set_id") is not None or anchor.get("lower_anchor") is not None or anchor.get("upper_anchor") is not None:
            errors.append("anchor details must be null when anchors are absent")
        if boundary_clarity != 0.5:
            errors.append("anchor_placement.boundary_clarity must be 0.5 without anchors")
        if formula_conflict:
            errors.append("formula_conflict must be false without anchors")
        if scores.get("provisional") is not True:
            errors.append("scores.provisional must be true when anchors are absent")
        if not close(l_weight, DEFAULT_L_WEIGHT) or not close(s_weight, DEFAULT_S_WEIGHT):
            errors.append("reports without anchors must use the default T weights 0.70/0.30")
        default_s_weights = dict(DEFAULT_S_WEIGHTS)
        if not any_required_elements:
            remainder = 1.0 - DEFAULT_S_WEIGHTS["elements"]
            default_s_weights = {
                "logic": DEFAULT_S_WEIGHTS["logic"] / remainder,
                "function": DEFAULT_S_WEIGHTS["function"] / remainder,
                "elements": 0.0,
                "support": DEFAULT_S_WEIGHTS["support"] / remainder,
            }
        for key, expected in default_s_weights.items():
            if not close(s_weights.get(key), expected, tolerance=1e-6):
                errors.append(f"reports without anchors must use the default S weight for {key}: {expected:.6f}")
        if is_number(t_center) and scores.get("band") != score_band(float(t_center)):
            errors.append(f"without anchors, scores.band must follow the formula band: {score_band(float(t_center))}")
    else:
        if calibration_registry_path is None:
            errors.append("scoring_config.calibration_registry is required when anchors exist")
        if not non_empty_string(anchor.get("anchor_set_id")):
            errors.append("anchor_placement.anchor_set_id is required when anchors exist")
        if anchor.get("reference_bundle_id") != reference.get("id") or anchor.get("reference_bundle_version") != reference.get("version"):
            errors.append("anchors must bind to the exact reference bundle id and version")
        if anchor.get("weights_version") != config.get("weights_version"):
            errors.append("anchor_placement.weights_version must equal scoring_config.weights_version")
        lower = anchor.get("lower_anchor")
        upper = anchor.get("upper_anchor")
        for name, item in (("lower_anchor", lower), ("upper_anchor", upper)):
            if not isinstance(item, dict) or not non_empty_string(item.get("id")) or item.get("band") not in valid_band_names or not is_number(item.get("T_center")):
                errors.append(f"anchor_placement.{name} must include id, band, and finite T_center")
            elif item.get("reference_bundle_id") != reference.get("id") or item.get("reference_bundle_version") != reference.get("version"):
                errors.append(f"anchor_placement.{name} must bind to the exact reference bundle")
        if isinstance(lower, dict) and isinstance(upper, dict) and lower.get("band") in valid_band_names and upper.get("band") in valid_band_names and band_index(lower["band"]) >= band_index(upper["band"]):
            errors.append("lower_anchor.band must be below upper_anchor.band")
        anchor_band = anchor.get("anchor_band")
        if anchor_band not in valid_band_names:
            errors.append("anchor_placement.anchor_band is invalid")
        elif scores.get("band") != anchor_band:
            errors.append("scores.band must equal the human-resolved anchor band")
        if is_number(t_center) and scores.get("formula_band") in valid_band_names:
            expected_conflict = scores.get("band") != scores.get("formula_band")
            if formula_conflict != expected_conflict:
                errors.append("anchor_placement.formula_conflict must equal the formula-versus-anchor band mismatch")
        if scores.get("provisional") is not False:
            errors.append("scores.provisional must be false when anchors exist")
        registry = read_json_file(calibration_registry_path, "scoring_config.calibration_registry", errors) if calibration_registry_path else None
        if registry is not None:
            if registry.get("schema_version") != "1.0" or registry.get("status") != "locked":
                errors.append("calibration registry must have schema_version=1.0 and status=locked")
            for key, expected in {
                "anchor_set_id": anchor.get("anchor_set_id"),
                "reference_bundle_id": reference.get("id"),
                "reference_bundle_version": reference.get("version"),
                "weights_version": config.get("weights_version"),
                "anchor_band": anchor.get("anchor_band"),
                "boundary_clarity": boundary_clarity,
            }.items():
                if registry.get(key) != expected:
                    errors.append(f"calibration registry {key} does not match the report")
            registry_anchors: dict[str, dict[str, Any]] = {}
            registry_anchor_list = registry.get("anchors")
            if not isinstance(registry_anchor_list, list):
                errors.append("calibration registry anchors must be an array")
            else:
                for item in registry_anchor_list:
                    if isinstance(item, dict) and non_empty_string(item.get("id")):
                        registry_anchors[item["id"]] = item
            for name, report_anchor in (("lower_anchor", lower), ("upper_anchor", upper)):
                if not isinstance(report_anchor, dict) or not non_empty_string(report_anchor.get("id")):
                    continue
                registered = registry_anchors.get(report_anchor["id"])
                if registered is None:
                    errors.append(f"{name} is missing from the calibration registry")
                    continue
                for key in ("band", "T_center", "reference_bundle_id", "reference_bundle_version"):
                    if registered.get(key) != report_anchor.get(key):
                        errors.append(f"{name}.{key} does not match the calibration registry")

    confidence = require_mapping(analysis, "confidence", "analysis", errors)
    clear_supported = 0
    manual_review_count = 0
    for decision in decisions:
        if decision.get("manual_review") is True:
            manual_review_count += 1
        evidence_ids = decision.get("evidence_ids") if isinstance(decision.get("evidence_ids"), list) else []
        eligible_ids = [evidence_id for evidence_id in evidence_ids if isinstance(evidence_id, str) and evidence_id in evidence and eligible_evidence(evidence[evidence_id])]
        if decision.get("evidence_clarity") == "clear" and (eligible_ids or decision.get("absence_verified") is True):
            clear_supported += 1
    decision_count = len(decisions)
    e_expected = clear_supported / decision_count if decision_count else 0.0
    review_denominator = decision_count + pending_candidates
    r_expected = (manual_review_count + pending_candidates) / review_denominator if review_denominator else 0.0
    metrics["E"] = e_expected
    metrics["R"] = r_expected
    if not close(confidence.get("E"), e_expected):
        errors.append(f"confidence.E must equal {e_expected:.4f}")
    if not close(confidence.get("R"), r_expected):
        errors.append(f"confidence.R must equal {r_expected:.4f}")
    m_value = confidence.get("M")
    if m_value not in {0, 0.5, 1}:
        errors.append("confidence.M must be 0, 0.5, or 1")
        m_value = 0
    if boundary_clarity != m_value:
        errors.append("confidence.M must equal anchor_placement.boundary_clarity")
    level_expected = expected_confidence(e_expected, float(m_value), r_expected)
    if not has_anchors and level_expected == "high":
        level_expected = "medium"
    if confidence.get("level") != level_expected:
        errors.append(f"confidence.level must be {level_expected}")
    level = confidence.get("level") if confidence.get("level") in {"high", "medium", "low"} else level_expected
    if is_number(t_center) and isinstance(t_range, list) and len(t_range) == 2:
        width = {"high": 3, "medium": 6, "low": 10}[level]
        expected_range = [max(0, int(round(float(t_center) - width))), min(100, int(round(float(t_center) + width)))]
        if t_range != expected_range:
            errors.append(f"scores.T_range must equal {expected_range} for {level} confidence")

    review_status = analysis.get("review_status")
    if review_status not in VALID_REVIEW_STATUS:
        errors.append("analysis.review_status must be pending or completed")
    needs_review = level == "low" or manual_review_count > 0 or pending_candidates > 0 or formula_conflict
    if needs_review and review_status != "completed":
        if allow_draft:
            warnings.append("report remains a draft because required human review is incomplete")
        else:
            errors.append("analysis.review_status must be completed before final delivery")
    if level == "low":
        warnings.append("low-confidence report requires human review")

    adaptation = require_mapping(analysis, "adaptation_diagnostic", "analysis", errors)
    if "A" in scores or "adaptation_score" in scores or "score" in adaptation:
        errors.append("adaptation must not be emitted as a numeric score")
    adaptation_statuses = {"aligned", "conditional", "mismatch", "unknown"}
    if adaptation.get("status") not in adaptation_statuses:
        errors.append("adaptation_diagnostic.status is invalid")
    adaptation_counts = {"successful": 0, "partial": 0, "failed": 0, "pending": 0, "unclear": 0}
    required_count = 0
    for assessment in teaching_assessments:
        if not isinstance(assessment, dict):
            continue
        required = assessment.get("adaptation_required")
        result = assessment.get("adaptation_result")
        if required == "yes":
            required_count += 1
            if result in adaptation_counts:
                adaptation_counts[result] += 1
        elif required == "unclear":
            adaptation_counts["unclear"] += 1
    expected_adaptation_fields = {
        "required_count": required_count,
        "successful_count": adaptation_counts["successful"],
        "partial_count": adaptation_counts["partial"],
        "failed_count": adaptation_counts["failed"],
        "pending_count": adaptation_counts["pending"],
        "unclear_count": adaptation_counts["unclear"],
    }
    for key, expected in expected_adaptation_fields.items():
        if adaptation.get(key) != expected:
            errors.append(f"adaptation_diagnostic.{key} must equal {expected}")
    confirmed_required = adaptation_counts["successful"] + adaptation_counts["partial"] + adaptation_counts["failed"]
    hit_rate = adaptation_counts["successful"] / confirmed_required if confirmed_required else None
    if hit_rate is None:
        if adaptation.get("compensation_hit_rate") is not None:
            errors.append("compensation_hit_rate must be null without confirmed requirements")
    elif not close(adaptation.get("compensation_hit_rate"), hit_rate):
        errors.append(f"compensation_hit_rate must equal {hit_rate:.4f}")
    if adaptation_counts["unclear"] or adaptation_counts["pending"]:
        expected_status = "unknown"
    elif adaptation_counts["failed"] and not adaptation_counts["successful"]:
        expected_status = "mismatch"
    elif adaptation_counts["failed"] or adaptation_counts["partial"]:
        expected_status = "conditional"
    else:
        expected_status = "aligned"
    if adaptation.get("status") != expected_status:
        errors.append(f"adaptation_diagnostic.status must equal {expected_status} from the adaptation counts")

    next_actions = require_list(analysis, "next_actions", "analysis", errors)
    if not 1 <= len(next_actions) <= 3:
        errors.append("analysis.next_actions must contain one to three actions")
    for index, action in enumerate(next_actions):
        if not non_empty_string(action):
            errors.append(f"analysis.next_actions[{index}] must be a non-empty string")
    for key in ("why_not_higher", "why_not_lower"):
        if not non_empty_string(analysis.get(key)):
            errors.append(f"analysis.{key} must be a non-empty string")

    if pending_candidates:
        warnings.append(f"{pending_candidates} guided candidate(s) await human confirmation")
    return errors, warnings, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a TwinClip report JSON file.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--allow-draft", action="store_true", help="allow pending human review in an intermediate report")
    args = parser.parse_args()

    report_path = args.report.expanduser()
    try:
        mode = report_path.stat().st_mode
        if not stat.S_ISREG(mode):
            print(f"error: report must be a regular file: {report_path}", file=sys.stderr)
            return 2
        if report_path.stat().st_size > MAX_REPORT_BYTES:
            print(f"error: report exceeds {MAX_REPORT_BYTES} bytes", file=sys.stderr)
            return 2
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"error: report not found: {report_path}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError) as exc:
        print(f"error: cannot read report: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        errors, warnings, metrics = validate_report(data, allow_draft=args.allow_draft)
    except Exception as exc:  # Defensive boundary for untrusted JSON.
        print(f"error: validator rejected malformed report safely: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        print(f"validation failed with {len(errors)} error(s)", file=sys.stderr)
        return 1

    metric_text = ", ".join(
        f"{key}={value:.2f}" for key, value in metrics.items() if key in {"L", "S", "T", "E", "R"}
    )
    print(f"valid TwinClip report ({metric_text})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
