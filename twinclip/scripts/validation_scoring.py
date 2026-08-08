#!/usr/bin/env python3
"""Scoring, calibration, confidence, and adaptation validation sections."""

from __future__ import annotations

from typing import Any

from contracts import (  # noqa: E402
    BANDS,
    CALIBRATION_REGISTRY_SCHEMA_VERSION,
    DEFAULT_L_WEIGHT,
    DEFAULT_S_WEIGHT,
    VALID_REVIEW_STATUS,
    band_bounds,
    default_s_weights as default_s_weights_for,
    registry_hash,
)
from compute_scores import confidence_from_decisions, score_boundary_distance, score_interval  # noqa: E402
from validation_support import (  # noqa: E402
    band_index,
    close,
    eligible_evidence,
    is_number,
    non_empty_string,
    read_json_file,
    require_list,
    require_mapping,
    score_band,
)


def validate_anchor_placement(
    *,
    analysis: dict[str, Any],
    config: dict[str, Any],
    reference: dict[str, Any],
    scores: dict[str, Any],
    t_center: Any,
    t_range: Any,
    l_weight: Any,
    s_weight: Any,
    s_weights: dict[str, Any],
    any_required_elements: bool,
    has_relationships: bool,
    calibration_registry_path: str | None,
    errors: list[str],
) -> tuple[dict[str, Any], bool, bool, float]:
    valid_band_names = {label for _, _, label in BANDS}
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
        expected_s_weights = default_s_weights_for(
            has_relationships=has_relationships,
            has_required_elements=any_required_elements,
        )
        for key, expected in expected_s_weights.items():
            if not close(s_weights.get(key), expected, tolerance=1e-6):
                errors.append(f"reports without anchors must use the default S weight for {key}: {expected:.6f}")
        if is_number(t_center) and scores.get("band") != score_band(float(t_center)):
            errors.append(f"without anchors, scores.band must follow the formula band: {score_band(float(t_center))}")
        return anchor, has_anchors, formula_conflict, boundary_clarity

    if calibration_registry_path is None:
        errors.append("scoring_config.calibration_registry is required when anchors exist")
    if not non_empty_string(anchor.get("anchor_set_id")):
        errors.append("anchor_placement.anchor_set_id is required when anchors exist")
    if anchor.get("reference_bundle_id") != reference.get("id") or anchor.get("reference_bundle_version") != reference.get("version"):
        errors.append("anchors must bind to the exact reference bundle id and version")
    if anchor.get("reference_bundle_hash") != reference.get("content_hash"):
        errors.append("anchors must bind to the exact reference bundle content hash")
    if anchor.get("weights_version") != config.get("weights_version"):
        errors.append("anchor_placement.weights_version must equal scoring_config.weights_version")
    lower = anchor.get("lower_anchor")
    upper = anchor.get("upper_anchor")
    for name, item in (("lower_anchor", lower), ("upper_anchor", upper)):
        if (
            not isinstance(item, dict)
            or not non_empty_string(item.get("id"))
            or item.get("band") not in valid_band_names
            or not is_number(item.get("T_center"))
            or not 0 <= float(item.get("T_center", 0)) <= 100
        ):
            errors.append(f"anchor_placement.{name} must include id, band, and finite T_center")
        elif (
            item.get("reference_bundle_id") != reference.get("id")
            or item.get("reference_bundle_version") != reference.get("version")
            or item.get("reference_bundle_hash") != reference.get("content_hash")
        ):
            errors.append(f"anchor_placement.{name} must bind to the exact reference bundle")
    if (
        isinstance(lower, dict)
        and isinstance(upper, dict)
        and lower.get("band") in valid_band_names
        and upper.get("band") in valid_band_names
        and band_index(lower["band"]) >= band_index(upper["band"])
    ):
        errors.append("lower_anchor.band must be below upper_anchor.band")
    anchor_band = anchor.get("anchor_band")
    if anchor_band not in valid_band_names:
        errors.append("anchor_placement.anchor_band is invalid")
    elif scores.get("band") != anchor_band:
        errors.append("scores.band must equal the human-resolved anchor band")
    if isinstance(lower, dict) and isinstance(upper, dict) and anchor_band in valid_band_names and is_number(t_center):
        if not (band_index(lower["band"]) <= band_index(anchor_band) <= band_index(upper["band"])):
            errors.append("anchor_band must be bracketed by the lower and upper anchor bands")
        if not float(lower["T_center"]) <= float(t_center) <= float(upper["T_center"]):
            errors.append("T_center must be numerically bracketed by the lower and upper anchors")
        anchor_range = band_bounds(anchor_band)
        if anchor_range and isinstance(t_range, list) and len(t_range) == 2 and all(is_number(value) for value in t_range) and (float(t_range[1]) < anchor_range[0] or float(t_range[0]) > anchor_range[1]):
            errors.append("T_range must overlap the human-resolved anchor band")
    if is_number(t_center) and scores.get("formula_band") in valid_band_names:
        expected_conflict = scores.get("band") != scores.get("formula_band")
        if formula_conflict != expected_conflict:
            errors.append("anchor_placement.formula_conflict must equal the formula-versus-anchor band mismatch")
    if scores.get("provisional") is not False:
        errors.append("scores.provisional must be false when anchors exist")
    registry = read_json_file(calibration_registry_path, "scoring_config.calibration_registry", errors) if calibration_registry_path else None
    if registry is not None:
        if registry.get("schema_version") != CALIBRATION_REGISTRY_SCHEMA_VERSION or registry.get("status") != "locked":
            errors.append(
                f"calibration registry must have schema_version={CALIBRATION_REGISTRY_SCHEMA_VERSION} and status=locked"
            )
        if registry.get("content_hash") != registry_hash(registry):
            errors.append("calibration registry content_hash does not match the locked registry")
        for key, expected in {
            "anchor_set_id": anchor.get("anchor_set_id"),
            "reference_bundle_id": reference.get("id"),
            "reference_bundle_version": reference.get("version"),
            "reference_bundle_hash": reference.get("content_hash"),
            "weights_version": config.get("weights_version"),
        }.items():
            if registry.get(key) != expected:
                errors.append(f"calibration registry {key} does not match the report")
        registry_weights = require_mapping(registry, "weights", "calibration registry", errors)
        expected_weights = {
            "l_weight": l_weight,
            "s_weight": s_weight,
            "s_weights": s_weights,
        }
        for key, expected in expected_weights.items():
            if registry_weights.get(key) != expected:
                errors.append(f"calibration registry weights.{key} does not match the report")
        boundaries = registry.get("boundaries")
        boundary_match = False
        lower_band = lower.get("band") if isinstance(lower, dict) else None
        upper_band = upper.get("band") if isinstance(upper, dict) else None
        if not isinstance(boundaries, list):
            errors.append("calibration registry boundaries must be an array")
        else:
            for boundary in boundaries:
                if not isinstance(boundary, dict):
                    continue
                if boundary.get("lower_band") == lower_band and boundary.get("upper_band") == upper_band:
                    boundary_match = True
                    if boundary.get("clarity") != boundary_clarity:
                        errors.append("anchor_placement.boundary_clarity does not match the locked boundary")
        if not boundary_match:
            errors.append("the selected lower/upper anchor boundary is missing from the calibration registry")
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
            for key in ("band", "T_center", "reference_bundle_id", "reference_bundle_version", "reference_bundle_hash"):
                if registered.get(key) != report_anchor.get(key):
                    errors.append(f"{name}.{key} does not match the calibration registry")
    return anchor, has_anchors, formula_conflict, boundary_clarity


def validate_confidence_and_adaptation(
    *,
    analysis: dict[str, Any],
    decisions: list[dict[str, Any]],
    evidence: dict[str, dict[str, Any]],
    pending_candidates: int,
    formula_conflict: bool,
    has_anchors: bool,
    boundary_clarity: float,
    scores: dict[str, Any],
    t_center: Any,
    t_range: Any,
    teaching_assessments: list[Any],
    lane_selection_review: bool,
    lane_selection_margin: float | None,
    allow_draft: bool,
    errors: list[str],
    warnings: list[str],
    metrics: dict[str, float],
) -> None:
    confidence = require_mapping(analysis, "confidence", "analysis", errors)
    clear_supported = 0
    manual_review_count = 0
    for decision in decisions:
        if decision.get("manual_review") is True:
            manual_review_count += 1
        evidence_ids = decision.get("evidence_ids") if isinstance(decision.get("evidence_ids"), list) else []
        eligible_ids = [
            evidence_id
            for evidence_id in evidence_ids
            if isinstance(evidence_id, str) and evidence_id in evidence and eligible_evidence(evidence[evidence_id])
        ]
        if decision.get("evidence_clarity") == "clear" and (eligible_ids or decision.get("absence_verified") is True):
            clear_supported += 1
    decision_count = len(decisions)
    try:
        expected = confidence_from_decisions(
            clear_supported=clear_supported,
            decision_count=decision_count,
            manual_review_count=manual_review_count,
            pending_candidates=pending_candidates,
            has_anchors=has_anchors,
            boundary_clarity=boundary_clarity,
            score_boundary_distance_value=score_boundary_distance(float(t_center)) if is_number(t_center) else None,
            lane_margin=lane_selection_margin,
        )
    except ValueError as exc:
        errors.append(f"confidence deterministic recomputation failed: {exc}")
        expected = {"E": 0.0, "M": 0.0, "M_components": {}, "R": 0.0, "level": "low"}
    e_expected = expected["E"]
    r_expected = expected["R"]
    m_value = expected["M"]
    metrics["E"] = e_expected
    metrics["M"] = m_value
    metrics["R"] = r_expected
    if not close(confidence.get("E"), e_expected):
        errors.append(f"confidence.E must equal {e_expected:.4f}")
    if not close(confidence.get("R"), r_expected):
        errors.append(f"confidence.R must equal {r_expected:.4f}")
    if confidence.get("M") not in {0, 0.5, 1}:
        errors.append("confidence.M must be 0, 0.5, or 1")
    elif confidence.get("M") != m_value:
        errors.append(f"confidence.M must equal deterministic minimum {m_value}")
    components = confidence.get("M_components")
    if not isinstance(components, dict):
        errors.append("confidence.M_components must be an object")
    else:
        if set(components) != set(expected["M_components"]):
            errors.append("confidence.M_components must contain anchor_boundary, score_boundary, and lane_margin")
        for key, expected_value in expected["M_components"].items():
            if components.get(key) != expected_value:
                errors.append(f"confidence.M_components.{key} must equal {expected_value}")
    level_expected = expected["level"]
    if confidence.get("level") != level_expected:
        errors.append(f"confidence.level must be {level_expected}")
    level = confidence.get("level") if confidence.get("level") in {"high", "medium", "low"} else level_expected
    if is_number(t_center) and isinstance(t_range, list) and len(t_range) == 2:
        expected_range = score_interval(float(t_center), level)
        if t_range != expected_range:
            errors.append(f"scores.T_range must equal {expected_range} for {level} confidence")

    review_status = analysis.get("review_status")
    if review_status not in VALID_REVIEW_STATUS:
        errors.append("analysis.review_status must be pending or completed")
    needs_review = (
        level == "low"
        or manual_review_count > 0
        or pending_candidates > 0
        or formula_conflict
        or lane_selection_review
    )
    if pending_candidates and review_status == "completed":
        errors.append("analysis.review_status cannot be completed while guided candidates are pending")
    if formula_conflict and review_status == "completed":
        errors.append("analysis.review_status cannot be completed while formula and anchor bands conflict")
    if review_status == "pending" and not allow_draft:
        errors.append("analysis.review_status=pending requires --allow-draft")
    if review_status == "pending" and not needs_review:
        errors.append("analysis.review_status=pending requires an unresolved review condition")
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
