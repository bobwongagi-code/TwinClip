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

from contracts import (  # noqa: E402
    ANALYSIS_VERSION,
    BANDS,
    COMPILER_VERSION,
    MIN_L_WEIGHT,
    PREPARE_MANIFEST_SCHEMA_VERSION,
    SCHEMA_VERSION,
    TOLERANCE,
    VALID_ADAPTATION_REQUIRED,
    VALID_ADAPTATION_RESULTS,
    VALID_CLARITY,
    VALID_EVIDENCE_SCOPES,
    VALID_FAILURES,
    analysis_id,
    canonical_json,
    provenance_fingerprint,
    reference_bundle_hash,
    sha256_bytes,
    sha256_file,
)
from validation_support import (  # noqa: E402
    MAX_REPORT_BYTES,
    close,
    collect_ids,
    eligible_evidence,
    is_number,
    non_empty_string,
    require_list,
    require_mapping,
    score_band,
    validate_id_list,
    validate_content_identity,
    validate_range,
    validate_regular_file,
    validate_source_hash,
    validate_weight_group,
    valid_string_list,
)
from validation_scoring import (  # noqa: E402
    validate_anchor_placement,
    validate_confidence_and_adaptation,
)


def validate_multi_reference(
    value: Any,
    *,
    scores: dict[str, Any],
    l_weight: Any,
    s_weight: Any,
    errors: list[str],
) -> None:
    """Validate optional per-lane summaries without mixing them into primary scores."""
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append("multi_reference must be an object when provided")
        return
    for key in ("run_mode", "primary_reference_lane", "selection_rule", "storyboard_scope"):
        if not non_empty_string(value.get(key)):
            errors.append(f"multi_reference.{key} must be a non-empty string")
    lanes = value.get("lane_comparison")
    if not isinstance(lanes, dict) or len(lanes) < 2:
        errors.append("multi_reference.lane_comparison must contain at least two lanes")
        return

    lane_ids = list(lanes)
    lane_values: dict[str, dict[str, float]] = {}
    for lane_id, lane in lanes.items():
        if not non_empty_string(lane_id) or not isinstance(lane, dict):
            errors.append(f"multi_reference lane {lane_id} must be a non-empty lane object")
            continue
        if not non_empty_string(lane.get("label")):
            errors.append(f"multi_reference lane {lane_id}.label must be a non-empty string")
        for key in ("L", "S_storyboard", "T_center"):
            if not is_number(lane.get(key)) or not 0 <= float(lane.get(key)) <= 100:
                errors.append(f"multi_reference lane {lane_id}.{key} must be a number from 0 to 100")
        for key in ("coverage_rate", "effective_coverage_rate"):
            if not is_number(lane.get(key)) or not 0 <= float(lane.get(key)) <= 1:
                errors.append(f"multi_reference lane {lane_id}.{key} must be a ratio from 0 to 1")
        depths = lane.get("depths")
        if not isinstance(depths, list) or not depths:
            errors.append(f"multi_reference lane {lane_id}.depths must be a non-empty array")
            continue
        if any(not isinstance(depth, int) or isinstance(depth, bool) or depth not in range(4) for depth in depths):
            errors.append(f"multi_reference lane {lane_id}.depths must contain only integers 0-3")
            continue
        count = len(depths)
        expected_l = 100 * sum(depths) / (3 * count)
        expected_coverage = sum(depth >= 1 for depth in depths) / count
        expected_effective = sum(depth >= 2 for depth in depths) / count
        if not close(lane.get("L"), expected_l):
            errors.append(f"multi_reference lane {lane_id}.L must equal {expected_l:.2f}")
        if not close(lane.get("coverage_rate"), expected_coverage):
            errors.append(f"multi_reference lane {lane_id}.coverage_rate must equal {expected_coverage:.4f}")
        if not close(lane.get("effective_coverage_rate"), expected_effective):
            errors.append(
                f"multi_reference lane {lane_id}.effective_coverage_rate must equal {expected_effective:.4f}"
            )
        if is_number(lane.get("L")) and is_number(lane.get("S_storyboard")) and is_number(l_weight) and is_number(s_weight):
            expected_t = float(lane["L"]) * float(l_weight) + float(lane["S_storyboard"]) * float(s_weight)
            if not close(lane.get("T_center"), expected_t):
                errors.append(f"multi_reference lane {lane_id}.T_center must equal {expected_t:.2f}")
        if all(is_number(lane.get(key)) for key in ("L", "S_storyboard", "T_center", "effective_coverage_rate")):
            lane_values[lane_id] = {
                "effective_coverage_rate": float(lane["effective_coverage_rate"]),
                "T_center": float(lane["T_center"]),
            }

    primary = value.get("primary_reference_lane")
    if primary not in lane_values:
        errors.append("multi_reference.primary_reference_lane must name a valid lane")
        return
    if not lane_values:
        return
    best_effective = max(item["effective_coverage_rate"] for item in lane_values.values())
    effective_candidates = [
        lane_id for lane_id in lane_ids
        if lane_id in lane_values and math.isclose(
            lane_values[lane_id]["effective_coverage_rate"], best_effective, abs_tol=1e-9
        )
    ]
    best_t = max(lane_values[lane_id]["T_center"] for lane_id in effective_candidates)
    t_candidates = [
        lane_id for lane_id in effective_candidates
        if math.isclose(lane_values[lane_id]["T_center"], best_t, abs_tol=1e-9)
    ]
    expected_primary = t_candidates[0]
    if len(t_candidates) > 1:
        if value.get("selection_tie") is not True:
            errors.append("multi_reference exact ties must set selection_tie=true")
        if value.get("tie_breaker") != "declared_lane_order":
            errors.append("multi_reference exact ties must use tie_breaker=declared_lane_order")
    elif "selection_tie" in value and not isinstance(value.get("selection_tie"), bool):
        errors.append("multi_reference.selection_tie must be boolean when provided")
    if primary != expected_primary:
        errors.append(
            "multi_reference.primary_reference_lane must follow effective coverage, then T, then declared lane order"
        )
    primary_lane = lanes.get(primary)
    if isinstance(primary_lane, dict):
        for score_key, lane_key in (("L", "L"), ("S", "S_storyboard"), ("T_center", "T_center")):
            if is_number(scores.get(score_key)) and is_number(primary_lane.get(lane_key)) and not close(
                scores.get(score_key), float(primary_lane[lane_key])
            ):
                errors.append(f"analysis.scores.{score_key} must match the selected multi-reference lane")


def validate_report(
    data: dict[str, Any], *, allow_draft: bool = False, hash_cache: dict[str, str] | None = None
) -> tuple[list[str], list[str], dict[str, float]]:
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, float] = {}
    hash_cache = hash_cache if hash_cache is not None else {}

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
    reference_hash = reference.get("content_hash")
    if not isinstance(reference_hash, str) or len(reference_hash) != 64:
        errors.append("reference_bundle.content_hash must be a SHA-256 digest")
    elif reference_hash != reference_bundle_hash(reference):
        errors.append("reference_bundle.content_hash does not match the locked reference graph")
    breakdown_path = validate_regular_file(
        source_inputs.get("breakdown_video"), "reference_bundle.source_inputs.breakdown_video", errors
    )
    storyboard_path = validate_regular_file(
        source_inputs.get("storyboard_pdf"), "reference_bundle.source_inputs.storyboard_pdf", errors
    )
    source_content_hashes = require_mapping(reference, "source_content_hashes", "reference_bundle", errors)
    if breakdown_path:
        validate_content_identity(
            source_content_hashes.get("breakdown_video"),
            breakdown_path,
            "reference_bundle.source_content_hashes.breakdown_video",
            errors,
            hash_cache,
        )
    if storyboard_path:
        validate_content_identity(
            source_content_hashes.get("storyboard_pdf"),
            storyboard_path,
            "reference_bundle.source_content_hashes.storyboard_pdf",
            errors,
            hash_cache,
        )

    if reference.get("skeleton_mode") not in {"storyboard", "merged"}:
        errors.append("reference_bundle.skeleton_mode must be storyboard or merged")
    if reference.get("status") != "locked":
        errors.append("a scored report requires reference_bundle.status=locked")
    if reference.get("score_ready") is not True:
        errors.append("a scored report requires reference_bundle.score_ready=true")

    nodes = require_list(reference, "storyboard_nodes", "reference_bundle", errors)
    raw_points = reference.get("teaching_points")
    if isinstance(raw_points, dict):
        multi_reference = data.get("multi_reference")
        selected_lane = multi_reference.get("primary_reference_lane") if isinstance(multi_reference, dict) else None
        if not non_empty_string(selected_lane) or not isinstance(raw_points.get(selected_lane), list):
            errors.append(
                "reference_bundle.teaching_points lane map requires a valid "
                "multi_reference.primary_reference_lane"
            )
            points = []
        else:
            points = raw_points[selected_lane]
    else:
        points = require_list(reference, "teaching_points", "reference_bundle", errors)
    relationship_reference = reference
    multi_reference_for_graph = data.get("multi_reference")
    selected_graph_lane = (
        multi_reference_for_graph.get("primary_reference_lane")
        if isinstance(multi_reference_for_graph, dict)
        else None
    )
    if (
        non_empty_string(selected_graph_lane)
        and isinstance(reference.get("lanes"), dict)
        and isinstance(reference["lanes"].get(selected_graph_lane), dict)
        and isinstance(reference["lanes"][selected_graph_lane].get("relationships"), list)
    ):
        relationship_reference = reference["lanes"][selected_graph_lane]
    relationships = require_list(relationship_reference, "relationships", "reference_bundle", errors)
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
    has_relationships = bool(relationship_ids)
    if has_relationships and is_number(s_weights.get("logic")) and float(s_weights.get("logic")) <= 0:
        errors.append("S logic weight must be positive when reference relationships exist")
    if not has_relationships and is_number(s_weights.get("logic")) and not math.isclose(float(s_weights.get("logic")), 0.0, abs_tol=1e-6):
        errors.append("S logic weight must be 0 when no reference relationships exist")
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
    if not isinstance(creator_videos_raw, list) or len(creator_videos_raw) != 1:
        errors.append("analysis.creator_videos must contain exactly one video path per report")
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

    provenance = require_mapping(analysis, "provenance", "analysis", errors)
    if provenance.get("analysis_version") != ANALYSIS_VERSION:
        errors.append(f"analysis.provenance.analysis_version must be '{ANALYSIS_VERSION}'")
    for key in (
        "observation_method",
        "model_id",
        "prompt_version",
        "extraction_version",
        "compiler_version",
        "scoring_config_hash",
        "anchor_placement_hash",
    ):
        if not non_empty_string(provenance.get(key)):
            errors.append(f"analysis.provenance.{key} must be a non-empty string")
    if provenance.get("compiler_version") != COMPILER_VERSION:
        errors.append(f"analysis.provenance.compiler_version must be '{COMPILER_VERSION}'")
    if not isinstance(provenance.get("reference_bundle_hash"), str):
        errors.append("analysis.provenance.reference_bundle_hash must be a string")
    elif provenance.get("reference_bundle_hash") != reference.get("content_hash"):
        errors.append("analysis.provenance.reference_bundle_hash must equal reference_bundle.content_hash")
    if non_empty_string(provenance.get("scoring_config_hash")) and provenance.get("scoring_config_hash") != sha256_bytes(
        canonical_json(config).encode("utf-8")
    ):
        errors.append("analysis.provenance.scoring_config_hash does not match scoring_config")
    anchor_for_hash = analysis.get("anchor_placement")
    if isinstance(anchor_for_hash, dict) and non_empty_string(provenance.get("anchor_placement_hash")):
        if provenance.get("anchor_placement_hash") != sha256_bytes(canonical_json(anchor_for_hash).encode("utf-8")):
            errors.append("analysis.provenance.anchor_placement_hash does not match analysis.anchor_placement")
    registry_path_for_hash = config.get("calibration_registry")
    registry_hash = provenance.get("calibration_registry_sha256")
    if registry_path_for_hash is None:
        if registry_hash is not None:
            errors.append("analysis.provenance.calibration_registry_sha256 must be null without a registry")
    elif isinstance(registry_hash, str):
        try:
            if registry_hash != sha256_file(registry_path_for_hash):
                errors.append("analysis.provenance.calibration_registry_sha256 does not match the registry")
        except OSError:
            errors.append("analysis.provenance.calibration_registry_sha256 cannot read the registry")
    else:
        errors.append("analysis.provenance.calibration_registry_sha256 must be a digest when a registry is configured")
    media_preparation = require_mapping(provenance, "media_preparation", "analysis.provenance", errors)
    if media_preparation.get("manifest_schema_version") != PREPARE_MANIFEST_SCHEMA_VERSION:
        errors.append(
            f"analysis.provenance.media_preparation.manifest_schema_version must be '{PREPARE_MANIFEST_SCHEMA_VERSION}'"
        )
    for key in ("interval_seconds", "max_frames"):
        if not is_number(media_preparation.get(key)) or float(media_preparation.get(key)) <= 0:
            errors.append(f"analysis.provenance.media_preparation.{key} must be positive")
    source_hashes = require_mapping(provenance, "source_hashes", "analysis.provenance", errors)
    if breakdown_path:
        validate_source_hash(
            source_hashes.get("breakdown_video"),
            breakdown_path,
            "analysis.provenance.source_hashes.breakdown_video",
            errors,
            hash_cache,
        )
    if storyboard_path:
        validate_source_hash(
            source_hashes.get("storyboard_pdf"),
            storyboard_path,
            "analysis.provenance.source_hashes.storyboard_pdf",
            errors,
            hash_cache,
        )
    if creator_videos:
        validate_source_hash(
            source_hashes.get("creator_video"),
            creator_videos[0],
            "analysis.provenance.source_hashes.creator_video",
            errors,
            hash_cache,
        )
    expected_method_fingerprint = provenance_fingerprint(provenance)
    if provenance.get("method_fingerprint") != expected_method_fingerprint:
        errors.append("analysis.provenance.method_fingerprint does not match the analysis method contract")
    creator_hash_value = source_hashes.get("creator_video", {}).get("sha256") if isinstance(source_hashes.get("creator_video"), dict) else None
    expected_analysis_id = analysis_id(str(reference.get("content_hash")), str(creator_hash_value), expected_method_fingerprint)
    if analysis.get("analysis_id") != expected_analysis_id:
        errors.append("analysis.analysis_id does not match the reference, creator, and method identities")

    execution = require_mapping(analysis, "execution", "analysis", errors)
    for key in ("run_id", "execution_context_id"):
        if not non_empty_string(execution.get(key)):
            errors.append(f"analysis.execution.{key} must be a non-empty string")
    task_ids = execution.get("task_ids")
    if not isinstance(task_ids, list) or not task_ids or any(not non_empty_string(item) for item in task_ids):
        errors.append("analysis.execution.task_ids must be a non-empty array of task IDs")
    elif len(task_ids) != len(set(task_ids)):
        errors.append("analysis.execution.task_ids must not contain duplicates")
    if execution.get("temperature") is not None and not is_number(execution.get("temperature")):
        errors.append("analysis.execution.temperature must be finite or null")
    if execution.get("seed") is not None and (
        not isinstance(execution.get("seed"), int) or isinstance(execution.get("seed"), bool)
    ):
        errors.append("analysis.execution.seed must be an integer or null")

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
        evidence_scope = record.get("evidence_scope")
        if evidence_scope not in VALID_EVIDENCE_SCOPES:
            errors.append(f"evidence {evidence_id}.evidence_scope must be segment or full_video")
        linked_node_ids = validate_id_list(
            record.get("storyboard_node_ids"),
            f"evidence {evidence_id}.storyboard_node_ids",
            node_id_set,
            errors,
        )
        if evidence_scope == "full_video":
            if record.get("scope_complete") is not True:
                errors.append(f"full_video evidence {evidence_id} requires scope_complete=true")
            if set(linked_node_ids) != node_id_set:
                errors.append(f"full_video evidence {evidence_id} must cover every Storyboard node")
            if creator_video and creator_video in media_durations:
                duration = float(media_durations[creator_video])
                if not (
                    is_number(record.get("start_seconds"))
                    and is_number(record.get("end_seconds"))
                    and math.isclose(float(record["start_seconds"]), 0.0, abs_tol=TOLERANCE)
                    and math.isclose(float(record["end_seconds"]), duration, abs_tol=TOLERANCE)
                ):
                    errors.append(f"full_video evidence {evidence_id} must span the declared creator-video duration")
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
            if key in assessment and assessment.get(key) is not None and not non_empty_string(assessment.get(key)):
                errors.append(f"teaching point {point_id}.{key} must be a non-empty string when provided")
        depth = assessment.get("depth")
        if not isinstance(depth, int) or isinstance(depth, bool) or depth not in range(4):
            errors.append(f"teaching point {point_id} depth must be an integer 0-3")
            continue
        depths.append(depth)
        point = point_by_id[point_id]
        eligible_ids = validate_decision(assessment, f"teaching point {point_id}", depth > 0, depth == 0, {point_id})
        linked_nodes = set(point.get("storyboard_node_ids", []))
        eligible_records = [evidence[evidence_id] for evidence_id in eligible_ids]
        if depth > 0:
            if any(record.get("evidence_scope") == "full_video" for record in eligible_records):
                errors.append(f"teaching point {point_id} cannot use full_video evidence for a positive depth")
            if linked_nodes and not any(
                set(record.get("storyboard_node_ids", [])) & linked_nodes
                for record in eligible_records
            ):
                errors.append(f"teaching point {point_id} has no eligible evidence linked to its Storyboard node")
        elif assessment.get("absence_verified") is True and not any(
            record.get("evidence_scope") == "full_video" for record in eligible_records
        ):
            errors.append(f"teaching point {point_id} requires full_video evidence to verify absence")
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
        eligible_ids = validate_decision(
            assessment,
            f"Storyboard node {node_id}",
            positive,
            not positive,
            set(node_point_ids.get(node_id, set())),
        )
        eligible_records = [evidence[evidence_id] for evidence_id in eligible_ids]
        for record in eligible_records:
            record_nodes = set(record.get("storyboard_node_ids", []))
            if record.get("evidence_scope") == "segment" and node_id not in record_nodes:
                errors.append(f"Storyboard node {node_id} uses segment evidence unrelated to that node")
            if positive and record.get("evidence_scope") == "full_video":
                errors.append(f"Storyboard node {node_id} cannot use full_video evidence for a positive score")
        if positive and not any(
            record.get("evidence_scope") == "segment" and node_id in set(record.get("storyboard_node_ids", []))
            for record in eligible_records
        ):
            errors.append(f"Storyboard node {node_id} has no eligible segment evidence linked to that node")
        if not positive and assessment.get("absence_verified") is True and not any(
            record.get("evidence_scope") == "full_video" for record in eligible_records
        ):
            errors.append(f"Storyboard node {node_id} requires full_video evidence to verify absence")

    if set(node_assessment_by_id) != node_id_set:
        errors.append("Storyboard-node assessments must cover every node exactly once")
    if any_required_elements and len(element_scores) != sum(bool(node_by_id[node_id].get("required_elements")) for node_id in node_ids):
        errors.append("every node with required elements must have a non-null element_score")
    if s_weights_valid and not any_required_elements and not math.isclose(float(s_weights.get("elements", 0)), 0.0, abs_tol=1e-6):
        errors.append("S cannot assign weight to a missing element dimension")

    logic = require_mapping(analysis, "logic_assessment", "analysis", errors)
    logic_score = logic.get("score")
    if not is_number(logic_score) or not 0 <= float(logic_score) <= 3:
        errors.append("logic_assessment.score must be a finite number from 0 to 3")
        logic_score = 0
    logic_relationship_ids = validate_id_list(logic.get("relationship_ids", []), "logic_assessment.relationship_ids", relationship_id_set, errors)
    logic_eligible_ids = validate_decision(logic, "logic assessment", logic_score > 0, logic_score == 0, point_id_set)
    logic_eligible_records = [evidence[evidence_id] for evidence_id in logic_eligible_ids]
    if logic_score > 0:
        if any(record.get("evidence_scope") == "full_video" for record in logic_eligible_records):
            errors.append("logic assessment cannot use full_video evidence for a positive score")
        if not any(record.get("evidence_scope") == "segment" for record in logic_eligible_records):
            errors.append("logic assessment needs eligible segment evidence for a positive score")
    elif logic.get("absence_verified") is True and not any(
        record.get("evidence_scope") == "full_video" for record in logic_eligible_records
    ):
        errors.append("logic assessment requires full_video evidence to verify absence")

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
        eligible_ids = validate_decision(assessment, f"relationship {relationship_id}", score > 0, score == 0, allowed_points)
        from_nodes = {
            node_id
            for node_id in relationship_by_id[relationship_id].get("from_node_ids", [])
            if isinstance(node_id, str)
        }
        to_nodes = {
            node_id
            for node_id in relationship_by_id[relationship_id].get("to_node_ids", [])
            if isinstance(node_id, str)
        }
        eligible_records = [evidence[evidence_id] for evidence_id in eligible_ids]
        for record in eligible_records:
            record_nodes = set(record.get("storyboard_node_ids", []))
            if record.get("evidence_scope") == "segment" and not (record_nodes & (from_nodes | to_nodes)):
                errors.append(f"relationship {relationship_id} uses segment evidence unrelated to its endpoint nodes")
            if score > 0 and record.get("evidence_scope") == "full_video":
                errors.append(f"relationship {relationship_id} cannot use full_video evidence for a positive score")
        if score > 0:
            has_from = any(
                record.get("evidence_scope") == "segment"
                and set(record.get("storyboard_node_ids", [])) & from_nodes
                for record in eligible_records
            )
            has_to = any(
                record.get("evidence_scope") == "segment"
                and set(record.get("storyboard_node_ids", [])) & to_nodes
                for record in eligible_records
            )
            if not has_from or not has_to:
                errors.append(f"relationship {relationship_id} needs eligible segment evidence for both endpoints")
        elif assessment.get("absence_verified") is True and not any(
            record.get("evidence_scope") == "full_video" for record in eligible_records
        ):
            errors.append(f"relationship {relationship_id} requires full_video evidence to verify absence")
    if set(relationship_assessment_by_id) != relationship_id_set:
        errors.append("relationship assessments must cover every reference relationship exactly once")
    if relationship_scores:
        if set(logic_relationship_ids) != relationship_id_set:
            errors.append("logic_assessment.relationship_ids must cover every relationship")
        relationship_expected = sum(relationship_scores) / len(relationship_scores)
        if not close(logic_score, relationship_expected, tolerance=1e-6):
            errors.append(f"logic_assessment.score must equal the relationship mean: {relationship_expected:.4f}")
    elif logic_relationship_ids:
        errors.append("logic_assessment.relationship_ids must be empty when no relationships exist")
    elif not close(logic_score, 0, tolerance=1e-6):
        errors.append("logic_assessment.score must be 0 when no relationships exist")

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
            "surface_share": depths.count(1) / len(depths),
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

    validate_multi_reference(
        data.get("multi_reference"),
        scores=scores,
        l_weight=l_weight,
        s_weight=s_weight,
        errors=errors,
    )

    valid_band_names = {label for _, _, label in BANDS}
    if scores.get("band") not in valid_band_names:
        errors.append("scores.band is invalid")
    if not isinstance(scores.get("provisional"), bool):
        errors.append("scores.provisional must be boolean")

    anchor, has_anchors, formula_conflict, boundary_clarity = validate_anchor_placement(
        analysis=analysis,
        config=config,
        reference=reference,
        scores=scores,
        t_center=t_center,
        t_range=t_range,
        l_weight=l_weight,
        s_weight=s_weight,
        s_weights=s_weights,
        any_required_elements=any_required_elements,
        has_relationships=has_relationships,
        calibration_registry_path=calibration_registry_path,
        errors=errors,
    )

    validate_confidence_and_adaptation(
        analysis=analysis,
        decisions=decisions,
        evidence=evidence,
        pending_candidates=pending_candidates,
        formula_conflict=formula_conflict,
        has_anchors=has_anchors,
        boundary_clarity=boundary_clarity,
        scores=scores,
        t_center=t_center,
        t_range=t_range,
        teaching_assessments=teaching_assessments,
        allow_draft=allow_draft,
        errors=errors,
        warnings=warnings,
        metrics=metrics,
    )
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
