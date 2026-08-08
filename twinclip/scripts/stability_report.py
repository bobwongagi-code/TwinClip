#!/usr/bin/env python3
"""Aggregate repeated TwinClip judgments without collapsing them into a mean.

The output is an audit of distributions and instability sources.  Means are
reported as descriptive coordinates only; the script never emits an averaged
score as the experiment's decision.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
from typing import Any, Iterable

from contracts import BANDS, COMPILER_VERSION  # noqa: E402
from compute_scores import T_WEIGHT_L, T_WEIGHT_S, score_band, select_lane  # noqa: E402


REPORT_SCHEMA_VERSION = "twinclip-stability-report-0.2"
STABILITY_RUN_SCHEMA_VERSION = "twinclip-stability-run-0.3"
BOUNDARIES = tuple(float(low) for low, _, _ in BANDS[1:])


def experiment_t_weights(manifest: dict[str, Any]) -> tuple[float, float]:
    method = manifest.get("method") if isinstance(manifest.get("method"), dict) else {}
    l_weight = method.get("l_weight")
    s_weight = method.get("s_weight")
    if finite_number(l_weight) and finite_number(s_weight) and math.isclose(float(l_weight) + float(s_weight), 1.0, abs_tol=1e-6):
        return float(l_weight), float(s_weight)
    return T_WEIGHT_L, T_WEIGHT_S


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze repeated TwinClip judgment stability.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    parser.add_argument("--strict", action="store_true", help="Require every planned run exactly once")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
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


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def number(value: Any, *keys: str) -> float | None:
    if finite_number(value):
        return float(value)
    if isinstance(value, dict):
        for key in keys:
            if finite_number(value.get(key)):
                return float(value[key])
    return None


def distribution(values: Iterable[Any]) -> dict[str, Any]:
    values_list = list(values)
    counts = Counter(str(value) for value in values_list)
    total = len(values_list)
    return {
        "count": total,
        "values": dict(sorted(counts.items())),
        "shares": {key: round(value / total, 6) for key, value in sorted(counts.items())} if total else {},
    }


def pairwise_differences(values: list[float]) -> list[float]:
    return [abs(left - right) for index, left in enumerate(values) for right in values[index + 1 :]]


def numeric_summary(values: Iterable[float]) -> dict[str, Any]:
    clean = [float(value) for value in values if finite_number(value)]
    if not clean:
        return {"count": 0, "values": [], "mean": None, "median": None, "min": None, "max": None,
                "range": None, "population_std": None, "mean_abs_deviation_from_mean": None,
                "median_abs_deviation": None, "mean_pairwise_abs_difference": None,
                "max_pairwise_abs_difference": None, "distinct_count": 0}
    mean = statistics.fmean(clean)
    median = statistics.median(clean)
    deviations = [abs(value - mean) for value in clean]
    median_deviations = [abs(value - median) for value in clean]
    pairs = pairwise_differences(clean)
    return {
        "count": len(clean),
        "values": clean,
        "mean": round(mean, 6),
        "median": round(median, 6),
        "min": round(min(clean), 6),
        "max": round(max(clean), 6),
        "range": round(max(clean) - min(clean), 6),
        "population_std": round(statistics.pstdev(clean), 6),
        "mean_abs_deviation_from_mean": round(statistics.fmean(deviations), 6),
        "median_abs_deviation": round(statistics.median(median_deviations), 6),
        "mean_pairwise_abs_difference": round(statistics.fmean(pairs), 6) if pairs else 0.0,
        "max_pairwise_abs_difference": round(max(pairs), 6) if pairs else 0.0,
        "distinct_count": len(set(clean)),
    }


def categorical_switch_rate(values: list[Any]) -> float:
    if len(values) < 2:
        return 0.0
    return round(sum(left != right for left, right in zip(values, values[1:])) / (len(values) - 1), 6)


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def boundary_distance(value: float | None) -> float | None:
    if value is None:
        return None
    return round(min(abs(value - boundary) for boundary in BOUNDARIES), 6)


def as_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def selected_evidence_ids(result: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("teaching_points", "teaching_point_assessments", "storyboard_nodes", "storyboard_node_assessments",
                "relationships", "relationship_assessments"):
        for item in as_list(result.get(key)):
            for evidence_id in item.get("evidence_ids", []):
                if isinstance(evidence_id, str):
                    ids.add(evidence_id)
    logic_assessment = result.get("logic_assessment")
    if isinstance(logic_assessment, dict):
        for evidence_id in logic_assessment.get("evidence_ids", []):
            if isinstance(evidence_id, str):
                ids.add(evidence_id)
        for item in as_list(logic_assessment.get("checklist")):
            for evidence_id in item.get("evidence_ids", []):
                if isinstance(evidence_id, str):
                    ids.add(evidence_id)
    return ids


def judgment_fingerprint(run: dict[str, Any]) -> str:
    payload = {
        "scores": run.get("scores"),
        "primary_lane": run.get("primary_lane"),
        "teaching_points": run.get("teaching_points"),
        "storyboard_nodes": run.get("storyboard_nodes"),
        "relationships": run.get("relationships"),
        "logic_checklist": run.get("logic_checklist"),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def normalize_run(manifest: dict[str, Any], planned: dict[str, Any], raw: dict[str, Any], path: Path) -> dict[str, Any]:
    meta = raw.get("run") if isinstance(raw.get("run"), dict) else {}
    planned_context = planned.get("execution_context_id")
    if planned_context is not None:
        if raw.get("schema_version") != STABILITY_RUN_SCHEMA_VERSION:
            raise ValueError(f"{path}: formal stability result must use {STABILITY_RUN_SCHEMA_VERSION}")
        if raw.get("compiler_version") != COMPILER_VERSION:
            raise ValueError(f"{path}: formal stability result must declare compiler_version={COMPILER_VERSION}")
        required_identity = ("run_id", "video_id", "replicate_index", "round", "execution_context_id")
        missing = [key for key in required_identity if key not in meta]
        if missing:
            raise ValueError(f"{path}: compiled result is missing nested run identity: {missing}")
        if meta.get("execution_context_id") != planned_context:
            raise ValueError(f"{path}: execution_context_id does not match the immutable run plan")
        for key in ("run_id", "video_id", "replicate_index", "round"):
            if meta.get(key) != planned.get(key):
                raise ValueError(f"{path}: nested run.{key} does not match the immutable run plan")
        run_id = str(meta["run_id"])
        video_id = str(meta["video_id"])
        replicate_index = int(meta["replicate_index"])
        round_index = int(meta["round"])
        execution_context_id = meta["execution_context_id"]
    else:
        run_id = str(meta.get("run_id") or raw.get("run_id") or planned["run_id"])
        video_id = str(meta.get("video_id") or raw.get("video_id") or planned["video_id"])
        replicate_index = int(meta.get("replicate_index") or raw.get("replicate_index") or planned["replicate_index"])
        round_index = int(meta.get("round") or raw.get("round") or planned["round"])
        execution_context_id = meta.get("execution_context_id") or raw.get("execution_context_id")
    planned_experiment = planned.get("experiment_id")
    actual_experiment = meta.get("experiment_id") or raw.get("experiment_id")
    if planned_context is not None and planned_experiment is not None and actual_experiment != planned_experiment:
        raise ValueError(f"{path}: experiment_id does not match the immutable run plan")
    planned_fixed_evidence = planned.get("fixed_evidence_hash")
    actual_fixed_evidence = meta.get("fixed_evidence_hash") or raw.get("fixed_evidence_hash")
    if planned_context is not None and planned_fixed_evidence is not None and actual_fixed_evidence != planned_fixed_evidence:
        raise ValueError(f"{path}: fixed_evidence_hash does not match the immutable run plan")
    expected_reference_hash = manifest.get("reference_bundle", {}).get("content_hash") if isinstance(manifest.get("reference_bundle"), dict) else None
    if planned_context is not None and expected_reference_hash is not None and raw.get("reference_bundle_hash") != expected_reference_hash:
        raise ValueError(f"{path}: reference_bundle_hash does not match the frozen experiment manifest")
    scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
    l_score = number(scores, "L", "l")
    s_score = number(scores, "S", "S_storyboard", "s")
    t_score = number(scores, "T_center", "T", "t")
    if l_score is None or s_score is None or t_score is None:
        raise ValueError(f"{path}: scores must include L, S, and T_center/T")
    l_weight, s_weight = experiment_t_weights(manifest)
    formula_residual = t_score - (l_weight * l_score + s_weight * s_score)
    band = scores.get("band") or scores.get("formula_band") or score_band(t_score)
    confidence = raw.get("confidence") if isinstance(raw.get("confidence"), dict) else {}
    points = raw.get("teaching_points")
    if points is None:
        points = raw.get("teaching_point_assessments")
    nodes = raw.get("storyboard_nodes")
    if nodes is None:
        nodes = raw.get("storyboard_node_assessments")
    relationships = raw.get("relationships")
    if relationships is None:
        relationships = raw.get("relationship_assessments")
    logic_assessment = raw.get("logic_assessment") if isinstance(raw.get("logic_assessment"), dict) else {}
    logic_checklist = logic_assessment.get("checklist") if isinstance(logic_assessment.get("checklist"), list) else []
    evidence_ids = selected_evidence_ids(raw)
    primary_lane = raw.get("primary_lane") or raw.get("primary_reference_lane") or (
        raw.get("multi_reference", {}).get("primary_reference_lane")
        if isinstance(raw.get("multi_reference"), dict) else None
    )
    lane_comparison_raw = raw.get("lane_comparison") if isinstance(raw.get("lane_comparison"), dict) else {}
    lane_selection = raw.get("lane_selection") if isinstance(raw.get("lane_selection"), dict) else {}
    lane_effective_coverage = {
        str(lane_id): number(lane_value, "effective_coverage_rate", "effective_coverage")
        for lane_id, lane_value in lane_comparison_raw.items()
        if isinstance(lane_value, dict)
        and number(lane_value, "effective_coverage_rate", "effective_coverage") is not None
    }
    lane_values = {
        str(lane_id): {
            "effective_coverage_rate": number(lane_value, "effective_coverage_rate", "effective_coverage"),
            "T_center": number(lane_value, "T_center"),
        }
        for lane_id, lane_value in lane_comparison_raw.items()
        if isinstance(lane_value, dict)
        and number(lane_value, "effective_coverage_rate", "effective_coverage") is not None
        and number(lane_value, "T_center") is not None
    }
    deterministic_lane_selection = select_lane(lane_values) if len(lane_values) >= 2 else None
    if planned_context is not None:
        selected_lane_value = lane_comparison_raw.get(str(primary_lane))
        if not isinstance(primary_lane, str) or not primary_lane or not isinstance(selected_lane_value, dict):
            raise ValueError(f"{path}: formal result must name a primary lane with a lane summary")
        for score_key, aliases in (
            ("L", ("L",)),
            ("S", ("S", "S_storyboard")),
            ("T_center", ("T_center",)),
        ):
            selected_value = number(selected_lane_value, *aliases)
            actual_value = {"L": l_score, "S": s_score, "T_center": t_score}[score_key]
            if selected_value is None or not math.isclose(selected_value, actual_value, abs_tol=0.05):
                raise ValueError(f"{path}: scores.{score_key} does not match the selected lane summary")
        if band not in {label for _, _, label in BANDS}:
            raise ValueError(f"{path}: scores.band is invalid")
        if deterministic_lane_selection is not None and primary_lane != deterministic_lane_selection["chosen_lane"]:
            raise ValueError(f"{path}: primary_lane does not match deterministic lane selection")
    if deterministic_lane_selection is not None:
        primary_lane_margin = deterministic_lane_selection["margin"]
    else:
        primary_effective = lane_effective_coverage.get(str(primary_lane))
        other_effective = [value for lane_id, value in lane_effective_coverage.items()
                           if lane_id != str(primary_lane)]
        primary_lane_margin = abs(primary_effective - other_effective[0]) if primary_effective is not None and other_effective else None
    normalized = {
        "schema_version": raw.get("schema_version", "unknown"),
        "run_id": run_id,
        "experiment_id": str(meta.get("experiment_id") or raw.get("experiment_id") or manifest["experiment_id"]),
        "video_id": video_id,
        "replicate_index": replicate_index,
        "round": round_index,
        "execution_context_id": execution_context_id,
        "fixed_evidence_hash": actual_fixed_evidence,
        "result_path": str(path.resolve()),
        "scores": {"L": l_score, "S": s_score, "T_center": t_score, "band": band},
        "formula_residual": round(formula_residual, 6),
        "boundary_distance": boundary_distance(t_score),
        "confidence": {
            "E": number(confidence, "E"),
            "M": number(confidence, "M"),
            "R": number(confidence, "R"),
            "level": confidence.get("level"),
            "M_components": confidence.get("M_components") if isinstance(confidence.get("M_components"), dict) else None,
        },
        "primary_lane": primary_lane,
        "lane_effective_coverage": lane_effective_coverage,
        "primary_lane_margin": primary_lane_margin,
        "lane_selection": {
            "margin": primary_lane_margin,
            "basis": deterministic_lane_selection["margin_basis"] if deterministic_lane_selection else lane_selection.get("margin_basis"),
            "needs_manual_review": deterministic_lane_selection["needs_manual_review"] if deterministic_lane_selection else bool(lane_selection.get("needs_manual_review", False)),
            "deterministic_chosen_lane": deterministic_lane_selection["chosen_lane"] if deterministic_lane_selection else None,
            "observed_lane_matches_deterministic": (
                primary_lane == deterministic_lane_selection["chosen_lane"]
                if deterministic_lane_selection else None
            ),
        },
        "manual_pending": int(raw.get("manual_pending_count", 0) or 0) + sum(
            1 for item in as_list(raw.get("candidate_matches")) if item.get("status") == "manual_pending"
        ),
        "failure_dimensions": [item.get("primary_failure_dimension") for item in as_list(points)
            if item.get("primary_failure_dimension")],
        "evidence_ids": sorted(evidence_ids),
        "evidence_count": len(evidence_ids),
        "teaching_points": [
            {
                "id": item.get("teaching_point_id") or item.get("id"),
                "depth": item.get("depth"),
                "primary_failure_dimension": item.get("primary_failure_dimension"),
                "manual_review": bool(item.get("manual_review", False)),
                "evidence_clarity": item.get("evidence_clarity"),
                "evidence_ids": sorted(str(value) for value in item.get("evidence_ids", []) if isinstance(value, str)),
            }
            for item in as_list(points)
        ],
        "storyboard_nodes": [
            {
                "id": item.get("storyboard_node_id") or item.get("id"),
                "function_score": item.get("function_score"),
                "element_score": item.get("element_score"),
                "support_score": item.get("support_score"),
                "primary_failure_dimension": item.get("primary_failure_dimension"),
                "manual_review": bool(item.get("manual_review", False)),
                "evidence_ids": sorted(str(value) for value in item.get("evidence_ids", []) if isinstance(value, str)),
            }
            for item in as_list(nodes)
        ],
        "relationships": [
            {
                "id": item.get("relationship_id") or item.get("id"),
                "score": item.get("score"),
                "primary_failure_dimension": item.get("primary_failure_dimension"),
                "manual_review": bool(item.get("manual_review", False)),
                "evidence_ids": sorted(str(value) for value in item.get("evidence_ids", []) if isinstance(value, str)),
            }
            for item in as_list(relationships)
        ],
        "logic_checklist": [
            {
                "id": item.get("check_id") or item.get("id"),
                "score": item.get("score"),
                "state": item.get("state"),
                "manual_review": bool(item.get("manual_review", False)),
                "evidence_clarity": item.get("evidence_clarity"),
                "evidence_ids": sorted(str(value) for value in item.get("evidence_ids", []) if isinstance(value, str)),
            }
            for item in as_list(logic_checklist)
        ],
    }
    normalized["judgment_fingerprint"] = judgment_fingerprint(normalized)
    return normalized


def group_runs(runs: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        groups[str(run[key])].append(run)
    for values in groups.values():
        values.sort(key=lambda item: (item["replicate_index"], item["round"], item["run_id"]))
    return dict(sorted(groups.items()))


def per_video_profile(values: list[dict[str, Any]]) -> dict[str, Any]:
    scores = {key: numeric_summary(run["scores"][key] for run in values) for key in ("L", "S", "T_center")}
    lanes = [run["primary_lane"] for run in values]
    bands = [run["scores"]["band"] for run in values]
    evidence_sets = [set(run["evidence_ids"]) for run in values]
    evidence_similarity = [jaccard(left, right) for index, left in enumerate(evidence_sets)
                           for right in evidence_sets[index + 1:]]
    formula_residuals = numeric_summary(run["formula_residual"] for run in values)
    confidence = {
        key: numeric_summary(run["confidence"][key] for run in values if run["confidence"][key] is not None)
        for key in ("E", "M", "R")
    }
    m_component_values: dict[str, list[float]] = defaultdict(list)
    for run in values:
        components = run["confidence"].get("M_components")
        if isinstance(components, dict):
            for key, value in components.items():
                if finite_number(value):
                    m_component_values[key].append(float(value))
    depths_by_point: dict[str, list[float]] = defaultdict(list)
    for run in values:
        for point in run["teaching_points"]:
            if finite_number(point.get("depth")) and point.get("id"):
                depths_by_point[str(point["id"])].append(float(point["depth"]))
    point_summary = {
        point_id: {
            "depth": numeric_summary(depths),
            "switch_rate": categorical_switch_rate(depths),
        }
        for point_id, depths in sorted(depths_by_point.items())
    }
    logic_checks_by_id: dict[str, list[float]] = defaultdict(list)
    for run in values:
        for check in run["logic_checklist"]:
            if check.get("id") and finite_number(check.get("score")):
                logic_checks_by_id[str(check["id"])].append(float(check["score"]))
    logic_checklist_stability = {
        check_id: {
            "score": numeric_summary(scores),
            "switch_rate": categorical_switch_rate(scores),
        }
        for check_id, scores in sorted(logic_checks_by_id.items())
    }
    fingerprints = [run["judgment_fingerprint"] for run in values]
    distinct_fingerprints = len(set(fingerprints))
    lane_margins = [run["primary_lane_margin"] for run in values if run["primary_lane_margin"] is not None]
    return {
        "run_count": len(values),
        "score_distributions": scores,
        "band_distribution": distribution(bands),
        "primary_lane_distribution": distribution(lanes),
        "band_switch_rate": categorical_switch_rate(bands),
        "lane_switch_rate": categorical_switch_rate(lanes),
        "primary_lane_margin": numeric_summary(lane_margins),
        "lane_tie_rate": round(sum(margin == 0 for margin in lane_margins) / len(lane_margins), 6)
            if lane_margins else 0.0,
        "boundary_distance": numeric_summary(run["boundary_distance"] for run in values
                                              if run["boundary_distance"] is not None),
        "formula_residual": formula_residuals,
        "confidence_components": confidence,
        "confidence_M_components": {
            key: numeric_summary(items) for key, items in sorted(m_component_values.items())
        },
        "confidence_level_distribution": distribution(run["confidence"]["level"] for run in values),
        "manual_pending_rate": round(sum(run["manual_pending"] > 0 for run in values) / len(values), 6)
            if values else 0.0,
        "evidence_count": numeric_summary(run["evidence_count"] for run in values),
        "evidence_set_pairwise_jaccard": numeric_summary(evidence_similarity),
        "judgment_fingerprint_distribution": distribution(fingerprints),
        "judgment_fingerprint_distinct_count": distinct_fingerprints,
        "exact_judgment_duplicate_count": len(fingerprints) - distinct_fingerprints,
        "exact_judgment_duplicate_rate": round(
            (len(fingerprints) - distinct_fingerprints) / len(fingerprints), 6
        ) if fingerprints else 0.0,
        "consecutive_judgment_repeat_rate": round(
            1.0 - categorical_switch_rate(fingerprints), 6
        ) if len(fingerprints) > 1 else 0.0,
        "teaching_point_stability": point_summary,
        "logic_checklist_stability": logic_checklist_stability,
    }


def atomic_profiles(runs: list[dict[str, Any]], collection: str, score_keys: tuple[str, ...]) -> dict[str, Any]:
    observations: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        for item in run[collection]:
            item_id = item.get("id")
            if item_id:
                observations[(run["video_id"], str(item_id))].append({"run": run, "item": item})
    output: dict[str, Any] = {}
    for (video_id, item_id), entries in sorted(observations.items()):
        row: dict[str, Any] = {"video_id": video_id, "item_id": item_id, "run_count": len(entries)}
        for score_key in score_keys:
            values = [entry["item"].get(score_key) for entry in entries if finite_number(entry["item"].get(score_key))]
            row[score_key] = numeric_summary(values)
        row["manual_review_rate"] = round(sum(entry["item"].get("manual_review", False) for entry in entries) / len(entries), 6)
        row["evidence_clarity_distribution"] = distribution(entry["item"].get("evidence_clarity") for entry in entries)
        row["failure_dimension_distribution"] = distribution(entry["item"].get("primary_failure_dimension") for entry in entries)
        row["evidence_set_pairwise_jaccard"] = numeric_summary(
            jaccard(set(left["item"].get("evidence_ids", [])), set(right["item"].get("evidence_ids", [])))
            for index, left in enumerate(entries) for right in entries[index + 1:]
        )
        output[f"{video_id}:{item_id}"] = row
    return output


def quality_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    unknown_channels = 0
    evidence_records = 0
    records_with_unknown_channel = 0
    unknown_by_channel = {"transcript": 0, "onscreen_text": 0}
    for video in manifest.get("videos", []):
        path = video.get("fixed_evidence_path")
        if not path or not Path(path).is_file():
            continue
        snapshot = read_json(Path(path))
        for item in as_list(snapshot.get("evidence_records")):
            evidence_records += 1
            transcript_unknown = item.get("transcript") == "unknown"
            onscreen_unknown = item.get("onscreen_text") == "unknown"
            unknown_channels += int(transcript_unknown) + int(onscreen_unknown)
            unknown_by_channel["transcript"] += int(transcript_unknown)
            unknown_by_channel["onscreen_text"] += int(onscreen_unknown)
            records_with_unknown_channel += int(transcript_unknown or onscreen_unknown)
    channel_slot_count = 2 * evidence_records
    return {
        "evidence_records": evidence_records,
        "channel_slot_count": channel_slot_count,
        "unknown_channel_count": unknown_channels,
        "unknown_channel_rate": round(unknown_channels / channel_slot_count, 6) if channel_slot_count else None,
        "records_with_unknown_channel": records_with_unknown_channel,
        "record_unknown_channel_rate": round(records_with_unknown_channel / evidence_records, 6) if evidence_records else None,
        "unknown_by_channel": unknown_by_channel,
        "interpretation": "Input/evidence quality is a separate confounder; it is not a model-randomness finding.",
    }


def storyboard_dimension_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe S sub-dimensions without using them as a replicate average."""
    values: dict[str, list[float]] = {"logic": [], "function": [], "elements": [], "support": []}
    for run in runs:
        nodes = run["storyboard_nodes"]
        values["function"].append(sum(float(item["function_score"]) for item in nodes) / len(nodes) / 3 * 100 if nodes else 0.0)
        values["support"].append(sum(float(item["support_score"]) for item in nodes) / len(nodes) / 3 * 100 if nodes else 0.0)
        element_scores = [float(item["element_score"]) for item in nodes if item.get("element_score") is not None]
        values["elements"].append(sum(element_scores) / len(element_scores) / 3 * 100 if element_scores else 0.0)
        checklist_scores = [float(item["score"]) for item in run["logic_checklist"] if finite_number(item.get("score"))]
        if checklist_scores:
            values["logic"].append(sum(checklist_scores) / len(checklist_scores) * 100)
        else:
            relationships = [float(item["score"]) for item in run["relationships"] if finite_number(item.get("score"))]
            values["logic"].append(sum(relationships) / len(relationships) / 3 * 100 if relationships else 0.0)
    return {key: numeric_summary(items) for key, items in values.items()}


def root_cause_signals(runs: list[dict[str, Any]], profiles: dict[str, Any], atomic: dict[str, Any],
                      manifest: dict[str, Any], s_dimensions: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    if any(abs(run["formula_residual"]) > 0.05 for run in runs):
        l_weight, s_weight = experiment_t_weights(manifest)
        signals.append({
            "cause": "deterministic_aggregation_or_output_contract",
            "severity": "high",
            "evidence": f"At least one run violates T={l_weight:.2f}*L+{s_weight:.2f}*S by more than 0.05.",
            "action": "Recompute T and band in code after model output; reject mismatches before aggregation.",
        })
    lane_rates = [(video_id, profile["lane_switch_rate"], profile["primary_lane_margin"]["median"])
                  for video_id, profile in profiles.items()
                  if profile["lane_switch_rate"] > 0]
    if lane_rates:
        signals.append({
            "cause": "reference_lane_selection_drift",
            "severity": "high",
            "evidence": {"videos": lane_rates},
            "action": "Make effective-coverage, T, and declared lane order a deterministic post-processing rule; inspect cases with a narrow lane margin or a true tie.",
        })
    repeated_trajectories = [
        (video_id, profile["judgment_fingerprint_distinct_count"], profile["exact_judgment_duplicate_rate"],
         profile["consecutive_judgment_repeat_rate"], profile["judgment_fingerprint_distribution"]["values"])
        for video_id, profile in profiles.items()
        if profile["exact_judgment_duplicate_rate"] >= 0.40
        or profile["consecutive_judgment_repeat_rate"] >= 0.50
    ]
    if repeated_trajectories:
        signals.append({
            "cause": "execution_context_or_duplicate_trajectory",
            "severity": "high",
            "evidence": {"videos": repeated_trajectories},
            "action": "Treat prompt-reset continuations as non-independent until proven otherwise; rerun with process- or thread-isolated contexts, immutable run identity checks, and a fresh worker for every replicate.",
        })
    boundary_videos = [
        (video_id, profile["score_distributions"]["T_center"]["range"],
         profile["boundary_distance"]["median"])
        for video_id, profile in profiles.items()
        if profile["score_distributions"]["T_center"]["range"] is not None
        and profile["score_distributions"]["T_center"]["range"] >= 5
        and profile["boundary_distance"]["median"] is not None
        and profile["boundary_distance"]["median"] <= 5
    ]
    if boundary_videos:
        signals.append({
            "cause": "band_boundary_amplification",
            "severity": "high",
            "evidence": {"videos": boundary_videos},
            "action": "Keep raw score distribution and boundary distance visible; do not use a single band as the only acceptance output.",
        })
    evidence_drift = [
        (video_id, profile["evidence_set_pairwise_jaccard"]["median"])
        for video_id, profile in profiles.items()
        if profile["evidence_set_pairwise_jaccard"]["median"] is not None
        and profile["evidence_set_pairwise_jaccard"]["median"] < 0.8
    ]
    if evidence_drift:
        signals.append({
            "cause": "evidence_selection_drift",
            "severity": "medium",
            "evidence": {"videos": evidence_drift},
            "action": "Add an evidence-linking checklist and stable evidence IDs; keep blind evidence fixed before changing scoring thresholds.",
        })
    score_drift_with_stable_evidence = []
    for video_id, profile in profiles.items():
        t = profile["score_distributions"]["T_center"]
        e = profile["evidence_set_pairwise_jaccard"]
        if t["range"] is not None and t["range"] >= 5 and (e["median"] is None or e["median"] >= 0.8):
            score_drift_with_stable_evidence.append((video_id, t["range"], e["median"]))
    if score_drift_with_stable_evidence:
        signals.append({
            "cause": "rubric_threshold_or_semantic_judgment_drift",
            "severity": "high",
            "evidence": {"videos": score_drift_with_stable_evidence},
            "action": "Create contrastive boundary examples for the unstable points and require a short rubric reason tied to minimum evidence.",
        })
    unstable_s_dimensions = {
        key: summary
        for key, summary in s_dimensions.items()
        if summary.get("range") is not None and summary["range"] > 0
    }
    if unstable_s_dimensions:
        signals.append({
            "cause": "storyboard_atomic_judgment_drift",
            "severity": "high",
            "evidence": {"dimensions": unstable_s_dimensions},
            "action": "Keep S aggregation in code; judge overall sales logic as five independent checks and calibrate each unstable node/check boundary with contrastive evidence.",
        })
    confidence_levels = {run["confidence"]["level"] for run in runs if run["confidence"]["level"] is not None}
    confidence_component_distinct = {
        key: len({run["confidence"][key] for run in runs if run["confidence"][key] is not None})
        for key in ("E", "M", "R")
    }
    m_component_distinct = {
        key: len({run["confidence"]["M_components"].get(key) for run in runs
                  if isinstance(run["confidence"].get("M_components"), dict)
                  and run["confidence"]["M_components"].get(key) is not None})
        for key in ("anchor_boundary", "score_boundary", "lane_margin")
    }
    m_components_collapsed = not any(m_component_distinct.values()) or all(
        count <= 1 for count in m_component_distinct.values()
    )
    if confidence_levels == {"medium"} and all(count <= 2 for count in confidence_component_distinct.values()) and m_components_collapsed:
        signals.append({
            "cause": "confidence_output_collapse",
            "severity": "high",
            "evidence": {
                "confidence_levels": sorted(confidence_levels),
                "component_distinct_counts": confidence_component_distinct,
                "M_component_distinct_counts": m_component_distinct,
            },
            "action": "Derive confidence from evidence completeness, lane margin, score-boundary distance, and manual-review flags; keep language/ASR quality as a separate confounder and reject a default medium label when its components do not vary.",
        })
    depth_drift = [
        key for key, row in atomic.items()
        if row.get("depth", {}).get("range") is not None and row["depth"]["range"] > 0
    ]
    if depth_drift:
        signals.append({
            "cause": "teaching_point_threshold_drift",
            "severity": "medium",
            "evidence": {"unstable_video_point_count": len(depth_drift), "examples": depth_drift[:20]},
            "action": "Calibrate each 0-3 teaching-point boundary with positive/negative contrast examples; do not tune only on T.",
        })
    if any(profile["confidence_level_distribution"]["values"].get("low", 0) for profile in profiles.values()):
        signals.append({
            "cause": "confidence_or_manual_review_instability",
            "severity": "medium",
            "evidence": "At least one video changed into the low-confidence category in repeated runs.",
            "action": "Keep E/M/R as separate observations and route low-confidence runs to review; do not relax thresholds.",
        })
    quality = quality_summary(manifest)
    if quality["unknown_channel_rate"] is not None and quality["unknown_channel_rate"] > 0.10:
        signals.append({
            "cause": "fixed_evidence_quality_or_language_confounder",
            "severity": "confounder",
            "evidence": quality,
            "action": "Improve VidLingo ASR/OCR or Malay/Manglish review separately before attributing the variation to semantic judgment.",
        })
    if not signals:
        signals.append({
            "cause": "no_dominant_instability_signal_detected",
            "severity": "info",
            "evidence": "The configured thresholds found no clear concentration of variation.",
            "action": "Keep the raw distributions and continue periodic non-anchor QA.",
        })
    order = {"high": 0, "medium": 1, "confounder": 2, "info": 3}
    return sorted(signals, key=lambda item: order.get(item["severity"], 9))


def build_report(manifest: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_video = group_runs(runs, "video_id")
    profiles = {video_id: per_video_profile(values) for video_id, values in by_video.items()}
    t_values = [run["scores"]["T_center"] for run in runs]
    per_video_means = [profile["score_distributions"]["T_center"]["mean"] for profile in profiles.values()]
    within_variances = [profile["score_distributions"]["T_center"]["population_std"] ** 2
                        for profile in profiles.values()
                        if profile["score_distributions"]["T_center"]["population_std"] is not None]
    between_variance = statistics.pvariance(per_video_means) if len(per_video_means) > 1 else 0.0
    within_variance = statistics.fmean(within_variances) if within_variances else 0.0
    total_variance = between_variance + within_variance
    atomic_points = atomic_profiles(runs, "teaching_points", ("depth",))
    atomic_nodes = atomic_profiles(runs, "storyboard_nodes", ("function_score", "element_score", "support_score"))
    atomic_relationships = atomic_profiles(runs, "relationships", ("score",))
    atomic_logic_checks = atomic_profiles(runs, "logic_checklist", ("score",))
    s_dimensions = storyboard_dimension_summary(runs)
    round_groups = group_runs(runs, "round")
    round_effect = {
        str(round_index): {
            "run_count": len(values),
            "T_center": numeric_summary(run["scores"]["T_center"] for run in values),
            "L": numeric_summary(run["scores"]["L"] for run in values),
            "S": numeric_summary(run["scores"]["S"] for run in values),
        }
        for round_index, values in round_groups.items()
    }
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "experiment": {
            "experiment_id": manifest["experiment_id"],
            "planned_video_count": manifest.get("video_count"),
            "planned_replicates": manifest.get("replicates"),
            "observed_run_count": len(runs),
            "fixed_observation": manifest.get("fixed_observation"),
            "method": manifest.get("method"),
            "execution_audit": manifest.get("execution_audit"),
        },
        "interpretation_rule": "No final score is obtained by averaging replicates. Numeric centers are descriptive; decisions use distributions, instability, and root-cause evidence.",
        "coverage": {
            "runs": len(runs),
            "videos": len(by_video),
            "replicates_per_video": {video_id: len(values) for video_id, values in by_video.items()},
            "rounds": len(round_groups),
        },
        "global_distributions": {
            "T_center": numeric_summary(t_values),
            "L": numeric_summary(run["scores"]["L"] for run in runs),
            "S": numeric_summary(run["scores"]["S"] for run in runs),
            "band": distribution(run["scores"]["band"] for run in runs),
            "primary_lane": distribution(run["primary_lane"] for run in runs),
            "confidence_level": distribution(run["confidence"]["level"] for run in runs),
            "confidence_components": {
                key: numeric_summary(run["confidence"][key] for run in runs if run["confidence"][key] is not None)
                for key in ("E", "M", "R")
            },
            "confidence_M_components": {
                key: numeric_summary(
                    run["confidence"]["M_components"].get(key)
                    for run in runs
                    if isinstance(run["confidence"].get("M_components"), dict)
                    and run["confidence"]["M_components"].get(key) is not None
                )
                for key in ("anchor_boundary", "score_boundary", "lane_margin")
            },
            "manual_pending": distribution(run["manual_pending"] > 0 for run in runs),
        },
        "execution_repetition": {
            "videos_with_high_exact_or_consecutive_repetition": sum(
                profile["exact_judgment_duplicate_rate"] >= 0.40
                or profile["consecutive_judgment_repeat_rate"] >= 0.50
                for profile in profiles.values()
            ),
            "interpretation": "Exact repeated judgment fingerprints are reported as an execution/context signal, not treated as evidence of independent model stability.",
        },
        "variance_decomposition": {
            "between_video_variance_of_video_means": round(between_variance, 6),
            "within_video_variance_mean": round(within_variance, 6),
            "within_video_share_of_decomposed_variance": round(within_variance / total_variance, 6) if total_variance else 0.0,
            "between_video_share_of_decomposed_variance": round(between_variance / total_variance, 6) if total_variance else 0.0,
            "interpretation": "Within-video variance is the repeated-judgment instability; between-video variance is content separation plus any content-dependent judgment effect.",
        },
        "round_effect": round_effect,
        "per_video": profiles,
        "atomic_stability": {
            "teaching_points": atomic_points,
            "storyboard_nodes": atomic_nodes,
            "relationships": atomic_relationships,
            "logic_checklist": atomic_logic_checks,
        },
        "storyboard_dimension_stability": s_dimensions,
        "root_cause_signals": root_cause_signals(runs, profiles, atomic_points, manifest, s_dimensions),
        "quality_confounder": quality_summary(manifest),
        "raw_runs": runs,
    }
    return report


def markdown_report(report: dict[str, Any]) -> str:
    def fmt(value: Any, digits: int = 2) -> str:
        return "n/a" if value is None else f"{float(value):.{digits}f}"

    experiment = report["experiment"]
    global_t = report["global_distributions"]["T_center"]
    variance = report["variance_decomposition"]
    s_dimensions = report.get("storyboard_dimension_stability", {})
    profiles = report["per_video"]
    ranked = sorted(
        profiles.items(),
        key=lambda item: (
            -(item[1]["score_distributions"]["T_center"]["range"] or 0),
            -(item[1]["score_distributions"]["T_center"]["mean_pairwise_abs_difference"] or 0),
        ),
    )
    lines = [
        "# TwinClip 重复运行稳定性复盘",
        "",
        f"实验 `{experiment['experiment_id']}` 共观察 {report['coverage']['runs']} 次运行，覆盖 {report['coverage']['videos']} 条视频。",
        "",
        "## 先看结论",
        "",
        "本报告不把 5 次结果取平均作为最终分数。均值和中位数只用于描述分布位置；验收和改进依据是极差、总体标准差、平均绝对偏差、两两平均绝对差、档位/标杆切换和逐点不稳定性。",
        "",
        f"T 的全体运行分布为：中位数 {global_t['median']:.2f}，极差 {global_t['range']:.2f}，总体标准差 {global_t['population_std']:.2f}，平均绝对偏差 {global_t['mean_abs_deviation_from_mean']:.2f}，两两平均绝对差 {global_t['mean_pairwise_abs_difference']:.2f}。",
        f"按方差分解，重复判断的组内方差占当前分解方差的 {variance['within_video_share_of_decomposed_variance']:.1%}；这是 16 条视频的总体描述，不代表每条视频都稳定，必须结合下方逐视频极差、标准差和切换率阅读。这个比例不用于生成新的总分。",
        f"另有 {report['execution_repetition']['videos_with_high_exact_or_consecutive_repetition']} 条视频出现较高的完整判断指纹重复或连续重复；这部分不能直接当作独立采样后的稳定性。",
        "",
        "S 分项的历史分布（旧版关系判断仅作为 logic 的代理）为：" + "；".join(
            f"{key} 极差 {value.get('range'):.2f}、总体标准差 {value.get('population_std'):.2f}"
            for key, value in s_dimensions.items()
            if value.get("range") is not None and value.get("population_std") is not None
        ) + "。这支持优先拆解 S 的原子判断，但不等于新 checklist 已经完成验证。",
        "",
        "## 波动最大的样本",
        "",
        "| 视频 | T 中位数 | T 极差 | T 总体标准差 | T 两两平均绝对差 | 档位切换率 | 标杆切换率 | 标杆 margin 中位数 | 证据集合两两 Jaccard 中位数 | 精确判断重复率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for video_id, profile in ranked[:10]:
        t = profile["score_distributions"]["T_center"]
        evidence = profile["evidence_set_pairwise_jaccard"]["median"]
        lines.append(
            f"| `{video_id}` | {fmt(t['median'])} | {fmt(t['range'])} | {fmt(t['population_std'])} | "
            f"{fmt(t['mean_pairwise_abs_difference'])} | {profile['band_switch_rate']:.1%} | "
            f"{profile['lane_switch_rate']:.1%} | {fmt(profile['primary_lane_margin']['median'], 3)} | "
            f"{fmt(evidence)} | "
            f"{profile['exact_judgment_duplicate_rate']:.1%} |"
        )
    lines.extend(["", "## 根因信号", ""])
    for signal in report["root_cause_signals"]:
        lines.append(f"- **{signal['severity']} / {signal['cause']}**：{signal['evidence']}")
        lines.append(f"  建议：{signal['action']}")
    lines.extend([
        "",
        "## 解释边界",
        "",
        f"本轮固定了观察证据，未重新跑 ASR/OCR：`{experiment['fixed_observation'].get('mode')}`。因此本轮测到的主要是语义匹配、量表阈值、标杆选择和置信度判断的波动；不能把结果直接解释成端到端 ASR/OCR 稳定性。",
        "执行上下文审计由嵌套 `run` 身份、唯一 `execution_context_id` 和固定证据 hash 完成；代码不会把重复 fingerprint 当成独立性证明。fresh worker/process 的真实隔离仍必须由运行器记录，不能从分数反推。",
        "分层根因、修复优先级和修复后验收条件见同目录的 `POST-RUN-IMPROVEMENTS.md`；本轮没有把修复后的结果伪装成已验证。",
        f"证据质量检查发现 {report['quality_confounder']['unknown_channel_count']} 个未知渠道槽位，占 {report['quality_confounder']['channel_slot_count']} 个槽位（{report['quality_confounder']['unknown_channel_rate']!s}）；有 {report['quality_confounder']['records_with_unknown_channel']} 条证据记录至少缺一个渠道（记录级比例 {report['quality_confounder']['record_unknown_channel_rate']!s}）。语言或证据质量是混杂因素，需要单独实验验证，不能用放宽评分阈值解决。",
        "",
        "## 下一步修复顺序",
        "",
        "1. 先修复所有公式残差、标杆选择和重复扣分等可确定性问题。",
        "2. 对极差大且证据集合稳定的教学点，补充 0-3 分对照锚点和最小证据反例。",
        "3. 对证据集合本身波动的样本，先修盲提取和证据 ID 链接，再评估评分波动。",
        "4. 修复后保留未参与调参的留出视频，重新跑同样的 5 次设计；比较分布收窄和档位切换率，而不是比较均值是否更好看。",
        "",
        "原始运行记录见同目录的 `raw-results/` JSON；每个运行均保留 `run_id`、轮次、视频、证据集合、L/S/T、档位、置信度和逐点判断。",
    ])
    return "\n".join(lines) + "\n"


def load_runs(manifest: dict[str, Any], results_dir: Path, strict: bool) -> list[dict[str, Any]]:
    planned = manifest.get("runs")
    if not isinstance(planned, list) or not planned:
        raise ValueError("manifest.runs must be a non-empty array")
    expected: dict[str, dict[str, Any]] = {}
    for item in planned:
        if not isinstance(item, dict) or not item.get("run_id"):
            raise ValueError("manifest contains an invalid run entry")
        run_id = str(item["run_id"])
        if run_id in expected:
            raise ValueError(f"duplicate planned run_id: {run_id}")
        expected[run_id] = item
    if strict:
        manifest_experiment_id = manifest.get("experiment_id")
        if not isinstance(manifest_experiment_id, str) or not manifest_experiment_id:
            raise ValueError("strict stability manifests require a non-empty experiment_id")
        contexts: set[str] = set()
        for run_id, item in expected.items():
            required = (
                "experiment_id",
                "video_id",
                "replicate_index",
                "round",
                "execution_context_id",
                "fixed_evidence_hash",
            )
            missing = [key for key in required if item.get(key) in (None, "")]
            if missing:
                raise ValueError(f"strict stability plan {run_id} is missing immutable identity: {missing}")
            if item.get("experiment_id") != manifest_experiment_id:
                raise ValueError(f"strict stability plan {run_id}.experiment_id does not match the manifest")
            context_id = item.get("execution_context_id")
            if context_id in contexts:
                raise ValueError(f"strict stability plan reuses execution_context_id: {context_id}")
            contexts.add(context_id)
            digest = item.get("fixed_evidence_hash")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest.lower())
            ):
                raise ValueError(f"strict stability plan {run_id}.fixed_evidence_hash must be a SHA-256 digest")
        method = manifest.get("method")
        if not isinstance(method, dict) or method.get("compiler_version") != COMPILER_VERSION:
            raise ValueError(f"strict stability manifests must declare compiler_version={COMPILER_VERSION}")
        videos = manifest.get("videos")
        video_hashes = {
            str(video.get("video_id")): video.get("fixed_evidence_hash")
            for video in videos
            if isinstance(video, dict) and video.get("video_id")
        } if isinstance(videos, list) else {}
        for run_id, item in expected.items():
            planned_hash = video_hashes.get(str(item["video_id"]))
            if planned_hash is None:
                raise ValueError(f"strict stability plan {run_id} has no fixed-evidence record for video {item['video_id']}")
            if planned_hash != item["fixed_evidence_hash"]:
                raise ValueError(f"strict stability plan {run_id}.fixed_evidence_hash does not match its video record")
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    reference_path = manifest.get("reference_bundle", {}).get("path") if isinstance(manifest.get("reference_bundle"), dict) else None
    reference = read_json(Path(reference_path)) if reference_path and Path(reference_path).is_file() else {}
    expected_nodes = {str(item.get("id")) for item in as_list(reference.get("storyboard_nodes")) if item.get("id")}
    expected_relationships_by_lane = {
        lane_id: {str(item.get("id")) for item in as_list(lane.get("relationships")) if item.get("id")}
        for lane_id, lane in (reference.get("lanes", {}) or {}).items()
        if isinstance(lane, dict) and "relationships" in lane
    }
    if not expected_relationships_by_lane:
        expected_relationships_by_lane = {
            "DEFAULT": {str(item.get("id")) for item in as_list(reference.get("relationships")) if item.get("id")}
        }
    l_weight, s_weight = experiment_t_weights(manifest)
    expected_points_by_lane = {
        lane_id: {str(item.get("id")) for item in as_list(lane.get("teaching_points")) if item.get("id")}
        for lane_id, lane in (reference.get("lanes", {}) or {}).items()
        if isinstance(lane, dict)
    }
    for run_id, plan in sorted(expected.items()):
        path = results_dir / f"{run_id}.json"
        if not path.is_file():
            if strict:
                raise ValueError(f"missing result for planned run: {run_id}")
            continue
        raw = read_json(path)
        normalized = normalize_run(manifest, plan, raw, path)
        if normalized["run_id"] != run_id:
            raise ValueError(f"{path}: run_id {normalized['run_id']} does not match filename {run_id}")
        if normalized["video_id"] != plan["video_id"]:
            raise ValueError(f"{path}: video_id does not match manifest")
        if normalized["replicate_index"] != int(plan["replicate_index"]):
            raise ValueError(f"{path}: replicate_index does not match manifest")
        if normalized["round"] != int(plan["round"]):
            raise ValueError(f"{path}: round does not match manifest")
        if strict:
            node_ids = {str(item.get("id")) for item in normalized["storyboard_nodes"] if item.get("id")}
            relationship_ids = {str(item.get("id")) for item in normalized["relationships"] if item.get("id")}
            point_ids = {str(item.get("id")) for item in normalized["teaching_points"] if item.get("id")}
            if expected_nodes and node_ids != expected_nodes:
                raise ValueError(f"{path}: storyboard_nodes must cover {sorted(expected_nodes)} exactly")
            expected_relationships = expected_relationships_by_lane.get(
                str(normalized["primary_lane"]), expected_relationships_by_lane.get("DEFAULT", set())
            )
            if expected_relationships and relationship_ids != expected_relationships:
                raise ValueError(f"{path}: relationships must cover {sorted(expected_relationships)} exactly")
            expected_points = expected_points_by_lane.get(str(normalized["primary_lane"]), set())
            if expected_points and point_ids != expected_points:
                raise ValueError(f"{path}: teaching_points do not cover the selected lane exactly")
            if abs(normalized["formula_residual"]) > 0.05:
                raise ValueError(f"{path}: T does not equal {l_weight:.2f}*L+{s_weight:.2f}*S")
        if run_id in seen:
            raise ValueError(f"duplicate result: {run_id}")
        seen.add(run_id)
        runs.append(normalized)
    if strict and len(runs) != len(expected):
        raise ValueError(f"expected {len(expected)} runs, loaded {len(runs)}")
    if not runs:
        raise ValueError("no stability results found")
    return runs


def main() -> int:
    try:
        args = parse_args()
        manifest = read_json(args.manifest.expanduser().resolve())
        runs = load_runs(manifest, args.results_dir.expanduser().resolve(), args.strict)
        report = build_report(manifest, runs)
        write_json(args.output_json.expanduser().resolve(), report)
        write_text(args.output_md.expanduser().resolve(), markdown_report(report))
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "runs": len(runs),
        "videos": report["coverage"]["videos"],
        "output_json": str(args.output_json.expanduser().resolve()),
        "output_md": str(args.output_md.expanduser().resolve()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
