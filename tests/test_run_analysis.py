#!/usr/bin/env python3
"""Integration test for the final and batch output contract."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_validate_report


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

    def write_draft(self, creator: Path, name: str) -> Path:
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
            provenance["reference_bundle_hash"] = report["reference_bundle"]["content_hash"]
            provenance["method_fingerprint"] = test_validate_report.provenance_fingerprint(provenance)
            report["analysis"]["analysis_id"] = test_validate_report.analysis_id(
                report["reference_bundle"]["content_hash"],
                provenance["source_hashes"]["creator_video"]["sha256"],
                provenance["method_fingerprint"],
            )
            path = self.root / name
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return path
        finally:
            fixture.tearDown()

    def test_run_analysis_writes_valid_reports_and_batch_metrics(self) -> None:
        draft_one = self.write_draft(self.creator_one, "draft-one.json")
        draft_two = self.write_draft(self.creator_two, "draft-two.json")
        output_dir = self.root / "run"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--breakdown-video",
                str(self.breakdown),
                "--storyboard-pdf",
                str(self.storyboard),
                "--creator-video",
                str(self.creator_one),
                "--draft-report",
                str(draft_one),
                "--creator-video",
                str(self.creator_two),
                "--draft-report",
                str(draft_two),
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

    def test_run_analysis_rejects_semantically_different_same_version_bundle(self) -> None:
        draft_one = self.write_draft(self.creator_one, "draft-one.json")
        draft_two = self.write_draft(self.creator_two, "draft-two.json")
        changed = json.loads(draft_two.read_text(encoding="utf-8"))
        changed["reference_bundle"]["teaching_points"][0]["core_meaning"] = "A different persuasion meaning"
        draft_two.write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--breakdown-video",
                str(self.breakdown),
                "--storyboard-pdf",
                str(self.storyboard),
                "--creator-video",
                str(self.creator_one),
                "--draft-report",
                str(draft_one),
                "--creator-video",
                str(self.creator_two),
                "--draft-report",
                str(draft_two),
                "--output-dir",
                str(self.root / "run-drift"),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("content_hash", result.stderr)


if __name__ == "__main__":
    unittest.main()
