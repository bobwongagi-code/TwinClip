#!/usr/bin/env python3
"""Tests for repeated-run distribution analysis."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "twinclip" / "scripts"
SCRIPT = SCRIPT_DIR / "stability_report.py"
sys.path.insert(0, str(SCRIPT_DIR))
from stability_report import (  # noqa: E402
    COMPILER_VERSION,
    judgment_fingerprint,
    normalize_run,
    numeric_summary,
    quality_summary,
    selected_evidence_ids,
)


class StabilityReportTests(unittest.TestCase):
    def test_numeric_summary_keeps_distribution_metrics(self) -> None:
        summary = numeric_summary([0, 2, 4])
        self.assertEqual(summary["range"], 4.0)
        self.assertEqual(summary["median"], 2.0)
        self.assertAlmostEqual(summary["mean_abs_deviation_from_mean"], 1.333333, places=5)
        self.assertAlmostEqual(summary["mean_pairwise_abs_difference"], 2.666667, places=5)

    def test_selected_evidence_excludes_full_fixed_inventory(self) -> None:
        result = {
            "evidence_records": [{"id": "EV-01"}, {"id": "EV-02"}],
            "teaching_points": [{"id": "TP-01", "evidence_ids": ["EV-02"]}],
            "logic_assessment": {
                "evidence_ids": ["EV-01"],
                "checklist": [{"check_id": "claims_supported", "evidence_ids": ["EV-01"]}],
            },
        }
        self.assertEqual(selected_evidence_ids(result), {"EV-01", "EV-02"})

    def test_quality_summary_exposes_unknown_channel_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = root / "fixed-evidence.json"
            snapshot.write_text(json.dumps({
                "evidence_records": [
                    {"transcript": "unknown", "onscreen_text": "caption"},
                    {"transcript": "spoken", "onscreen_text": "unknown"},
                ]
            }), encoding="utf-8")
            result = quality_summary({"videos": [{"fixed_evidence_path": str(snapshot)}]})
        self.assertEqual(result["evidence_records"], 2)
        self.assertEqual(result["channel_slot_count"], 4)
        self.assertEqual(result["unknown_channel_count"], 2)
        self.assertEqual(result["unknown_channel_rate"], 0.5)
        self.assertEqual(result["records_with_unknown_channel"], 2)
        self.assertEqual(result["record_unknown_channel_rate"], 1.0)

    def test_judgment_fingerprint_is_sensitive_to_judgment_fields(self) -> None:
        base = {"scores": {"L": 20, "S": 20, "T_center": 20}, "primary_lane": "REF-A"}
        changed = {"scores": {"L": 21, "S": 20, "T_center": 20}, "primary_lane": "REF-A"}
        self.assertEqual(judgment_fingerprint(base), judgment_fingerprint(base))
        self.assertNotEqual(judgment_fingerprint(base), judgment_fingerprint(changed))

    def test_formal_run_requires_nested_identity_and_fixed_evidence_binding(self) -> None:
        manifest = {"reference_bundle": {"content_hash": "ref-hash"}, "experiment_id": "exp-1"}
        planned = {
            "run_id": "r01-v01",
            "experiment_id": "exp-1",
            "video_id": "v01",
            "replicate_index": 1,
            "round": 1,
            "execution_context_id": "ctx-1",
            "fixed_evidence_hash": "e" * 64,
        }
        raw = {
            "schema_version": "twinclip-stability-run-0.3",
            "compiler_version": COMPILER_VERSION,
            "reference_bundle_hash": "ref-hash",
            "scores": {"L": 20, "S": 20, "T_center": 20, "band": "表层模仿"},
            "primary_lane": "DEFAULT",
            "lane_comparison": {"DEFAULT": {"L": 20, "S": 20, "T_center": 20, "effective_coverage_rate": 1.0}},
            "run": {"run_id": "r01-v01", "experiment_id": "exp-1", "video_id": "v01", "round": 1, "replicate_index": 1,
                    "execution_context_id": "ctx-1", "fixed_evidence_hash": "e" * 64},
        }
        normalized = normalize_run(manifest, planned, raw, Path("r01-v01.json"))
        self.assertEqual(normalized["fixed_evidence_hash"], "e" * 64)
        del raw["run"]["execution_context_id"]
        with self.assertRaisesRegex(ValueError, "missing nested run identity"):
            normalize_run(manifest, planned, raw, Path("r01-v01.json"))

    def test_legacy_run_can_be_profiled_without_formal_identity(self) -> None:
        manifest = {"reference_bundle": {}, "experiment_id": "exp-1"}
        planned = {
            "run_id": "r01-v01",
            "experiment_id": "exp-1",
            "video_id": "v01",
            "replicate_index": 1,
            "round": 1,
            "fixed_evidence_hash": "e" * 64,
        }
        raw = {
            "schema_version": "twinclip-stability-run-0.1",
            "run": {},
            "primary_lane": "REF-B",
            "lane_comparison": {
                "REF-A": {"L": 50, "S": 50, "T_center": 60, "effective_coverage_rate": 0.5},
                "REF-B": {"L": 50, "S": 50, "T_center": 50, "effective_coverage_rate": 0.5},
            },
            "scores": {"L": 50, "S": 50, "T_center": 50, "band": "单点机制迁移"},
        }
        normalized = normalize_run(manifest, planned, raw, Path("legacy.json"))
        self.assertEqual(normalized["primary_lane"], "REF-B")
        self.assertEqual(normalized["primary_lane_margin"], 0.1)
        self.assertEqual(normalized["lane_selection"]["basis"], "T_center")
        self.assertFalse(normalized["lane_selection"]["observed_lane_matches_deterministic"])

    def test_formula_residual_uses_manifest_weights(self) -> None:
        manifest = {
            "reference_bundle": {},
            "experiment_id": "exp-1",
            "method": {"l_weight": 0.6, "s_weight": 0.4},
        }
        planned = {"run_id": "r01-v01", "video_id": "v01", "replicate_index": 1, "round": 1}
        raw = {
            "scores": {"L": 50, "S": 25, "T_center": 40, "band": "单点机制迁移"},
            "primary_lane": "DEFAULT",
            "lane_comparison": {"DEFAULT": {"L": 50, "S": 25, "T_center": 40, "effective_coverage_rate": 1.0}},
        }
        normalized = normalize_run(manifest, planned, raw, Path("legacy.json"))
        self.assertEqual(normalized["formula_residual"], 0.0)

    def test_cli_requires_all_planned_runs_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "results"
            results.mkdir()
            manifest = {
                "experiment_id": "exp-1",
                "video_count": 1,
                "replicates": 2,
                "runs": [
                    {"run_id": "r01-v01", "experiment_id": "exp-1", "video_id": "v01", "replicate_index": 1, "round": 1,
                     "execution_context_id": "ctx-r01-v01", "fixed_evidence_hash": "1" * 64},
                    {"run_id": "r02-v01", "experiment_id": "exp-1", "video_id": "v01", "replicate_index": 2, "round": 2,
                     "execution_context_id": "ctx-r02-v01", "fixed_evidence_hash": "1" * 64},
                ],
                "fixed_observation": {"mode": "fixed"},
                "method": {"score_formula": "T=0.70*L+0.30*S", "compiler_version": COMPILER_VERSION},
                "videos": [{"video_id": "v01", "fixed_evidence_hash": "1" * 64}],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(root / "manifest.json"),
                 "--results-dir", str(results), "--output-json", str(root / "report.json"),
                 "--output-md", str(root / "report.md"), "--strict"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing result", result.stderr)

    def test_strict_mode_rejects_legacy_plan_without_execution_identity(self) -> None:
        manifest = {
            "experiment_id": "exp-1",
            "runs": [{"run_id": "r01-v01", "video_id": "v01", "replicate_index": 1, "round": 1}],
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "results"
            results.mkdir()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(manifest_path), "--results-dir", str(results),
                 "--output-json", str(root / "report.json"), "--output-md", str(root / "report.md"), "--strict"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing immutable identity", result.stderr)

    def test_strict_mode_rejects_reused_execution_context(self) -> None:
        manifest = {
            "experiment_id": "exp-1",
            "method": {"compiler_version": COMPILER_VERSION},
            "videos": [{"video_id": "v01", "fixed_evidence_hash": "1" * 64}],
            "runs": [
                {"run_id": "r01-v01", "experiment_id": "exp-1", "video_id": "v01", "replicate_index": 1,
                 "round": 1, "execution_context_id": "same-context", "fixed_evidence_hash": "1" * 64},
                {"run_id": "r02-v01", "experiment_id": "exp-1", "video_id": "v01", "replicate_index": 2,
                 "round": 2, "execution_context_id": "same-context", "fixed_evidence_hash": "1" * 64},
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "results"
            results.mkdir()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(manifest_path), "--results-dir", str(results),
                 "--output-json", str(root / "report.json"), "--output-md", str(root / "report.md"), "--strict"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reuses execution_context_id", result.stderr)

    def test_cli_emits_raw_distributions_without_averaging_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "results"
            results.mkdir()
            manifest = {
                "experiment_id": "exp-1",
                "video_count": 1,
                "replicates": 3,
                "runs": [
                    {"run_id": f"r0{index}-v01", "experiment_id": "exp-1", "video_id": "v01", "replicate_index": index,
                     "round": index, "execution_context_id": f"ctx-r0{index}-v01", "fixed_evidence_hash": "1" * 64}
                    for index in range(1, 4)
                ],
                "fixed_observation": {"mode": "fixed"},
                "method": {"score_formula": "T=0.70*L+0.30*S", "compiler_version": COMPILER_VERSION},
                "videos": [{"video_id": "v01", "fixed_evidence_hash": "1" * 64}],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            for index, t in enumerate((18, 22, 31), start=1):
                (results / f"r0{index}-v01.json").write_text(json.dumps({
                    "schema_version": "twinclip-stability-run-0.3",
                    "compiler_version": COMPILER_VERSION,
                    "run": {"run_id": f"r0{index}-v01", "experiment_id": "exp-1", "video_id": "v01",
                            "round": index, "replicate_index": index,
                            "execution_context_id": f"ctx-r0{index}-v01", "fixed_evidence_hash": "1" * 64},
                    "run_id": f"r0{index}-v01",
                    "video_id": "v01",
                    "scores": {"L": t, "S": t, "T_center": t, "band": "未采纳" if t < 20 else "表层模仿"},
                    "primary_lane": "DEFAULT",
                    "lane_comparison": {"DEFAULT": {"L": t, "S": t, "T_center": t, "effective_coverage_rate": 1.0}},
                    "teaching_points": [], "storyboard_nodes": [], "relationships": [],
                }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(root / "manifest.json"),
                 "--results-dir", str(results), "--output-json", str(root / "report.json"),
                 "--output-md", str(root / "report.md"), "--strict"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["coverage"]["runs"], 3)
            self.assertEqual(report["global_distributions"]["T_center"]["range"], 13.0)
            self.assertEqual(report["global_distributions"]["T_center"]["median"], 22.0)
            self.assertIn("No final score is obtained by averaging", report["interpretation_rule"])
            self.assertEqual(len(report["raw_runs"]), 3)


if __name__ == "__main__":
    unittest.main()
