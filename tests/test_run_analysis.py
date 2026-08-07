#!/usr/bin/env python3
"""Integration test for the final and batch output contract."""

from __future__ import annotations

import json
import copy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_validate_report
from semantic_test_helpers import write_semantic_run
from run_analysis import build_batch_summary


SCRIPT = Path(__file__).resolve().parents[1] / "twinclip" / "scripts" / "run_analysis.py"


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
class RunAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.breakdown = self.root / "breakdown.mp4"
        self.creator_one = self.root / "creator-01.mp4"
        self.creator_two = self.root / "creator-02.mp4"
        self.storyboard = self.root / "storyboard.pdf"
        self.storyboard.write_bytes(b"pdf fixture")
        for target in (self.breakdown, self.creator_one):
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=160x120:r=2:d=2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(target),
                ],
                check=True,
            )
        shutil.copyfile(self.creator_one, self.creator_two)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_semantic(self, creator: Path, name: str) -> tuple[Path, Path]:
        fixture = test_validate_report.ReportFixture(methodName="runTest")
        fixture.setUp()
        try:
            report = fixture.report()
            report["reference_bundle"]["source_inputs"] = {
                "breakdown_video": str(self.breakdown),
                "storyboard_pdf": str(self.storyboard),
            }
            report["reference_bundle"]["source_content_hashes"] = {
                "breakdown_video": {
                    "sha256": test_validate_report.sha256_file(self.breakdown),
                    "bytes": self.breakdown.stat().st_size,
                },
                "storyboard_pdf": {
                    "sha256": test_validate_report.sha256_file(self.storyboard),
                    "bytes": self.storyboard.stat().st_size,
                },
            }
            report["reference_bundle"]["content_hash"] = test_validate_report.reference_bundle_hash(
                report["reference_bundle"]
            )
            report["analysis"]["creator_videos"] = [str(creator)]
            report["analysis"]["media_durations"] = {str(creator): 2.0}
            for evidence in report["analysis"]["evidence_records"]:
                evidence["creator_video"] = str(creator)
            provenance = report["analysis"]["provenance"]
            provenance["source_hashes"]["breakdown_video"] = {
                "path": str(self.breakdown.resolve()),
                "sha256": test_validate_report.sha256_file(self.breakdown),
                "bytes": self.breakdown.stat().st_size,
            }
            provenance["source_hashes"]["storyboard_pdf"] = {
                "path": str(self.storyboard.resolve()),
                "sha256": test_validate_report.sha256_file(self.storyboard),
                "bytes": self.storyboard.stat().st_size,
            }
            provenance["source_hashes"]["creator_video"] = {
                "path": str(creator.resolve()),
                "sha256": test_validate_report.sha256_file(creator),
                "bytes": creator.stat().st_size,
            }
            provenance["reference_bundle_hash"] = report["reference_bundle"]["content_hash"]
            provenance["method_fingerprint"] = test_validate_report.provenance_fingerprint(provenance)
            report["analysis"]["analysis_id"] = test_validate_report.analysis_id(
                report["reference_bundle"]["content_hash"],
                provenance["source_hashes"]["creator_video"]["sha256"],
                provenance["method_fingerprint"],
            )
            reference_path = self.root / "reference-bundle.json"
            if not reference_path.exists():
                reference_path.write_text(
                    json.dumps(report["reference_bundle"], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            return write_semantic_run(report, creator, self.root, name, reference_path), reference_path
        finally:
            fixture.tearDown()

    def test_run_analysis_writes_valid_reports_and_batch_metrics(self) -> None:
        semantic_one, reference_path = self.write_semantic(self.creator_one, "semantic-one")
        semantic_two, _ = self.write_semantic(self.creator_two, "semantic-two")
        output_dir = self.root / "run"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--breakdown-video",
                str(self.breakdown),
                "--storyboard-pdf",
                str(self.storyboard),
                "--reference-bundle",
                str(reference_path),
                "--creator-video",
                str(self.creator_one),
                "--semantic-dir",
                str(semantic_one),
                "--creator-video",
                str(self.creator_two),
                "--semantic-dir",
                str(semantic_two),
                "--output-dir",
                str(output_dir),
                "--interval",
                "0.5",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((output_dir / "reports" / "creator_001.json").is_file())
        self.assertTrue((output_dir / "reports" / "creator_002.json").is_file())
        batch = json.loads((output_dir / "batch.json").read_text(encoding="utf-8"))
        self.assertEqual(batch["creator_video_count"], 2)
        self.assertEqual(batch["teaching_points"][0]["adoption_rate"], 1.0)
        self.assertEqual(batch["teaching_points"][0]["per_creator"][0]["first_appearance_seconds"], 0.1)
        self.assertEqual(batch["reports"][0]["surface_share"], 0.0)
        self.assertEqual(batch["reports"][0]["surface_error_rate"], 0.0)
        compiled = json.loads((output_dir / "reports" / "creator_001.json").read_text(encoding="utf-8"))
        self.assertEqual(compiled["analysis"]["provenance"]["compiler_version"], "twinclip-compiler-0.2")

    def test_run_analysis_rejects_task_identity_drift(self) -> None:
        semantic_one, reference_path = self.write_semantic(self.creator_one, "semantic-one")
        semantic_two, _ = self.write_semantic(self.creator_two, "semantic-two")
        changed_path = semantic_two / "tasks" / "teaching-DEFAULT.json"
        changed = json.loads(changed_path.read_text(encoding="utf-8"))
        changed["run"]["reference_bundle_hash"] = "0" * 64
        changed_path.write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--breakdown-video",
                str(self.breakdown),
                "--storyboard-pdf",
                str(self.storyboard),
                "--reference-bundle",
                str(reference_path),
                "--creator-video",
                str(self.creator_one),
                "--semantic-dir",
                str(semantic_one),
                "--creator-video",
                str(self.creator_two),
                "--semantic-dir",
                str(semantic_two),
                "--output-dir",
                str(self.root / "run-drift"),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match", result.stderr)

    def test_batch_summary_groups_selected_reference_lanes(self) -> None:
        fixture = test_validate_report.ReportFixture(methodName="runTest")
        fixture.setUp()
        try:
            report_a = fixture.report()
            report_b = copy.deepcopy(report_a)
            report_a["multi_reference"] = {"primary_reference_lane": "REF-A"}
            report_b["multi_reference"] = {"primary_reference_lane": "REF-B"}
            report_a["analysis"]["teaching_point_assessments"][0]["teaching_point_id"] = "A-TP01"
            report_b["analysis"]["teaching_point_assessments"][0]["teaching_point_id"] = "B-TP01"
            batch = build_batch_summary(
                [
                    ("creator-a.mp4", self.root / "reports" / "creator-a.json", report_a),
                    ("creator-b.mp4", self.root / "reports" / "creator-b.json", report_b),
                ],
                self.root,
            )
            self.assertEqual(set(batch["teaching_points_by_reference_lane"]), {"REF-A", "REF-B"})
            self.assertEqual(batch["teaching_points_by_reference_lane"]["REF-A"][0]["per_creator"][0]["creator_video"], "creator-a.mp4")
            self.assertEqual(batch["teaching_points_by_reference_lane"]["REF-B"][0]["per_creator"][0]["creator_video"], "creator-b.mp4")
            self.assertEqual([item["primary_reference_lane"] for item in batch["reports"]], ["REF-A", "REF-B"])
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
