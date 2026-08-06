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
        self.batch.write_text(
            json.dumps(
                {
                    "reports": [
                        {"creator_video": f"creator-{index}.mp4", "band": "多点结构化迁移"}
                        for index in range(5)
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.expected.write_text(json.dumps(["多点结构化迁移"] * 5), encoding="utf-8")

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
        for _ in range(3):
            result = self.run_check()
            self.assertEqual(result.returncode, 0, result.stderr)
        history = json.loads(self.history.read_text(encoding="utf-8"))
        self.assertEqual(history["next_sample_cadence"], "5 per 50 non-anchor analyses")

    def test_two_band_error_fails_sample(self) -> None:
        self.expected.write_text(json.dumps(["未采纳"] + ["多点结构化迁移"] * 4), encoding="utf-8")
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        history = json.loads(self.history.read_text(encoding="utf-8"))
        self.assertEqual(history["latest"]["two_band_errors"], 1)


if __name__ == "__main__":
    unittest.main()
