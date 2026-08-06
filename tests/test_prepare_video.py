#!/usr/bin/env python3
"""Smoke tests for bounded media preparation."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "twinclip" / "scripts" / "prepare_video.py"


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
class PrepareVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.video = self.root / "sample.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=320x240:r=2:d=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(self.video),
            ],
            check=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_prepare(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.video), str(self.root / "prepared"), *extra],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_manifest_uses_actual_frame_timestamps_and_marks_unknown_channels(self) -> None:
        result = self.run_prepare("--interval", "0.5", "--max-frames", "10")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.root / "prepared" / "manifest.json").read_text())
        timestamps = [frame["time_seconds"] for frame in manifest["frames"]]
        self.assertEqual(timestamps, [0.0, 0.5, 1.0, 1.5])
        self.assertEqual(manifest["duration_seconds"], 2.0)
        self.assertEqual(manifest["transcript_status"], "unknown")
        self.assertEqual(manifest["ocr_status"], "not_run")

    def test_nan_interval_is_rejected_before_ffmpeg(self) -> None:
        result = self.run_prepare("--interval", "nan")
        self.assertEqual(result.returncode, 2)
        self.assertIn("finite", result.stderr)

    def test_local_hls_playlist_is_rejected(self) -> None:
        playlist = self.root / "input.m3u8"
        playlist.write_text("#EXTM3U\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(playlist), str(self.root / "prepared")],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("HLS", result.stderr)


if __name__ == "__main__":
    unittest.main()
