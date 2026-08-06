#!/usr/bin/env python3
"""Prepare a local video for TwinClip timeline inspection.

This helper intentionally does not perform ASR or OCR. It produces bounded,
timestamped media artifacts and marks unavailable channels as unknown so the
analysis layer cannot silently infer them.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from contracts import PREPARE_MANIFEST_SCHEMA_VERSION, sha256_file  # noqa: E402


COMMAND_TIMEOUT_SECONDS = 180
MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024
MAX_DEFAULT_DURATION_SECONDS = 30 * 60
MAX_DEFAULT_OUTPUT_BYTES = 512 * 1024 * 1024
PROTOCOL_WHITELIST = "file"
SHOWINFO_TIME = re.compile(r"showinfo.*?pts_time:([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")


def run(command: list[str], *, timeout: int = COMMAND_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"command timed out after {timeout}s: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise RuntimeError(detail) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract bounded, timestamped review frames, audio, and metadata for TwinClip."
    )
    parser.add_argument("video", type=Path, help="Input local video file")
    parser.add_argument("output_dir", type=Path, help="New output directory")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Requested frame interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=300,
        help="Maximum extracted review frames (default: 300)",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=MAX_DEFAULT_DURATION_SECONDS,
        help=f"Maximum accepted video-stream duration (default: {MAX_DEFAULT_DURATION_SECONDS:g}s)",
    )
    parser.add_argument(
        "--max-output-bytes",
        type=int,
        default=MAX_DEFAULT_OUTPUT_BYTES,
        help=f"Maximum temporary output size (default: {MAX_DEFAULT_OUTPUT_BYTES} bytes)",
    )
    return parser.parse_args()


def is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except OSError:
        return False


def total_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def parse_rate(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            rate = float(numerator) / float(denominator)
        else:
            rate = float(value)
    except (ValueError, ZeroDivisionError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def main() -> int:
    args = parse_args()
    video = args.video.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not is_regular_file(video):
        print(f"error: input must be a regular local video file: {video}", file=sys.stderr)
        return 2
    if video.suffix.lower() in {".m3u", ".m3u8"}:
        print("error: HLS playlists are not accepted; provide a local media file", file=sys.stderr)
        return 2
    if video.stat().st_size > MAX_INPUT_BYTES:
        print(f"error: input exceeds {MAX_INPUT_BYTES} bytes", file=sys.stderr)
        return 2
    if not math.isfinite(args.interval) or args.interval <= 0:
        print("error: --interval must be a finite number greater than 0", file=sys.stderr)
        return 2
    if args.max_frames <= 0 or args.max_frames > 5000:
        print("error: --max-frames must be an integer from 1 to 5000", file=sys.stderr)
        return 2
    if not math.isfinite(args.max_duration) or args.max_duration <= 0 or args.max_duration > 4 * 60 * 60:
        print("error: --max-duration must be finite and between 0 and 14400 seconds", file=sys.stderr)
        return 2
    if args.max_output_bytes <= 0 or args.max_output_bytes > 2 * 1024 * 1024 * 1024:
        print("error: --max-output-bytes must be between 1 and 2147483648", file=sys.stderr)
        return 2
    if output_dir.exists():
        print(f"error: output directory already exists: {output_dir}", file=sys.stderr)
        return 2

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print("error: ffmpeg and ffprobe are required", file=sys.stderr)
        return 2

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=str(output_dir.parent)))

    try:
        probe_result = run(
            [
                ffprobe,
                "-v",
                "error",
                "-protocol_whitelist",
                PROTOCOL_WHITELIST,
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(video),
            ]
        )
        probe = json.loads(probe_result.stdout)
        streams = probe.get("streams", [])
        if not isinstance(streams, list):
            raise RuntimeError("ffprobe returned an invalid stream list")
        video_stream = next(
            (stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"),
            None,
        )
        if video_stream is None:
            raise RuntimeError("input has no video stream")

        # Prefer the video stream duration. Container duration may include a
        # longer audio tail and must not expand the visual inspection range.
        duration_raw = video_stream.get("duration")
        duration: float
        if duration_raw is not None:
            duration = float(duration_raw or 0)
        else:
            frame_count = video_stream.get("nb_frames")
            frame_rate = parse_rate(video_stream.get("avg_frame_rate"))
            if frame_count is not None and frame_rate:
                duration = float(frame_count) / frame_rate
            else:
                duration = float(probe.get("format", {}).get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeError("unable to determine a positive video-stream duration")
        if duration > args.max_duration:
            raise RuntimeError(
                f"video-stream duration {duration:.3f}s exceeds the {args.max_duration:.3f}s limit"
            )

        effective_interval = max(args.interval, duration / args.max_frames)
        if not math.isfinite(effective_interval) or effective_interval <= 0:
            raise RuntimeError("unable to determine a finite frame interval")
        frames_dir = temp_dir / "frames"
        frames_dir.mkdir()
        frame_filter = f"fps=1/{effective_interval:.6f},scale=min(1280\\,iw):-2,showinfo"
        frame_result = run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "info",
                "-protocol_whitelist",
                PROTOCOL_WHITELIST,
                "-i",
                str(video),
                "-vf",
                frame_filter,
                "-frames:v",
                str(args.max_frames),
                "-vsync",
                "vfr",
                "-q:v",
                "2",
                "-start_number",
                "0",
                str(frames_dir / "frame_%06d.jpg"),
            ]
        )

        frame_files = sorted(frames_dir.glob("frame_*.jpg"))
        if not frame_files:
            raise RuntimeError("ffmpeg produced no review frames")
        timestamps = [float(match.group(1)) for match in SHOWINFO_TIME.finditer(frame_result.stderr)]
        if len(timestamps) != len(frame_files):
            raise RuntimeError(
                f"could not map actual frame timestamps: ffmpeg reported {len(timestamps)} for {len(frame_files)} frames"
            )
        if any(not math.isfinite(value) or value < 0 or value > duration + 0.1 for value in timestamps):
            raise RuntimeError("ffmpeg returned an invalid frame timestamp")

        has_audio = any(isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams)
        audio_path: str | None = None
        if has_audio:
            audio_file = temp_dir / "audio.wav"
            run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-protocol_whitelist",
                    PROTOCOL_WHITELIST,
                    "-i",
                    str(video),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-t",
                    f"{duration:.3f}",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(audio_file),
                ]
            )
            audio_path = "audio.wav"

        output_size = total_size(temp_dir)
        if output_size > args.max_output_bytes:
            raise RuntimeError(
                f"temporary output {output_size} bytes exceeds the {args.max_output_bytes} byte limit"
            )

        manifest = {
            "schema_version": PREPARE_MANIFEST_SCHEMA_VERSION,
            "source_video": str(video),
            "source_sha256": sha256_file(video),
            "source_bytes": video.stat().st_size,
            "duration_seconds": round(duration, 3),
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "average_frame_rate": video_stream.get("avg_frame_rate"),
            "requested_interval_seconds": args.interval,
            "effective_interval_seconds": round(effective_interval, 6),
            "audio": audio_path,
            "transcript": None,
            "transcript_status": "unknown",
            "ocr_status": "not_run",
            "preparation_version": "1.0",
            "frames": [
                {
                    "path": f"frames/{frame_file.name}",
                    "time_seconds": round(timestamp, 3),
                }
                for frame_file, timestamp in zip(frame_files, timestamps)
            ],
            "limitations": [
                "Frames are bounded by max-frames and may miss actions shorter than the requested interval.",
                "ASR and OCR were not run; speech and on-screen text remain unknown until separately inspected.",
                "Verify decisive visual evidence against the original video and the actual frame timestamps.",
            ],
        }
        (temp_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_dir, output_dir)
    except (RuntimeError, ValueError, json.JSONDecodeError, OSError) as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"prepared {len(frame_files)} frames with actual timestamps at {effective_interval:.3f}s intervals: "
        f"{output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
