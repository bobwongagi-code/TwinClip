#!/usr/bin/env python3
"""Tests for auditable QA population selection."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "twinclip" / "scripts" / "select_qa_sample.py"


class SelectQASampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.batch_one = self.root / "batch-one.json"
        self.batch_two = self.root / "batch-two.json"
        self.output = self.root / "sample.json"

        def batch(indexes: range) -> dict:
            return {
                "schema_version": "1.6",
                "reference_bundle_content_hash": "bundle-hash",
                "method_fingerprint": "method-hash",
                "creator_video_count": len(indexes),
                "reports": [
                    {
                        "analysis_id": f"analysis-{index}",
                        "creator_video": f"creator-{index}.mp4",
                        "band": "多点结构化迁移",
                        "has_anchors": False,
                        "review_status": "completed",
                    }
                    for index in indexes
                ],
            }

        self.batch_one.write_text(json.dumps(batch(range(10))), encoding="utf-8")
        self.batch_two.write_text(json.dumps(batch(range(10, 20))), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_selects_five_and_records_population(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--batch-json",
                str(self.batch_one),
                "--batch-json",
                str(self.batch_two),
                "--output",
                str(self.output),
                "--population-size",
                "20",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        sample = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(len(sample["reports"]), 5)
        self.assertEqual(sample["qa_sample"]["population_size"], 20)
        self.assertEqual(len(sample["qa_sample"]["population_analysis_ids"]), 20)
        self.assertEqual(sample["qa_sample"]["selected_analysis_ids"], [
            report["analysis_id"] for report in sample["reports"]
        ])


if __name__ == "__main__":
    unittest.main()
