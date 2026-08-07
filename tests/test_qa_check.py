#!/usr/bin/env python3
"""Tests for band-only continuous QA monitoring."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "twinclip" / "scripts" / "qa_check.py"


class QACheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.batch = self.root / "batch.json"
        self.expected = self.root / "expected.json"
        self.history = self.root / "history.json"
        self.write_batch(0)

    def write_batch(self, population_start: int) -> None:
        reports = [
            {
                "analysis_id": f"analysis-{population_start + index}",
                "creator_video": f"creator-{population_start + index}.mp4",
                "band": "多点结构化迁移",
                "has_anchors": False,
                "review_status": "completed",
            }
            for index in range(5)
        ]
        population_ids = [f"analysis-{population_start + index}" for index in range(20)]
        self.batch.write_text(
            json.dumps(
                {
                    "schema_version": "1.4",
                    "reference_bundle_content_hash": "bundle-hash",
                    "method_fingerprint": "method-hash",
                    "creator_video_count": 5,
                    "reports": reports,
                    "qa_sample": {
                        "selection_method": "system_random",
                        "population_size": 20,
                        "population_analysis_ids": population_ids,
                        "selected_analysis_ids": [report["analysis_id"] for report in reports],
                    },
                }
            ),
            encoding="utf-8",
        )
        self.expected.write_text(
            json.dumps({report["analysis_id"]: "多点结构化迁移" for report in reports}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_check(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--batch-json",
                str(self.batch),
                "--expected-bands",
                str(self.expected),
                "--history",
                str(self.history),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_three_passing_samples_switch_to_fifty_cadence(self) -> None:
        for round_index in range(3):
            result = self.run_check()
            self.assertEqual(result.returncode, 0, result.stderr)
            if round_index < 2:
                self.write_batch((round_index + 1) * 20)
        history = json.loads(self.history.read_text(encoding="utf-8"))
        self.assertEqual(history["next_sample_cadence"], "5 per 50 non-anchor analyses")

    def test_two_band_error_fails_sample(self) -> None:
        self.expected.write_text(json.dumps(["未采纳"] + ["多点结构化迁移"] * 4), encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        history = json.loads(self.history.read_text(encoding="utf-8"))
        self.assertEqual(history["latest"]["two_band_errors"], 1)

    def test_population_cannot_be_counted_twice(self) -> None:
        first = self.run_check()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_check()
        self.assertEqual(second.returncode, 2)
        self.assertIn("reuses analyses", second.stderr)


if __name__ == "__main__":
    unittest.main()
