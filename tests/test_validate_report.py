#!/usr/bin/env python3
"""Regression tests for TwinClip's report invariants."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "twinclip" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
from contracts import (  # noqa: E402
    ANALYSIS_VERSION,
    PREPARE_MANIFEST_SCHEMA_VERSION,
    analysis_id,
    canonical_json,
    provenance_fingerprint,
    reference_bundle_hash,
    registry_hash,
    sha256_bytes,
    sha256_file,
)
from validate_report import SCHEMA_VERSION, validate_report  # noqa: E402
from validation_support import score_band  # noqa: E402


class ReportFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.breakdown = root / "breakdown.mp4"
        self.creator = root / "creator.mp4"
        self.storyboard = root / "storyboard.pdf"
        for path in (self.breakdown, self.creator, self.storyboard):
            path.write_bytes(b"fixture")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def report(self) -> dict:
        creator = str(self.creator.resolve())
        report = {
            "schema_version": SCHEMA_VERSION,
            "reference_bundle": {
                "id": "bundle-1",
                "version": "1.0",
                "source_inputs": {
                    "breakdown_video": str(self.breakdown.resolve()),
                    "storyboard_pdf": str(self.storyboard.resolve()),
                },
                "source_content_hashes": {
                    "breakdown_video": {
                        "sha256": sha256_file(self.breakdown),
                        "bytes": self.breakdown.stat().st_size,
                    },
                    "storyboard_pdf": {
                        "sha256": sha256_file(self.storyboard),
                        "bytes": self.storyboard.stat().st_size,
                    },
                },
                "skeleton_mode": "storyboard",
                "status": "locked",
                "score_ready": True,
                "storyboard_nodes": [
                    {
                        "id": "SB01",
                        "label": "Hook",
                        "source_range": [0, 1],
                        "function": "Establish the problem",
                        "required_elements": ["problem evidence"],
                    },
                    {
                        "id": "SB02",
                        "label": "Transition",
                        "source_range": [1, 2],
                        "function": "Move to the proof",
                        "required_elements": [],
                    },
                ],
                "teaching_points": [
                    {
                        "id": "TP01",
                        "source_type": "breakdown_explicit",
                        "source_locator": "breakdown 00:00-00:01, teaching point 1",
                        "stage": "Hook",
                        "name": "Problem hook",
                        "content_function": "Create relevance",
                        "core_meaning": "Show the concrete problem",
                        "persuasion_element": "problem salience",
                        "evidence_method": "observable problem",
                        "logical_role": "opening",
                        "allowed_substitutions": ["another concrete problem proof"],
                        "minimum_evidence": ["problem is visible"],
                        "false_positive_guards": ["generic close-up alone does not count"],
                        "source_ranges": [[0, 1]],
                        "storyboard_node_ids": ["SB01"],
                    }
                ],
                "relationships": [
                    {
                        "id": "REL01",
                        "type": "problem_solution",
                        "from_node_ids": ["SB01"],
                        "to_node_ids": ["SB02"],
                        "teaching_point_ids": ["TP01"],
                        "description": "The problem leads into the proof",
                    }
                ],
            },
            "scoring_config": {
                "l_weight": 0.7,
                "s_weight": 0.3,
                "s_weights": {
                    "logic": 0.35,
                    "function": 0.299,
                    "elements": 0.247,
                    "support": 0.104,
                },
                "weights_version": "2.0-checklist-default",
            },
            "analysis": {
                "creator_videos": [creator],
                "media_durations": {creator: 2.0},
                "evidence_records": [
                    {
                        "id": "EV01",
                        "creator_video": creator,
                        "start_seconds": 0.1,
                        "end_seconds": 0.8,
                        "visual": "Creator points to the visible problem area",
                        "onscreen_text": "unknown",
                        "transcript": "Here is the problem",
                        "source_channels": ["visual", "voiceover"],
                        "observed_function": "Problem demonstration",
                        "coverage_scope": "Hook segment",
                        "scope_complete": True,
                        "evidence_scope": "segment",
                        "storyboard_node_ids": ["SB01"],
                        "observation_mode": "blind",
                        "human_confirmation": "not_required",
                    },
                    {
                        "id": "EV02",
                        "creator_video": creator,
                        "start_seconds": 1.0,
                        "end_seconds": 1.8,
                        "visual": "A transition into the product proof",
                        "onscreen_text": "unknown",
                        "transcript": "unknown",
                        "source_channels": ["visual"],
                        "observed_function": "Transition to proof",
                        "coverage_scope": "Transition through end of video",
                        "scope_complete": True,
                        "evidence_scope": "segment",
                        "storyboard_node_ids": ["SB02"],
                        "observation_mode": "blind",
                        "human_confirmation": "not_required",
                    },
                    {
                        "id": "EV-FULL",
                        "creator_video": creator,
                        "start_seconds": 0.0,
                        "end_seconds": 2.0,
                        "visual": "The complete creator video was inspected",
                        "onscreen_text": "unknown",
                        "transcript": "unknown",
                        "source_channels": ["visual"],
                        "observed_function": "Complete video scope inspection",
                        "coverage_scope": "Entire creator video",
                        "scope_complete": True,
                        "evidence_scope": "full_video",
                        "storyboard_node_ids": ["SB01", "SB02"],
                        "observation_mode": "blind",
                        "human_confirmation": "not_required",
                    },
                ],
                "teaching_point_assessments": [
                    {
                        "teaching_point_id": "TP01",
                        "depth": 2,
                        "evidence_ids": ["EV01"],
                        "reason": "The problem function is independently recognizable",
                        "done_well": "Uses a concrete problem",
                        "missing_or_misused": "The result preview is brief",
                        "manual_review": False,
                        "evidence_clarity": "clear",
                        "absence_verified": False,
                        "primary_failure_dimension": None,
                        "failure_id": None,
                        "adaptation_required": "no",
                        "adaptation_result": "not_needed",
                    }
                ],
                "storyboard_node_assessments": [
                    {
                        "storyboard_node_id": "SB01",
                        "function_score": 2,
                        "element_score": 2,
                        "support_score": 2,
                        "evidence_ids": ["EV01"],
                        "reason": "The opening function is covered",
                        "manual_review": False,
                        "evidence_clarity": "clear",
                        "absence_verified": False,
                        "primary_failure_dimension": None,
                        "failure_id": None,
                    },
                    {
                        "storyboard_node_id": "SB02",
                        "function_score": 0,
                        "element_score": None,
                        "support_score": 0,
                        "evidence_ids": ["EV-FULL"],
                        "reason": "The transition is not a scored persuasion element",
                        "manual_review": False,
                        "evidence_clarity": "clear",
                        "absence_verified": True,
                        "primary_failure_dimension": None,
                        "failure_id": None,
                    },
                ],
                "logic_assessment": {
                    "score": 1.8,
                    "checklist_mean": 0.6,
                    "checklist": [
                        {
                            "check_id": "hook_leads_need",
                            "state": "met",
                            "score": 1,
                            "evidence_ids": ["EV01"],
                            "reason": "The hook establishes the problem.",
                            "manual_review": False,
                            "evidence_clarity": "clear",
                            "absence_verified": False,
                        },
                        {
                            "check_id": "points_answer_problem",
                            "state": "met",
                            "score": 1,
                            "evidence_ids": ["EV01", "EV02"],
                            "reason": "The point responds to the problem.",
                            "manual_review": False,
                            "evidence_clarity": "clear",
                            "absence_verified": False,
                        },
                        {
                            "check_id": "claims_supported",
                            "state": "met",
                            "score": 1,
                            "evidence_ids": ["EV01", "EV02"],
                            "reason": "The claim has observable support.",
                            "manual_review": False,
                            "evidence_clarity": "clear",
                            "absence_verified": False,
                        },
                        {
                            "check_id": "cta_has_reason",
                            "state": "not_met",
                            "score": 0,
                            "evidence_ids": ["EV-FULL"],
                            "reason": "The full video does not establish a purchase reason before CTA.",
                            "manual_review": False,
                            "evidence_clarity": "clear",
                            "absence_verified": True,
                        },
                        {
                            "check_id": "coherent_if_reordered",
                            "state": "not_met",
                            "score": 0,
                            "evidence_ids": ["EV-FULL"],
                            "reason": "The full route remains incomplete.",
                            "manual_review": False,
                            "evidence_clarity": "clear",
                            "absence_verified": True,
                        },
                    ],
                    "relationship_ids": ["REL01"],
                    "evidence_ids": ["EV01", "EV02"],
                    "reason": "The route is derived from five independent checks.",
                    "manual_review": False,
                    "evidence_clarity": "clear",
                    "absence_verified": False,
                    "primary_failure_dimension": None,
                    "failure_id": None,
                },
                "S_components": {
                    "logic_coherence": 60.0,
                    "node_average": 46.0,
                    "function": 33.33333333333333,
                    "elements": 66.66666666666667,
                    "support": 33.33333333333333,
                },
                "relationship_assessments": [
                    {
                        "relationship_id": "REL01",
                        "score": 2,
                        "evidence_ids": ["EV01", "EV02"],
                        "reason": "The transition is understandable",
                        "manual_review": False,
                        "evidence_clarity": "clear",
                        "absence_verified": False,
                        "primary_failure_dimension": None,
                        "failure_id": None,
                    }
                ],
                "candidate_matches": [],
                "scores": {
                    "L": 66.6666667,
                    "S": 50.9,
                    "T_center": 61.9366667,
                    "T_range": [52, 72],
                    "formula_band": "多点结构化迁移",
                    "band": "多点结构化迁移",
                    "provisional": True,
                },
                "coverage": {
                    "coverage_rate": 1.0,
                    "effective_coverage_rate": 1.0,
                    "innovation_rate": 0.0,
                    "surface_share": 0.0,
                    "surface_error_rate": 0.0,
                },
                "borrowing_summary": {
                    "missing": 0,
                    "surface": 0,
                    "effective": 1,
                    "innovative": 0,
                },
                "confidence": {
                    "E": 1.0,
                    "M": 0,
                    "M_components": {"anchor_boundary": 0.5, "score_boundary": 0.0, "lane_margin": 1.0},
                    "R": 0.0,
                    "level": "low",
                },
                "anchor_placement": {
                    "has_anchors": False,
                    "anchor_set_id": None,
                    "reference_bundle_id": None,
                    "reference_bundle_version": None,
                    "weights_version": None,
                    "lower_anchor": None,
                    "upper_anchor": None,
                    "anchor_band": None,
                    "boundary_clarity": 0.5,
                    "formula_conflict": False,
                },
                "review_status": "completed",
                "adaptation_diagnostic": {
                    "required_count": 0,
                    "successful_count": 0,
                    "partial_count": 0,
                    "failed_count": 0,
                    "pending_count": 0,
                    "unclear_count": 0,
                    "compensation_hit_rate": None,
                    "status": "aligned",
                    "summary": "No material creator-fit issue was identified.",
                },
                "why_not_higher": "The transition is less developed than the reference.",
                "why_not_lower": "The main problem hook and its function are present.",
                "next_actions": ["Make the transition into proof more explicit."],
            },
        }
        report["reference_bundle"]["content_hash"] = reference_bundle_hash(report["reference_bundle"])
        provenance = report["analysis"]["provenance"] = {
            "analysis_version": ANALYSIS_VERSION,
            "observation_method": "fixture_multimodal_review",
            "model_id": "fixture-model",
            "prompt_version": "fixture-prompt-1",
            "extraction_version": "fixture-extractor-1",
            "compiler_version": "twinclip-compiler-0.3",
            "reference_bundle_hash": report["reference_bundle"]["content_hash"],
            "media_preparation": {
                "manifest_schema_version": PREPARE_MANIFEST_SCHEMA_VERSION,
                "interval_seconds": 1.0,
                "max_frames": 300,
            },
            "source_hashes": {
                "breakdown_video": {
                    "path": str(self.breakdown.resolve()),
                    "sha256": sha256_file(self.breakdown),
                    "bytes": self.breakdown.stat().st_size,
                },
                "storyboard_pdf": {
                    "path": str(self.storyboard.resolve()),
                    "sha256": sha256_file(self.storyboard),
                    "bytes": self.storyboard.stat().st_size,
                },
                "creator_video": {
                    "path": creator,
                    "sha256": sha256_file(self.creator),
                    "bytes": self.creator.stat().st_size,
                },
            },
        }
        provenance["scoring_config_hash"] = sha256_bytes(canonical_json(report["scoring_config"]).encode("utf-8"))
        provenance["anchor_placement_hash"] = sha256_bytes(
            canonical_json(report["analysis"]["anchor_placement"]).encode("utf-8")
        )
        provenance["calibration_registry_sha256"] = None
        report["analysis"]["execution"] = {
            "run_id": "fixture-run",
            "execution_context_id": "fixture-context",
            "temperature": 0.0,
            "seed": 1,
            "task_ids": ["fixture-task"],
        }
        provenance["method_fingerprint"] = provenance_fingerprint(provenance)
        report["analysis"]["analysis_id"] = analysis_id(
            report["reference_bundle"]["content_hash"],
            provenance["source_hashes"]["creator_video"]["sha256"],
            provenance["method_fingerprint"],
        )
        return report

    def errors_for(self, mutate) -> list[str]:
        report = self.report()
        mutate(report)
        errors, _, _ = validate_report(report)
        return errors

    def test_valid_report(self) -> None:
        errors, warnings, metrics = validate_report(self.report())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, ["low-confidence report requires human review"])
        self.assertTrue(math.isclose(metrics["T"], 61.9366667, abs_tol=0.02))

    def test_final_report_requires_execution_identity(self) -> None:
        report = self.report()
        del report["analysis"]["execution"]
        errors, _, _ = validate_report(report)
        self.assertTrue(any("analysis.execution" in error for error in errors))

    def test_score_band_uses_deterministic_half_up_rounding(self) -> None:
        self.assertEqual(score_band(19.5), "表层模仿")
        self.assertEqual(score_band(39.5), "单点机制迁移")

    def test_locked_anchor_registry_is_checked(self) -> None:
        report = self.report()
        registry_path = Path(self.temp_dir.name) / "anchors.json"
        bundle_hash = report["reference_bundle"]["content_hash"]
        registry = {
            "schema_version": "1.1",
            "status": "locked",
            "anchor_set_id": "anchors-1",
            "reference_bundle_id": "bundle-1",
            "reference_bundle_version": "1.0",
            "reference_bundle_hash": bundle_hash,
            "weights_version": "1.0-anchor",
            "weights": {
                "l_weight": 0.7,
                "s_weight": 0.3,
                "s_weights": {
                    "logic": 0.35,
                    "function": 0.299,
                    "elements": 0.247,
                    "support": 0.104,
                },
            },
            "boundaries": [
                {
                    "lower_band": "单点机制迁移",
                    "upper_band": "二次创新",
                    "clarity": 1,
                }
            ],
            "anchors": [
                {
                    "id": "A-LOW",
                    "band": "单点机制迁移",
                    "T_center": 50,
                    "reference_bundle_id": "bundle-1",
                    "reference_bundle_version": "1.0",
                    "reference_bundle_hash": bundle_hash,
                },
                {
                    "id": "A-HIGH",
                    "band": "二次创新",
                    "T_center": 90,
                    "reference_bundle_id": "bundle-1",
                    "reference_bundle_version": "1.0",
                    "reference_bundle_hash": bundle_hash,
                },
            ],
        }
        registry["content_hash"] = registry_hash(registry)
        registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
        report["scoring_config"].update(
            {
                "weights_version": "1.0-anchor",
                "calibration_registry": str(registry_path),
            }
        )
        report["analysis"]["scores"].update(
            {"T_range": [52, 72], "provisional": False}
        )
        report["analysis"]["confidence"].update(
            {
                "M": 0,
                "M_components": {"anchor_boundary": 1.0, "score_boundary": 0.0, "lane_margin": 1.0},
                "level": "low",
            }
        )
        report["analysis"]["anchor_placement"] = {
            "has_anchors": True,
            "anchor_set_id": "anchors-1",
            "reference_bundle_id": "bundle-1",
            "reference_bundle_version": "1.0",
            "reference_bundle_hash": bundle_hash,
            "weights_version": "1.0-anchor",
            "lower_anchor": {
                "id": "A-LOW",
                "band": "单点机制迁移",
                "T_center": 50,
                "reference_bundle_id": "bundle-1",
                "reference_bundle_version": "1.0",
                "reference_bundle_hash": bundle_hash,
            },
            "upper_anchor": {
                "id": "A-HIGH",
                "band": "二次创新",
                "T_center": 90,
                "reference_bundle_id": "bundle-1",
                "reference_bundle_version": "1.0",
                "reference_bundle_hash": bundle_hash,
            },
            "anchor_band": "多点结构化迁移",
            "boundary_clarity": 1,
            "formula_conflict": False,
        }
        provenance = report["analysis"]["provenance"]
        provenance["scoring_config_hash"] = sha256_bytes(canonical_json(report["scoring_config"]).encode("utf-8"))
        provenance["anchor_placement_hash"] = sha256_bytes(
            canonical_json(report["analysis"]["anchor_placement"]).encode("utf-8")
        )
        provenance["calibration_registry_sha256"] = sha256_file(registry_path)
        provenance["method_fingerprint"] = provenance_fingerprint(provenance)
        report["analysis"]["analysis_id"] = analysis_id(
            report["reference_bundle"]["content_hash"],
            provenance["source_hashes"]["creator_video"]["sha256"],
            provenance["method_fingerprint"],
        )
        errors, warnings, _ = validate_report(report)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, ["low-confidence report requires human review"])

        report["scoring_config"]["l_weight"] = 0.6
        report["scoring_config"]["s_weight"] = 0.4
        errors, _, _ = validate_report(report)
        self.assertTrue(any("calibration registry weights.l_weight" in error for error in errors))

        report["analysis"]["scores"]["band"] = "表层模仿"
        report["analysis"]["anchor_placement"].update(
            {"anchor_band": "表层模仿", "formula_conflict": True}
        )
        errors, _, _ = validate_report(report)
        self.assertTrue(any("bracketed" in error for error in errors))

    def test_empty_evidence_is_rejected(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["evidence_records"][0].update(
                {"visual": "unknown", "transcript": "unknown", "observed_function": "unknown"}
            )
        )
        self.assertTrue(any("observed_function" in error or "not scoring-eligible" in error for error in errors))

    def test_evidence_requires_explicit_source_channels(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["evidence_records"][0].pop("source_channels")
        )
        self.assertTrue(any("source_channels" in error for error in errors))

    def test_ambiguous_logic_cannot_keep_a_positive_derived_score(self) -> None:
        def mutate(report: dict) -> None:
            report["analysis"]["logic_assessment"]["checklist"][0].update(
                {"evidence_clarity": "ambiguous", "manual_review": True}
            )

        errors = self.errors_for(mutate)
        self.assertTrue(any("score must be derived from its state" in error for error in errors))

    def test_rejected_blind_evidence_is_not_eligible(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["evidence_records"][0].update(
                {"human_confirmation": "rejected"}
            )
        )
        self.assertTrue(any("not scoring-eligible" in error for error in errors))

    def test_required_element_cannot_be_hidden_with_null(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["storyboard_node_assessments"][0].update(
                {"element_score": None}
            )
        )
        self.assertTrue(any("hide required elements" in error for error in errors))

    def test_full_video_scope_cannot_support_positive_node(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["evidence_records"][0].update(
                {
                    "evidence_scope": "full_video",
                    "storyboard_node_ids": ["SB01", "SB02"],
                }
            )
        )
        self.assertTrue(any("cannot use full_video evidence for a positive" in error for error in errors))

    def test_full_video_scope_must_span_creator_duration(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["evidence_records"][2].update(
                {"end_seconds": 1.5}
            )
        )
        self.assertTrue(any("must span the declared creator-video duration" in error for error in errors))

    def test_positive_logic_requires_segment_evidence(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["logic_assessment"]["checklist"][0].update(
                {"evidence_ids": ["EV-FULL"]}
            )
        )
        self.assertTrue(any("logic checklist hook_leads_need cannot use full_video" in error for error in errors))

    def test_multi_reference_primary_lane_is_validated(self) -> None:
        def mutate(report: dict) -> None:
            report["multi_reference"] = {
                "run_mode": "provisional_multi_reference",
                "primary_reference_lane": "REF-A",
                "selection_rule": "effective coverage, then T, then lane order",
                "storyboard_scope": "shared Storyboard",
                "lane_comparison": {
                    "REF-A": {
                        "label": "Reference A",
                        "L": 66.6666667,
                        "effective_coverage_rate": 1.0,
                        "coverage_rate": 1.0,
                        "S_storyboard": 50.9,
                        "T_center": 61.9366667,
                        "depths": [2],
                    },
                    "REF-B": {
                        "label": "Reference B",
                        "L": 0.0,
                        "effective_coverage_rate": 0.0,
                        "coverage_rate": 0.0,
                        "S_storyboard": 50.9,
                        "T_center": 15.27,
                        "depths": [0],
                    },
                },
                "selection_margin": {
                    "value": 1.0,
                    "basis": "effective_coverage_rate",
                    "threshold": 0.05,
                    "needs_manual_review": False,
                },
            }

        errors = self.errors_for(mutate)
        self.assertEqual(errors, [])

    def test_multi_reference_cannot_select_weaker_lane(self) -> None:
        def mutate(report: dict) -> None:
            report["multi_reference"] = {
                "run_mode": "provisional_multi_reference",
                "primary_reference_lane": "REF-B",
                "selection_rule": "effective coverage, then T, then lane order",
                "storyboard_scope": "shared Storyboard",
                "lane_comparison": {
                    "REF-A": {
                        "label": "Reference A",
                        "L": 66.6666667,
                        "effective_coverage_rate": 1.0,
                        "coverage_rate": 1.0,
                        "S_storyboard": 50.9,
                        "T_center": 61.9366667,
                        "depths": [2],
                    },
                    "REF-B": {
                        "label": "Reference B",
                        "L": 0.0,
                        "effective_coverage_rate": 0.0,
                        "coverage_rate": 0.0,
                        "S_storyboard": 0.0,
                        "T_center": 0.0,
                        "depths": [0],
                    },
                },
                "selection_margin": {
                    "value": 1.0,
                    "basis": "effective_coverage_rate",
                    "threshold": 0.05,
                    "needs_manual_review": False,
                },
            }

        errors = self.errors_for(mutate)
        self.assertTrue(any("must follow effective coverage" in error for error in errors))

    def test_multi_reference_exact_tie_requires_explicit_tie_breaker(self) -> None:
        def mutate(report: dict) -> None:
            report["multi_reference"] = {
                "run_mode": "provisional_multi_reference",
                "primary_reference_lane": "REF-A",
                "selection_rule": "effective coverage, then T, then lane order",
                "storyboard_scope": "shared Storyboard",
                "lane_comparison": {
                    "REF-A": {
                        "label": "Reference A",
                        "L": 66.6666667,
                        "effective_coverage_rate": 1.0,
                        "coverage_rate": 1.0,
                    "S_storyboard": 50.9,
                    "T_center": 61.9366667,
                        "depths": [2],
                    },
                    "REF-B": {
                        "label": "Reference B",
                        "L": 66.6666667,
                        "effective_coverage_rate": 1.0,
                        "coverage_rate": 1.0,
                    "S_storyboard": 50.9,
                    "T_center": 61.9366667,
                        "depths": [2],
                    },
                },
                "selection_margin": {
                    "value": 0.0,
                    "basis": "declared_lane_order",
                    "threshold": 0.05,
                    "needs_manual_review": True,
                },
            }

        errors = self.errors_for(mutate)
        self.assertTrue(any("selection_tie=true" in error for error in errors))

    def test_positive_node_requires_linked_segment_evidence(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["storyboard_node_assessments"][0].update(
                {"evidence_ids": ["EV02"]}
            )
        )
        self.assertTrue(any("segment evidence unrelated" in error for error in errors))

    def test_positive_relationship_requires_both_endpoint_segments(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["relationship_assessments"][0].update(
                {"evidence_ids": ["EV01"]}
            )
        )
        self.assertTrue(any("both endpoints" in error for error in errors))

    def test_shared_evidence_rejects_l_s_contradiction(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["storyboard_node_assessments"][0].update(
                {"function_score": 0, "element_score": 0, "support_score": 0, "absence_verified": True}
            )
        )
        self.assertTrue(any("claims adoption while every linked" in error for error in errors))

    def test_missing_anchor_metadata_is_rejected(self) -> None:
        def mutate(report: dict) -> None:
            report["analysis"]["anchor_placement"].update(
                {
                    "has_anchors": True,
                    "anchor_set_id": "fake",
                    "reference_bundle_id": "wrong",
                    "reference_bundle_version": "wrong",
                    "weights_version": "1.0-default",
                    "anchor_band": "未采纳",
                    "lower_anchor": None,
                    "upper_anchor": None,
                }
            )

        errors = self.errors_for(mutate)
        self.assertTrue(any("bind to the exact reference bundle" in error or "lower_anchor" in error for error in errors))

    def test_unanchored_report_cannot_change_t_weights(self) -> None:
        errors = self.errors_for(
            lambda report: report["scoring_config"].update({"l_weight": 1.0, "s_weight": 0.0})
        )
        self.assertTrue(any("default T weights" in error for error in errors))

    def test_unanchored_report_cannot_change_s_weights(self) -> None:
        errors = self.errors_for(
            lambda report: report["scoring_config"]["s_weights"].update({"logic": 0.5, "function": 0.2, "elements": 0.2, "support": 0.1})
        )
        self.assertTrue(any("default S weight" in error for error in errors))

    def test_interval_width_is_checked(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["scores"].update({"T_range": [0, 100]})
        )
        self.assertTrue(any("T_range must equal" in error for error in errors))

    def test_surface_metrics_use_distinct_denominators(self) -> None:
        report = self.report()
        report["reference_bundle"]["teaching_points"].append(
            {
                "id": "TP02",
                "source_type": "breakdown_explicit",
                "source_locator": "breakdown 00:01-00:02, teaching point 2",
                "stage": "Transition",
                "name": "Proof transition",
                "content_function": "Connect the problem to the proof",
                "core_meaning": "Move from the problem into the product proof",
                "persuasion_element": "problem-to-proof bridge",
                "evidence_method": "observable transition",
                "logical_role": "transition",
                "allowed_substitutions": ["another clear bridge into proof"],
                "minimum_evidence": ["the transition is observable"],
                "false_positive_guards": ["a generic cut does not count"],
                "source_ranges": [[1, 2]],
                "storyboard_node_ids": ["SB02"],
            }
        )
        report["reference_bundle"]["content_hash"] = reference_bundle_hash(report["reference_bundle"])
        provenance = report["analysis"]["provenance"]
        provenance["reference_bundle_hash"] = report["reference_bundle"]["content_hash"]
        provenance["method_fingerprint"] = provenance_fingerprint(provenance)
        report["analysis"]["analysis_id"] = analysis_id(
            report["reference_bundle"]["content_hash"],
            provenance["source_hashes"]["creator_video"]["sha256"],
            provenance["method_fingerprint"],
        )
        assessment = report["analysis"]["teaching_point_assessments"][0]
        assessment.update(
            {
                "depth": 1,
                "reason": "A related cue is present but the complete mechanism is not established",
                "missing_or_misused": "The observable proof is incomplete",
            }
        )
        report["analysis"]["teaching_point_assessments"].append(
            {
                "teaching_point_id": "TP02",
                "depth": 0,
                "evidence_ids": ["EV-FULL"],
                "reason": "The complete video scope does not show this transition",
                "missing_or_misused": "The transition is not present",
                "manual_review": False,
                "evidence_clarity": "clear",
                "absence_verified": True,
                "primary_failure_dimension": None,
                "failure_id": None,
                "adaptation_required": "no",
                "adaptation_result": "not_needed",
            }
        )
        report["analysis"]["scores"].update(
            {
                "L": 16.6666667,
                "S": 50.9,
                "T_center": 26.9366667,
                "T_range": [21, 33],
                "formula_band": "表层模仿",
                "band": "表层模仿",
            }
        )
        report["analysis"]["confidence"].update(
            {
                "M": 0.5,
                "M_components": {"anchor_boundary": 0.5, "score_boundary": 1.0, "lane_margin": 1.0},
                "level": "medium",
            }
        )
        report["analysis"]["coverage"].update(
            {
                "coverage_rate": 0.5,
                "effective_coverage_rate": 0.0,
                "innovation_rate": 0.0,
                "surface_share": 0.5,
                "surface_error_rate": 1.0,
            }
        )
        report["analysis"]["borrowing_summary"].update(
            {"missing": 1, "surface": 1, "effective": 0, "innovative": 0}
        )
        errors, _, _ = validate_report(report)
        self.assertEqual(errors, [])

        report["analysis"]["coverage"]["surface_share"] = 1.0
        errors, _, _ = validate_report(report)
        self.assertTrue(any("coverage.surface_share must equal 0.5000" in error for error in errors))

        report["analysis"]["coverage"]["surface_share"] = 0.5
        report["analysis"]["coverage"]["surface_error_rate"] = 0.5
        errors, _, _ = validate_report(report)
        self.assertTrue(any("coverage.surface_error_rate must equal 1.0000" in error for error in errors))

    def test_pending_guided_candidate_cannot_be_final(self) -> None:
        report = self.report()
        creator = str(self.creator.resolve())
        report["analysis"]["evidence_records"].append(
            {
                "id": "EV-PENDING",
                "creator_video": creator,
                "start_seconds": 0.2,
                "end_seconds": 0.4,
                "visual": "Subtle possible proof cue",
                "onscreen_text": "unknown",
                "transcript": "unknown",
                "observed_function": "Possible proof cue",
                "coverage_scope": "Candidate segment",
                "scope_complete": False,
                "evidence_scope": "segment",
                "storyboard_node_ids": ["SB02"],
                "observation_mode": "guided",
                "human_confirmation": "pending",
                "candidate_id": "C-PENDING",
            }
        )
        report["analysis"]["candidate_matches"].append(
            {
                "candidate_id": "C-PENDING",
                "teaching_point_id": "TP01",
                "evidence_ids": ["EV-PENDING"],
                "reason": "Subtle guided candidate",
                "status": "manual_pending",
            }
        )
        report["analysis"]["confidence"]["R"] = 1 / 6
        errors, _, _ = validate_report(report)
        self.assertTrue(any("cannot be completed" in error or "must be completed" in error for error in errors))

    def test_pending_review_status_requires_draft_mode(self) -> None:
        report = self.report()
        report["analysis"]["review_status"] = "pending"
        errors, _, _ = validate_report(report)
        self.assertTrue(any("requires --allow-draft" in error for error in errors))

    def test_source_content_hash_is_required_for_auditability(self) -> None:
        errors = self.errors_for(
            lambda report: report["analysis"]["provenance"]["source_hashes"]["creator_video"].update(
                {"sha256": "0" * 64}
            )
        )
        self.assertTrue(any("does not match the current file content" in error for error in errors))

    def test_reference_graph_binds_source_content(self) -> None:
        report = self.report()
        self.storyboard.write_bytes(b"changed storyboard")
        errors, _, _ = validate_report(report)
        self.assertTrue(any("source_content_hashes.storyboard_pdf.sha256" in error for error in errors))

    def test_logic_mean_remains_fractional(self) -> None:
        report = self.report()
        reference = report["reference_bundle"]
        reference["relationships"].append(
            {
                "id": "REL02",
                "type": "claim_proof",
                "from_node_ids": ["SB01"],
                "to_node_ids": ["SB02"],
                "teaching_point_ids": ["TP01"],
                "description": "The proof supports the product claim",
            }
        )
        reference["content_hash"] = reference_bundle_hash(reference)
        provenance = report["analysis"]["provenance"]
        provenance["reference_bundle_hash"] = reference["content_hash"]
        provenance["method_fingerprint"] = provenance_fingerprint(provenance)
        report["analysis"]["analysis_id"] = analysis_id(
            reference["content_hash"],
            provenance["source_hashes"]["creator_video"]["sha256"],
            provenance["method_fingerprint"],
        )
        report["analysis"]["logic_assessment"].update(
            {"score": 2.4, "checklist_mean": 0.8, "relationship_ids": ["REL01", "REL02"]}
        )
        report["analysis"]["logic_assessment"]["checklist"][3].update(
            {"state": "met", "score": 1, "evidence_ids": ["EV02"], "absence_verified": False}
        )
        report["analysis"]["S_components"]["logic_coherence"] = 80.0
        report["analysis"]["relationship_assessments"].append(
            {
                "relationship_id": "REL02",
                "score": 3,
                "evidence_ids": ["EV01", "EV02"],
                "reason": "The proof supports the claim clearly",
                "manual_review": False,
                "evidence_clarity": "clear",
                "absence_verified": False,
                "primary_failure_dimension": None,
                "failure_id": None,
            }
        )
        report["analysis"]["scores"].update(
            {"S": 57.9, "T_center": 64.0366667, "T_range": [58, 70]}
        )
        report["analysis"]["confidence"].update(
            {
                "M": 0.5,
                "M_components": {"anchor_boundary": 0.5, "score_boundary": 0.5, "lane_margin": 1.0},
                "level": "medium",
            }
        )
        errors, _, metrics = validate_report(report)
        self.assertEqual(errors, [])
        self.assertTrue(math.isclose(metrics["S"], 57.9, abs_tol=0.02))

    def test_missing_relationships_do_not_disable_logic_checklist(self) -> None:
        report = self.report()
        report["reference_bundle"]["relationships"] = []
        report["reference_bundle"]["content_hash"] = reference_bundle_hash(report["reference_bundle"])
        provenance = report["analysis"]["provenance"]
        provenance["reference_bundle_hash"] = report["reference_bundle"]["content_hash"]
        provenance["method_fingerprint"] = provenance_fingerprint(provenance)
        report["analysis"]["analysis_id"] = analysis_id(
            report["reference_bundle"]["content_hash"],
            provenance["source_hashes"]["creator_video"]["sha256"],
            provenance["method_fingerprint"],
        )
        provenance["scoring_config_hash"] = sha256_bytes(canonical_json(report["scoring_config"]).encode("utf-8"))
        provenance["method_fingerprint"] = provenance_fingerprint(provenance)
        report["analysis"]["analysis_id"] = analysis_id(
            report["reference_bundle"]["content_hash"],
            provenance["source_hashes"]["creator_video"]["sha256"],
            provenance["method_fingerprint"],
        )
        report["analysis"]["logic_assessment"].update(
            {
                "relationship_ids": [],
            }
        )
        report["analysis"]["relationship_assessments"] = []
        errors, _, _ = validate_report(report)
        self.assertEqual(errors, [])

    def test_adaptation_status_is_derived_from_counts(self) -> None:
        def mutate(report: dict) -> None:
            assessment = report["analysis"]["teaching_point_assessments"][0]
            assessment.update({"adaptation_required": "yes", "adaptation_result": "failed"})
            report["analysis"]["adaptation_diagnostic"].update(
                {"required_count": 1, "failed_count": 1, "status": "aligned"}
            )

        errors = self.errors_for(mutate)
        self.assertTrue(any("status must equal mismatch" in error for error in errors))

    def test_malformed_id_returns_errors_instead_of_crashing(self) -> None:
        errors = self.errors_for(
            lambda report: report["reference_bundle"]["relationships"][0]["from_node_ids"].append({})
        )
        self.assertTrue(errors)

    def test_malformed_anchor_shape_returns_errors_instead_of_crashing(self) -> None:
        report = self.report()
        report["analysis"]["anchor_placement"].update(
            {
                "has_anchors": True,
                "anchor_set_id": "broken",
                "reference_bundle_id": "bundle-1",
                "reference_bundle_version": "1.0",
                "reference_bundle_hash": report["reference_bundle"]["content_hash"],
                "weights_version": "1.0-default",
                "lower_anchor": "not-an-object",
                "upper_anchor": {"id": "A", "band": "二次创新", "T_center": 90},
                "anchor_band": "多点结构化迁移",
                "boundary_clarity": 1,
                "formula_conflict": False,
            }
        )
        errors, _, _ = validate_report(report)
        self.assertTrue(errors)

    def test_guided_evidence_cannot_cross_teaching_points(self) -> None:
        def mutate(report: dict) -> None:
            creator = str(self.creator.resolve())
            report["analysis"]["evidence_records"].append(
                {
                    "id": "EV03",
                    "creator_video": creator,
                    "start_seconds": 1.0,
                    "end_seconds": 1.2,
                    "visual": "Candidate transition",
                    "onscreen_text": "unknown",
                    "transcript": "unknown",
                    "observed_function": "Candidate transition",
                    "coverage_scope": "Transition",
                    "scope_complete": False,
                    "evidence_scope": "segment",
                    "storyboard_node_ids": ["SB02"],
                    "observation_mode": "guided",
                    "human_confirmation": "confirmed",
                    "candidate_id": "C01",
                }
            )
            report["analysis"]["candidate_matches"].append(
                {
                    "candidate_id": "C01",
                    "teaching_point_id": "TP01",
                    "evidence_ids": ["EV03"],
                    "reason": "Guided candidate",
                    "status": "confirmed",
                }
            )
            report["analysis"]["storyboard_node_assessments"][1]["evidence_ids"] = ["EV03"]

        errors = self.errors_for(mutate)
        self.assertTrue(any("unrelated teaching point" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
