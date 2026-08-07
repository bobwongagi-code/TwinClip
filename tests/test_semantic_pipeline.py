#!/usr/bin/env python3
"""Tests for the model/code ownership boundary."""

from __future__ import annotations

import json
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_validate_report
from semantic_pipeline import (
    SEMANTIC_TASK_SCHEMA_VERSION,
    SemanticContractError,
    _derived_confidence,
    compile_report,
    load_run_manifest,
    read_json,
    stability_result,
    validate_task,
)
from semantic_test_helpers import write_semantic_run


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "twinclip" / "scripts"
SEMANTIC_RUN = SCRIPT_DIR / "semantic_run.py"


class SemanticPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = test_validate_report.ReportFixture(methodName="runTest")
        self.fixture.setUp()
        self.root = Path(self.fixture.temp_dir.name)
        self.report = self.fixture.report()
        self.reference_path = self.root / "reference-bundle.json"
        self.reference_path.write_text(
            json.dumps(self.report["reference_bundle"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def test_compiler_derives_scores_from_atomic_states(self) -> None:
        semantic_dir = write_semantic_run(self.report, self.fixture.creator, self.root, "semantic", self.reference_path)
        compiled = compile_report(
            reference_path=self.reference_path,
            breakdown_video=self.fixture.breakdown,
            storyboard_pdf=self.fixture.storyboard,
            creator_video=self.fixture.creator,
            semantic_dir=semantic_dir,
            duration=2.0,
            interval=1.0,
            max_frames=300,
        )
        self.assertEqual(compiled["analysis"]["scores"]["T_center"], 62.666666666666664)
        self.assertEqual(compiled["analysis"]["provenance"]["compiler_version"], "twinclip-compiler-0.2")
        task_text = "\n".join(path.read_text(encoding="utf-8") for path in (semantic_dir / "tasks").glob("*.json"))
        self.assertNotIn('"depth"', task_text)
        self.assertNotIn('"T_center"', task_text)
        self.assertIn('"depth": 2', json.dumps(compiled["analysis"]["teaching_point_assessments"]))
        stability = stability_result(compiled, load_run_manifest(semantic_dir / "run.json"))
        self.assertEqual(stability["compiler_version"], "twinclip-compiler-0.2")

    def test_confidence_m_is_bound_to_locked_anchor_boundary(self) -> None:
        decisions = [{"evidence_clarity": "clear", "evidence_ids": ["EV01"], "manual_review": False}]
        confidence = _derived_confidence(
            decisions,
            0,
            has_anchors=True,
            boundary_clarity=1,
        )
        self.assertEqual(confidence["M"], 1)
        self.assertEqual(confidence["level"], "high")
        with self.assertRaisesRegex(SemanticContractError, "boundary_clarity"):
            _derived_confidence(decisions, 0, has_anchors=True, boundary_clarity=0.25)

    def test_task_contract_rejects_derived_fields(self) -> None:
        semantic_dir = write_semantic_run(self.report, self.fixture.creator, self.root, "semantic", self.reference_path)
        run = load_run_manifest(semantic_dir / "run.json")
        task = read_json(semantic_dir / "tasks" / "teaching-DEFAULT.json")
        task["payload"]["depth"] = 2
        with self.assertRaisesRegex(SemanticContractError, "code-derived"):
            validate_task(task, run)

        task = read_json(semantic_dir / "tasks" / "teaching-DEFAULT.json")
        task["payload"]["judgments"][0]["primary_failure_dimension"] = "L"
        with self.assertRaisesRegex(SemanticContractError, "code-derived"):
            validate_task(task, run)

    def test_observation_cannot_smuggle_final_link_fields(self) -> None:
        semantic_dir = write_semantic_run(self.report, self.fixture.creator, self.root, "semantic-observation", self.reference_path)
        run = load_run_manifest(semantic_dir / "run.json")
        task = read_json(semantic_dir / "tasks" / "observation.json")
        task["payload"]["evidence_records"][0]["storyboard_node_ids"] = ["SB01"]
        with self.assertRaisesRegex(SemanticContractError, "code-owned observation fields"):
            validate_task(task, run)

    def test_publish_rejects_a_large_report_shape_and_never_writes_it(self) -> None:
        semantic_dir = write_semantic_run(self.report, self.fixture.creator, self.root, "semantic", self.reference_path)
        task = {
            "schema_version": SEMANTIC_TASK_SCHEMA_VERSION,
            "task_type": "teaching_point",
            "task_id": "attack",
            "run": read_json(semantic_dir / "tasks" / "teaching-DEFAULT.json")["run"],
            "payload": {"lane_id": "DEFAULT", "judgments": [], "scores": {"T_center": 99}},
        }
        source = self.root / "attack.json"
        source.write_text(json.dumps(task), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SEMANTIC_RUN), "publish", "--run-dir", str(semantic_dir), "--task", str(source)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("code-derived", result.stderr)
        self.assertFalse((semantic_dir / "tasks" / "attack.json").exists())

    def test_task_contract_rejects_embedded_report_payload(self) -> None:
        semantic_dir = write_semantic_run(self.report, self.fixture.creator, self.root, "embedded-report", self.reference_path)
        run = load_run_manifest(semantic_dir / "run.json")
        task = read_json(semantic_dir / "tasks" / "teaching-DEFAULT.json")
        task["payload"]["final_report"] = {"analysis": {"scores": {"T_center": 100}}}
        with self.assertRaisesRegex(SemanticContractError, "code-derived"):
            validate_task(task, run)

    def test_init_binds_fixed_evidence_and_locked_configuration(self) -> None:
        fixed_evidence = self.root / "fixed-evidence.json"
        fixed_evidence.write_text(json.dumps({"evidence": "fixture"}), encoding="utf-8")
        output_dir = self.root / "initialized-run"
        result = subprocess.run(
            [
                sys.executable,
                str(SEMANTIC_RUN),
                "init",
                "--run-id",
                "run-001",
                "--experiment-id",
                "exp-001",
                "--execution-context-id",
                "ctx-001",
                "--video-id",
                "creator-001",
                "--round",
                "1",
                "--replicate-index",
                "1",
                "--reference-bundle",
                str(self.reference_path),
                "--breakdown-video",
                str(self.fixture.breakdown),
                "--storyboard-pdf",
                str(self.fixture.storyboard),
                "--creator-video",
                str(self.fixture.creator),
                "--fixed-evidence",
                str(fixed_evidence),
                "--output-dir",
                str(output_dir),
                "--model-id",
                "fixture-model",
                "--prompt-version",
                "fixture-prompt-1",
                "--extraction-version",
                "fixture-extractor-1",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = load_run_manifest(output_dir / "run.json")
        self.assertEqual(manifest["execution_context_id"], "ctx-001")
        self.assertEqual(manifest["fixed_evidence_hash"], test_validate_report.sha256_file(fixed_evidence))
        self.assertEqual(manifest["video_id"], "creator-001")

    def test_multi_reference_uses_lane_specific_relationship_graphs(self) -> None:
        reference = self.report["reference_bundle"]
        original_point = copy.deepcopy(reference["teaching_points"][0])
        point_a = copy.deepcopy(original_point)
        point_a["id"] = "A-TP01"
        point_b = copy.deepcopy(original_point)
        point_b["id"] = "B-TP01"
        relationship_a = copy.deepcopy(reference["relationships"][0])
        relationship_a["id"] = "A-REL01"
        relationship_a["teaching_point_ids"] = ["A-TP01"]
        relationship_b = copy.deepcopy(relationship_a)
        relationship_b["id"] = "B-REL01"
        relationship_b["teaching_point_ids"] = ["B-TP01"]
        reference["teaching_points"] = {"REF-A": [point_a], "REF-B": [point_b]}
        reference["lanes"] = {
            "REF-A": {"label": "Reference A", "relationships": [relationship_a]},
            "REF-B": {"label": "Reference B", "relationships": [relationship_b]},
        }
        reference["relationships"] = [relationship_a]
        reference["content_hash"] = test_validate_report.reference_bundle_hash(reference)
        self.reference_path.write_text(json.dumps(reference, ensure_ascii=False, indent=2), encoding="utf-8")

        semantic_dir = write_semantic_run(self.report, self.fixture.creator, self.root, "multi", self.reference_path)
        ref_b_relationship = semantic_dir / "tasks" / "relationships-REF-B.json"
        task = json.loads(ref_b_relationship.read_text(encoding="utf-8"))
        task["payload"]["judgments"][0]["logic_state"] = "jump"
        ref_b_relationship.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        compiled = compile_report(
            reference_path=self.reference_path,
            breakdown_video=self.fixture.breakdown,
            storyboard_pdf=self.fixture.storyboard,
            creator_video=self.fixture.creator,
            semantic_dir=semantic_dir,
            duration=2.0,
            interval=1.0,
            max_frames=300,
        )
        errors, _, _ = test_validate_report.validate_report(compiled)
        self.assertEqual(errors, [])
        lanes = compiled["multi_reference"]["lane_comparison"]
        self.assertNotEqual(lanes["REF-A"]["S_storyboard"], lanes["REF-B"]["S_storyboard"])
        self.assertEqual(compiled["multi_reference"]["primary_reference_lane"], "REF-A")

        ref_a_relationship = semantic_dir / "tasks" / "relationships-REF-A.json"
        task_a = json.loads(ref_a_relationship.read_text(encoding="utf-8"))
        task_a["payload"]["judgments"][0]["logic_state"] = "jump"
        ref_a_relationship.write_text(json.dumps(task_a, ensure_ascii=False, indent=2), encoding="utf-8")
        task["payload"]["judgments"][0]["logic_state"] = "convincing"
        ref_b_relationship.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        compiled_b = compile_report(
            reference_path=self.reference_path,
            breakdown_video=self.fixture.breakdown,
            storyboard_pdf=self.fixture.storyboard,
            creator_video=self.fixture.creator,
            semantic_dir=semantic_dir,
            duration=2.0,
            interval=1.0,
            max_frames=300,
        )
        errors, _, _ = test_validate_report.validate_report(compiled_b)
        self.assertEqual(errors, [])
        self.assertEqual(compiled_b["multi_reference"]["primary_reference_lane"], "REF-B")

    def test_mutable_scoring_sidecar_is_rejected(self) -> None:
        semantic_dir = write_semantic_run(self.report, self.fixture.creator, self.root, "sidecar", self.reference_path)
        (semantic_dir / "scoring-config.json").write_text(json.dumps({"l_weight": 1.0}), encoding="utf-8")
        with self.assertRaisesRegex(SemanticContractError, "mutable sidecar"):
            compile_report(
                reference_path=self.reference_path,
                breakdown_video=self.fixture.breakdown,
                storyboard_pdf=self.fixture.storyboard,
                creator_video=self.fixture.creator,
                semantic_dir=semantic_dir,
                duration=2.0,
                interval=1.0,
                max_frames=300,
            )


if __name__ == "__main__":
    unittest.main()
