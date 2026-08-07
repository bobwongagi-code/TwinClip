#!/usr/bin/env python3
"""Freeze inputs and create an identity manifest for repeated TwinClip runs.

The stability experiment deliberately separates fixed observation evidence from
the repeated semantic judgment.  It uses the existing VidLingo-backed batch as
the frozen observation snapshot and never copies old scores into new runs.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sys
from pathlib import Path
import tempfile
from typing import Any

from contracts import ANALYSIS_VERSION, COMPILER_VERSION, canonical_json, sha256_bytes, sha256_file  # noqa: E402
from semantic_pipeline import SEMANTIC_TASK_SCHEMA_VERSION  # noqa: E402


EXPERIMENT_SCHEMA_VERSION = "twinclip-stability-experiment-0.1"
RUN_SCHEMA_VERSION = "twinclip-stability-run-0.2"
DEFAULT_REPLICATES = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze inputs for repeated TwinClip stability runs.")
    parser.add_argument("--legacy-run", required=True, type=Path, help="Existing TwinClip run directory")
    parser.add_argument("--videos-dir", required=True, type=Path, help="Directory containing source videos")
    parser.add_argument("--output-dir", required=True, type=Path, help="New experiment directory")
    parser.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return normalized or "video"


def source_hash(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def creator_report_pairs(legacy_run: Path, videos_dir: Path) -> list[tuple[Path, Path, dict[str, Any]]]:
    reports_dir = legacy_run / "reports"
    if not reports_dir.is_dir():
        raise ValueError(f"missing reports directory: {reports_dir}")
    videos = {
        source_hash(path)["sha256"]: path.resolve()
        for path in videos_dir.glob("*.mp4")
        if path.is_file() and path.name != "PAPA FEEL whatsapp.mp4"
    }
    pairs: list[tuple[Path, Path, dict[str, Any]]] = []
    for report_path in sorted(reports_dir.glob("*.json")):
        report = read_json(report_path)
        creator = report.get("analysis", {}).get("provenance", {}).get("source_hashes", {}).get("creator_video", {})
        digest = creator.get("sha256") if isinstance(creator, dict) else None
        if digest not in videos:
            raise ValueError(f"could not match report to a creator video by sha256: {report_path}")
        pairs.append((videos[digest], report_path, report))
    if len(pairs) != 16:
        raise ValueError(f"expected 16 creator reports, found {len(pairs)}")
    return pairs


def fixed_evidence_snapshot(video: Path, report: dict[str, Any], asr_dir: Path) -> dict[str, Any]:
    analysis = report.get("analysis", {})
    provenance = analysis.get("provenance", {})
    source_hashes = provenance.get("source_hashes", {})
    creator_hash = source_hashes.get("creator_video", {})
    asr_path = asr_dir / f"creator__{safe_id(video.stem)}.json"
    # The legacy filename for the full-width parenthesized creator ID is not
    # derivable from ASCII sanitization. Find it by inspecting the source path.
    if not asr_path.is_file():
        candidates = sorted(asr_dir.glob("creator__*.json"))
        asr_path = next((candidate for candidate in candidates if safe_id(video.stem) in candidate.stem), asr_path)
    evidence = analysis.get("evidence_records", [])
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(f"report has no evidence_records: {video}")
    blind_evidence = [
        copy.deepcopy(record)
        for record in evidence
        if isinstance(record, dict) and record.get("observation_mode") == "blind"
    ]
    if not blind_evidence:
        raise ValueError(f"report has no blind evidence_records: {video}")
    snapshot = {
        "schema_version": "twinclip-stability-evidence-0.1",
        "video_id": safe_id(video.stem),
        "creator_video": source_hash(video),
        "declared_duration_seconds": analysis.get("media_durations", {}).get(str(video),
            analysis.get("media_durations", {}).get(str(video.resolve()))),
        "observation_method": provenance.get("observation_method"),
        "extraction_version": provenance.get("extraction_version"),
        "language_note": "Frozen legacy observation snapshot; original ASR/OCR channels are retained as supplied.",
        "asr": source_hash(asr_path) if asr_path.is_file() else {"path": str(asr_path), "missing": True},
        "evidence_records": blind_evidence,
        "excluded_guided_evidence_count": len(evidence) - len(blind_evidence),
    }
    snapshot["evidence_hash"] = sha256_bytes(canonical_json(snapshot).encode("utf-8"))
    return snapshot


def method_fingerprint(repo_root: Path) -> str:
    tracked = [
        repo_root / "twinclip" / "SKILL.md",
        repo_root / "twinclip" / "references" / "scoring-model.md",
        repo_root / "twinclip" / "references" / "report-schema.md",
        repo_root / "twinclip" / "references" / "calibration-and-qa.md",
        repo_root / "twinclip" / "references" / "malay-language.md",
        repo_root / "twinclip" / "references" / "semantic-task-contract.md",
        repo_root / "twinclip" / "scripts" / "contracts.py",
        repo_root / "twinclip" / "scripts" / "semantic_pipeline.py",
        repo_root / "twinclip" / "scripts" / "semantic_run.py",
        repo_root / "twinclip" / "scripts" / "compile_report.py",
        repo_root / "twinclip" / "scripts" / "stability_report.py",
    ]
    payload = []
    for path in tracked:
        if path.is_file():
            payload.append({"path": str(path.relative_to(repo_root)), "sha256": sha256_file(path)})
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def build_experiment(args: argparse.Namespace) -> dict[str, Any]:
    legacy_run = args.legacy_run.expanduser().resolve()
    videos_dir = args.videos_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    reference_path = legacy_run / "reference-bundle.json"
    asr_dir = legacy_run / "asr"
    if not reference_path.is_file():
        raise ValueError(f"missing reference bundle: {reference_path}")
    if not asr_dir.is_dir():
        raise ValueError(f"missing ASR directory: {asr_dir}")

    reference = read_json(reference_path)
    pairs = creator_report_pairs(legacy_run, videos_dir)
    repo_root = Path(__file__).resolve().parents[2]
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=str(output_dir.parent)))
    try:
        write_json(staging_dir / "reference-bundle.json", reference)

        videos: list[dict[str, Any]] = []
        for index, (video, report_path, report) in enumerate(pairs, start=1):
            video_id = safe_id(video.stem)
            snapshot = fixed_evidence_snapshot(video, report, asr_dir)
            staged_evidence_path = staging_dir / "fixed-evidence" / f"{index:02d}-{video_id}.json"
            published_evidence_path = output_dir / "fixed-evidence" / f"{index:02d}-{video_id}.json"
            write_json(staged_evidence_path, snapshot)
            videos.append({
                "video_id": video_id,
                "ordinal": index,
                "source": source_hash(video),
                "legacy_report": source_hash(report_path),
                "fixed_evidence_path": str(published_evidence_path),
                "fixed_evidence_hash": snapshot["evidence_hash"],
            })

        experiment_id = f"twinclip-stability-{reference.get('reference_bundle_id', 'reference')}-20260807"
        runs: list[dict[str, Any]] = []
        for round_index in range(1, args.replicates + 1):
            for video in videos:
                run_id = f"r{round_index:02d}-v{video['ordinal']:02d}"
                runs.append({
                    "run_id": run_id,
                    "experiment_id": experiment_id,
                    "execution_context_id": f"ctx-{experiment_id}-{run_id}",
                    "round": round_index,
                    "replicate_index": round_index,
                    "video_id": video["video_id"],
                    "video_ordinal": video["ordinal"],
                    "fixed_evidence_path": video["fixed_evidence_path"],
                    "fixed_evidence_hash": video["fixed_evidence_hash"],
                    "semantic_run_dir": str((output_dir / "semantic-runs" / run_id).resolve()),
                    "result_path": None,
                    "status": "pending",
                })

        manifest = {
            "schema_version": EXPERIMENT_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "status": "frozen_inputs",
            "replicates": args.replicates,
            "video_count": len(videos),
            "expected_run_count": len(runs),
            "reference_bundle": {
                "path": str((output_dir / "reference-bundle.json").resolve()),
                "content_hash": reference.get("content_hash"),
                "source_content_hashes": reference.get("source_content_hashes", {}),
            },
            "fixed_observation": {
                "mode": "frozen_legacy_multimodal_observation",
                "asr_ocr_reextraction": False,
                "source_run": str(legacy_run),
                "vidlingo_outputs_reused": True,
                "note": "Phase 1 isolates semantic judgment variance. ASR/OCR variance is a separate follow-up experiment.",
            },
            "method": {
                "analysis_version": ANALYSIS_VERSION,
                "compiler_version": COMPILER_VERSION,
                "semantic_task_schema_version": SEMANTIC_TASK_SCHEMA_VERSION,
                "method_fingerprint": method_fingerprint(repo_root),
                "score_formula": "T=0.70*L+0.30*S",
                "final_score_aggregation": "descriptive_only; no replicate averaging used as a decision",
            },
            "videos": videos,
            "runs": runs,
        }
        write_json(staging_dir / "experiment.json", manifest)
        os.rename(staging_dir, output_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def main() -> int:
    try:
        args = parse_args()
        if args.replicates < 2 or args.replicates > 20:
            raise ValueError("--replicates must be between 2 and 20")
        manifest = build_experiment(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "experiment_id": manifest["experiment_id"],
        "video_count": manifest["video_count"],
        "expected_run_count": manifest["expected_run_count"],
        "output_dir": str(Path(manifest["runs"][0]["fixed_evidence_path"]).parents[1]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
