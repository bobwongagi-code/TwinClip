"""Fixtures for the atomic semantic-task pipeline tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from contracts import COMPILER_VERSION, canonical_json, sha256_bytes, sha256_file
from compute_scores import LOGIC_CHECK_IDS
from semantic_pipeline import SEMANTIC_RUN_SCHEMA_VERSION, SEMANTIC_TASK_SCHEMA_VERSION


def _write_task(task_dir: Path, task_type: str, task_id: str, run: dict[str, Any], payload: dict[str, Any]) -> None:
    task = {
        "schema_version": SEMANTIC_TASK_SCHEMA_VERSION,
        "task_type": task_type,
        "task_id": task_id,
        "run": {
            key: run[key]
            for key in (
                "run_id",
                "execution_context_id",
                "reference_bundle_hash",
                "creator_video_sha256",
                "experiment_id",
            )
            if key in run
        },
        "payload": payload,
    }
    (task_dir / f"{task_id}.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")


def write_semantic_run(report: dict[str, Any], creator: Path, root: Path, name: str, reference_path: Path) -> Path:
    analysis = report["analysis"]
    provenance = analysis["provenance"]
    semantic_dir = root / name
    tasks_dir = semantic_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    run = {
        "schema_version": SEMANTIC_RUN_SCHEMA_VERSION,
        "run_id": f"{name}-run",
        "execution_context_id": f"{name}-context",
        "reference_bundle_hash": report["reference_bundle"]["content_hash"],
        "creator_video_sha256": sha256_file(creator),
        "model_id": "fixture-model",
        "prompt_version": "fixture-prompt-1",
        "extraction_version": "fixture-extractor-1",
        "observation_method": "fixture_multimodal_review",
        "temperature": 0.0,
        "seed": 1,
        "compiler_version": COMPILER_VERSION,
        "task_schema_version": SEMANTIC_TASK_SCHEMA_VERSION,
        "source_hashes": copy.deepcopy(provenance["source_hashes"]),
        "scoring_config": copy.deepcopy(report["scoring_config"]),
        "anchor_placement": copy.deepcopy(analysis["anchor_placement"]),
    }
    run["scoring_config"].setdefault("calibration_registry", None)
    run["scoring_config_hash"] = sha256_bytes(canonical_json(run["scoring_config"]).encode("utf-8"))
    run["anchor_placement_hash"] = sha256_bytes(canonical_json(run["anchor_placement"]).encode("utf-8"))
    run["calibration_registry_sha256"] = None
    (semantic_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")

    evidence_records = []
    links = []
    for record in analysis["evidence_records"]:
        evidence = copy.deepcopy(record)
        for key in ("creator_video", "storyboard_node_ids", "candidate_id"):
            evidence.pop(key, None)
        evidence_records.append(evidence)
        links.append({"evidence_id": record["id"], "storyboard_node_ids": record["storyboard_node_ids"]})
    _write_task(tasks_dir, "observation", "observation", run, {"evidence_records": evidence_records})
    _write_task(tasks_dir, "evidence_linking", "evidence-links", run, {"links": links})

    depth_states = {
        0: ("not_observed", "not_applicable", "not_applicable", "not_applicable"),
        1: ("observed", "not_met", "not_landed", "none"),
        2: ("observed", "met", "landed", "none"),
        3: ("observed", "met", "landed", "meaningful"),
    }
    reference_points = report["reference_bundle"].get("teaching_points")
    if isinstance(reference_points, dict):
        lane_points = reference_points
    else:
        lane_points = {"DEFAULT": reference_points}
    source_point_by_id = {item["teaching_point_id"]: item for item in analysis["teaching_point_assessments"]}

    def point_source(point_id: str) -> dict[str, Any]:
        return source_point_by_id.get(point_id) or next(iter(source_point_by_id.values()))

    def point_judgment(item: dict[str, Any], point_id: str) -> dict[str, Any]:
        observed, minimum, function, transformation = depth_states[item["depth"]]
        return {
            "teaching_point_id": point_id,
            "observed_state": observed,
            "minimum_evidence_state": minimum,
            "function_state": function,
            "transformation_state": transformation,
            "evidence_ids": item["evidence_ids"],
            "evidence_clarity": item["evidence_clarity"],
            "manual_review": item["manual_review"],
            "failure_dimension_candidate": item["primary_failure_dimension"]
            if item["primary_failure_dimension"] in {"L", None}
            else None,
            "reason": item["reason"],
        }

    all_point_judgments: dict[str, dict[str, Any]] = {}
    for lane_id, points in lane_points.items():
        judgments = []
        for point in points:
            point_id = point["id"]
            item = point_source(point_id)
            judgments.append(point_judgment(item, point_id))
            all_point_judgments[point_id] = item
        _write_task(tasks_dir, "teaching_point", f"teaching-{lane_id}", run, {"lane_id": lane_id, "judgments": judgments})

    function_states = {0: "missing", 1: "fragment", 2: "understandable", 3: "complete"}
    element_states = {None: "not_required", 0: "missing", 1: "partial", 2: "correct", 3: "clear"}
    support_states = {0: "contradictory", 1: "weak", 2: "supportive", 3: "especially_clear"}
    node_judgments = [
        {
            "storyboard_node_id": item["storyboard_node_id"],
            "function_state": function_states[item["function_score"]],
            "element_state": element_states[item["element_score"]],
            "support_state": support_states[item["support_score"]],
            "evidence_ids": item["evidence_ids"],
            "evidence_clarity": item["evidence_clarity"],
            "manual_review": item["manual_review"],
            "failure_dimension_candidate": item["primary_failure_dimension"]
            if item["primary_failure_dimension"] in {"S", None}
            else None,
            "reason": item["reason"],
        }
        for item in analysis["storyboard_node_assessments"]
    ]
    _write_task(tasks_dir, "storyboard_node", "storyboard-nodes", run, {"judgments": node_judgments})

    logic_checks = []
    source_logic = report["analysis"].get("logic_assessment", {}).get("checklist", [])
    source_logic_by_id = {item["check_id"]: item for item in source_logic}
    for check_id in LOGIC_CHECK_IDS:
        item = source_logic_by_id.get(check_id, {
            "state": "met",
            "evidence_ids": ["EV01"],
            "evidence_clarity": "clear",
            "manual_review": False,
            "reason": "Fixture logic checklist judgment.",
        })
        logic_checks.append({
            "check_id": check_id,
            "state": item["state"],
            "evidence_ids": item["evidence_ids"],
            "evidence_clarity": item["evidence_clarity"],
            "manual_review": item["manual_review"],
            "reason": item["reason"],
        })
    _write_task(tasks_dir, "logic_checklist", "logic-checklist", run, {"checks": logic_checks})

    relationship_states = {0: "broken", 1: "jump", 2: "complete", 3: "convincing"}
    source_relationship_by_id = {item["relationship_id"]: item for item in analysis["relationship_assessments"]}
    reference_lanes = report["reference_bundle"].get("lanes")
    for lane_id in lane_points:
        lane = reference_lanes.get(lane_id) if isinstance(reference_lanes, dict) else None
        relationships = lane.get("relationships") if isinstance(lane, dict) else None
        if relationships is None:
            relationships = report["reference_bundle"].get("relationships", [])
        relationship_judgments = []
        for relationship in relationships:
            relationship_id = relationship["id"]
            item = source_relationship_by_id.get(relationship_id) or next(iter(source_relationship_by_id.values()))
            relationship_judgments.append(
                {
                    "relationship_id": relationship_id,
                    "logic_state": relationship_states[item["score"]],
                    "evidence_ids": item["evidence_ids"],
                    "evidence_clarity": item["evidence_clarity"],
                    "manual_review": item["manual_review"],
                    "failure_dimension_candidate": item["primary_failure_dimension"]
                    if item["primary_failure_dimension"] in {"S", None}
                    else None,
                    "reason": item["reason"],
                }
            )
        _write_task(tasks_dir, "relationship", f"relationships-{lane_id}", run, {"lane_id": lane_id, "judgments": relationship_judgments})

    applicability = {"no": "not_required", "yes": "required", "unclear": "unclear"}
    adaptation_judgments = []
    for point_id, item in all_point_judgments.items():
        adaptation_judgments.append(
            {
                "teaching_point_id": point_id,
                "applicability_state": applicability[item["adaptation_required"]],
                "compensation_state": item["adaptation_result"],
                "evidence_clarity": "clear",
                "manual_review": False,
                "failure_dimension_candidate": None,
                "reason": "Fixture adaptation judgment.",
            }
        )
    _write_task(tasks_dir, "adaptation", "adaptation", run, {"judgments": adaptation_judgments})
    return semantic_dir
