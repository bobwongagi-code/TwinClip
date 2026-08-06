#!/usr/bin/env python3
"""Run the deterministic TwinClip delivery pipeline.

The multimodal reasoning step still belongs to the calling agent: it writes one
structured draft report per creator video after inspecting the prepared media
and the Storyboard. This command binds those drafts to the real source files,
prepares bounded media artifacts, validates final reports, and emits a batch
manifest with relative comparison metrics.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from contracts import (  # noqa: E402
    ANALYSIS_VERSION,
    PREPARE_MANIFEST_SCHEMA_VERSION,
    SCHEMA_VERSION,
    analysis_id,
    provenance_fingerprint,
    sha256_file,
)
from validate_report import validate_report  # noqa: E402


PREPARE_TIMEOUT_SECONDS = 240


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bind TwinClip draft reports to source media and produce final/batch outputs."
    )
    parser.add_argument("--breakdown-video", type=Path, required=True)
    parser.add_argument("--storyboard-pdf", type=Path, required=True)
    parser.add_argument("--creator-video", type=Path, action="append", required=True)
    parser.add_argument(
        "--draft-report",
        type=Path,
        action="append",
        required=True,
        help="One structured draft report per creator video, in the same order.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int, default=300)
    parser.add_argument("--allow-draft", action="store_true", help="write a batch draft while review is pending")
    return parser.parse_args()


def regular_file(path: Path, label: str) -> Path:
    path = path.expanduser().resolve()
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise RuntimeError(f"{label} is not readable: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise RuntimeError(f"{label} must be a regular file: {path}")
    return path


def source_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def run_prepare(video: Path, output_dir: Path, interval: float, max_frames: int) -> None:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "prepare_video.py"),
        str(video),
        str(output_dir),
        "--interval",
        str(interval),
        "--max-frames",
        str(max_frames),
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=PREPARE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"media preparation timed out for {video}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise RuntimeError(f"media preparation failed for {video}: {detail}") from exc
    if result.stdout.strip():
        print(result.stdout.strip())


def read_json(path: Path) -> dict[str, Any]:
    path = regular_file(path, "draft report")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise RuntimeError(f"draft report exceeds 20 MiB: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read draft report {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"draft report root must be an object: {path}")
    return value


def manifest_duration(prepared_dir: Path, source: Path, source_hash: str) -> float:
    manifest_path = prepared_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != PREPARE_MANIFEST_SCHEMA_VERSION:
        raise RuntimeError(f"unsupported media manifest schema: {manifest_path}")
    if manifest.get("source_sha256") != source_hash or manifest.get("source_video") != str(source):
        raise RuntimeError(f"media manifest source identity does not match {source}")
    duration = manifest.get("duration_seconds")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(float(duration)) or float(duration) <= 0:
        raise RuntimeError(f"invalid duration in media manifest: {manifest_path}")
    return float(duration)


def evidence_summary(report: dict[str, Any], creator_video: str) -> dict[str, dict[str, Any]]:
    analysis = report["analysis"]
    creator_video = str(Path(creator_video).expanduser().resolve())
    evidence = {
        item["id"]: item
        for item in analysis["evidence_records"]
        if isinstance(item, dict)
        and isinstance(item.get("creator_video"), str)
        and str(Path(item["creator_video"]).expanduser().resolve()) == creator_video
    }
    result: dict[str, dict[str, Any]] = {}
    for assessment in analysis["teaching_point_assessments"]:
        point_id = assessment["teaching_point_id"]
        records = [evidence[evidence_id] for evidence_id in assessment["evidence_ids"] if evidence_id in evidence]
        eligible = [record for record in records if record.get("human_confirmation") in {"not_required", "confirmed"}]
        if not eligible or assessment["depth"] == 0:
            result[point_id] = {
                "depth": assessment["depth"],
                "first_appearance_seconds": None,
                "evidence_count": 0,
                "persistence_observed": False,
            }
            continue
        first = min(float(record["start_seconds"]) for record in eligible)
        result[point_id] = {
            "depth": assessment["depth"],
            "first_appearance_seconds": round(first, 3),
            "evidence_count": len(eligible),
            "persistence_observed": len({record["id"] for record in eligible}) >= 2,
        }
    return result


def build_batch_summary(reports: list[tuple[str, Path, dict[str, Any]]], output_dir: Path) -> dict[str, Any]:
    first_report = reports[0][2]
    point_ids = [point["id"] for point in first_report["reference_bundle"]["teaching_points"]]
    point_summary: list[dict[str, Any]] = []
    for point_id in point_ids:
        per_creator = []
        for creator_video, report_path, report in reports:
            item = evidence_summary(report, creator_video).get(point_id, {
                "depth": 0,
                "first_appearance_seconds": None,
                "evidence_count": 0,
                "persistence_observed": False,
            })
            per_creator.append({"creator_video": creator_video, **item})
        count = len(per_creator)
        point_summary.append({
            "teaching_point_id": point_id,
            "adoption_rate": round(sum(item["depth"] >= 1 for item in per_creator) / count, 4),
            "effective_adoption_rate": round(sum(item["depth"] >= 2 for item in per_creator) / count, 4),
            "innovation_rate": round(sum(item["depth"] == 3 for item in per_creator) / count, 4),
            "persistence_rate": round(sum(item["persistence_observed"] for item in per_creator) / count, 4),
            "per_creator": per_creator,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_bundle_id": first_report["reference_bundle"]["id"],
        "reference_bundle_version": first_report["reference_bundle"]["version"],
        "reference_bundle_content_hash": first_report["reference_bundle"]["content_hash"],
        "method_fingerprint": first_report["analysis"]["provenance"]["method_fingerprint"],
        "creator_video_count": len(reports),
        "reports": [
            {
                "analysis_id": report["analysis"]["analysis_id"],
                "creator_video": creator_video,
                "creator_video_sha256": report["analysis"]["provenance"]["source_hashes"]["creator_video"]["sha256"],
                "report_path": str(report_path.relative_to(output_dir)),
                "band": report["analysis"]["scores"]["band"],
                "T_center": report["analysis"]["scores"]["T_center"],
                "L": report["analysis"]["scores"]["L"],
                "S": report["analysis"]["scores"]["S"],
                "has_anchors": report["analysis"]["anchor_placement"]["has_anchors"],
                "review_status": report["analysis"]["review_status"],
            }
            for creator_video, report_path, report in reports
        ],
        "teaching_points": point_summary,
    }


def main() -> int:
    args = parse_args()
    if len(args.creator_video) != len(args.draft_report):
        print("error: --creator-video and --draft-report must have the same count", file=sys.stderr)
        return 2
    if not math.isfinite(args.interval) or args.interval <= 0 or args.max_frames <= 0:
        print("error: interval and max-frames must be positive finite values", file=sys.stderr)
        return 2

    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists():
        print(f"error: output directory already exists: {output_dir}", file=sys.stderr)
        return 2

    try:
        breakdown_video = regular_file(args.breakdown_video, "breakdown video")
        storyboard_pdf = regular_file(args.storyboard_pdf, "Storyboard PDF")
        if storyboard_pdf.suffix.lower() != ".pdf":
            raise RuntimeError(f"Storyboard input must be a PDF: {storyboard_pdf}")
        creator_videos = [regular_file(path, "creator video") for path in args.creator_video]
        if len(creator_videos) != len(set(creator_videos)):
            raise RuntimeError("creator videos must be unique within a batch")
        drafts = [read_json(path) for path in args.draft_report]
        shared_source_hashes = {
            "breakdown_video": source_record(breakdown_video),
            "storyboard_pdf": source_record(storyboard_pdf),
        }
        creator_source_hashes = {str(path): source_record(path) for path in creator_videos}
        output_dir.mkdir(parents=True, exist_ok=False)
        prepared_dir = output_dir / "prepared"
        reports_dir = output_dir / "reports"
        prepared_dir.mkdir()
        reports_dir.mkdir()

        breakdown_prepared = prepared_dir / "breakdown"
        run_prepare(breakdown_video, breakdown_prepared, args.interval, args.max_frames)
        manifest_duration(
            breakdown_prepared,
            breakdown_video,
            shared_source_hashes["breakdown_video"]["sha256"],
        )
        creator_durations: dict[str, float] = {}
        for index, creator_video in enumerate(creator_videos, start=1):
            creator_prepared = prepared_dir / f"creator_{index:03d}"
            run_prepare(creator_video, creator_prepared, args.interval, args.max_frames)
            creator_durations[str(creator_video)] = manifest_duration(
                creator_prepared,
                creator_video,
                creator_source_hashes[str(creator_video)]["sha256"],
            )

        reports: list[tuple[str, Path, dict[str, Any]]] = []
        reference_identity: tuple[Any, Any, Any, Any] | None = None
        for index, (creator_video, draft) in enumerate(zip(creator_videos, drafts), start=1):
            report = copy.deepcopy(draft)
            if report.get("schema_version") != SCHEMA_VERSION:
                raise RuntimeError(f"draft for {creator_video} must use schema_version={SCHEMA_VERSION}")
            reference = report.setdefault("reference_bundle", {})
            reference["source_inputs"] = {
                "breakdown_video": str(breakdown_video),
                "storyboard_pdf": str(storyboard_pdf),
            }
            analysis = report.setdefault("analysis", {})
            analysis["creator_videos"] = [str(creator_video)]
            durations = analysis.setdefault("media_durations", {})
            durations[str(creator_video)] = creator_durations[str(creator_video)]
            provenance = analysis.setdefault("provenance", {})
            provenance["analysis_version"] = ANALYSIS_VERSION
            provenance["reference_bundle_hash"] = reference.get("content_hash")
            provenance["source_hashes"] = {
                **shared_source_hashes,
                "creator_video": creator_source_hashes[str(creator_video)],
            }
            provenance["media_preparation"] = {
                "manifest_schema_version": PREPARE_MANIFEST_SCHEMA_VERSION,
                "interval_seconds": args.interval,
                "max_frames": args.max_frames,
            }
            provenance["method_fingerprint"] = provenance_fingerprint(provenance)
            analysis["analysis_id"] = analysis_id(
                str(reference.get("content_hash")),
                creator_source_hashes[str(creator_video)]["sha256"],
                provenance["method_fingerprint"],
            )
            current_identity = (
                reference.get("id"),
                reference.get("version"),
                reference.get("content_hash"),
                provenance.get("method_fingerprint"),
            )
            if reference_identity is None:
                reference_identity = current_identity
            elif current_identity != reference_identity:
                raise RuntimeError("all creator drafts in a batch must use the same reference and analysis-method identity")
            report_path = reports_dir / f"creator_{index:03d}.json"
            errors, warnings, _ = validate_report(
                report,
                allow_draft=args.allow_draft,
            )
            for warning in warnings:
                print(f"warning: creator_{index:03d}: {warning}")
            if errors:
                details = "; ".join(errors[:8])
                if len(errors) > 8:
                    details += f"; and {len(errors) - 8} more"
                raise RuntimeError(f"draft for {creator_video} failed validation: {details}")
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            reports.append((str(creator_video), report_path, report))

        batch = build_batch_summary(reports, output_dir)
        (output_dir / "batch.json").write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {len(reports)} validated TwinClip report(s) and batch summary: {output_dir}")
        return 0
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
