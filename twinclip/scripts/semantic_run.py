#!/usr/bin/env python3
"""Create and atomically publish TwinClip semantic-task runs.

This wrapper owns run identity. Model-produced task files are accepted only
when their identity matches the immutable manifest and when they contain no
code-derived output fields.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid
from typing import Any

from contracts import canonical_json, reference_bundle_hash, sha256_bytes, sha256_file  # noqa: E402
from semantic_pipeline import (  # noqa: E402
    COMPILER_VERSION,
    SEMANTIC_RUN_SCHEMA_VERSION,
    SEMANTIC_TASK_SCHEMA_VERSION,
    SemanticContractError,
    default_anchor_placement,
    default_scoring_config,
    load_run_manifest,
    read_json,
    validate_task,
    write_json_atomic_no_overwrite,
    write_json_atomic,
)


SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def source_record(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SemanticContractError(f"source is not a regular file: {path}")
    return {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize or publish a TwinClip semantic judgment run.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create an immutable semantic run manifest")
    init.add_argument("--run-id", required=True)
    init.add_argument("--experiment-id")
    init.add_argument("--execution-context-id")
    init.add_argument("--video-id")
    init.add_argument("--round", type=int)
    init.add_argument("--replicate-index", type=int)
    init.add_argument("--reference-bundle", required=True, type=Path)
    init.add_argument("--breakdown-video", required=True, type=Path)
    init.add_argument("--storyboard-pdf", required=True, type=Path)
    init.add_argument("--creator-video", required=True, type=Path)
    init.add_argument("--output-dir", required=True, type=Path)
    init.add_argument("--model-id", required=True)
    init.add_argument("--prompt-version", required=True)
    init.add_argument("--extraction-version", required=True)
    init.add_argument("--observation-method", default="agent_multimodal_review")
    init.add_argument("--temperature", type=float)
    init.add_argument("--seed", type=int)
    init.add_argument("--fixed-evidence-hash")
    init.add_argument("--fixed-evidence", type=Path)
    init.add_argument("--scoring-config", type=Path)
    init.add_argument("--anchor-placement", type=Path)

    publish = subparsers.add_parser("publish", help="validate and atomically publish one semantic task")
    publish.add_argument("--run-dir", required=True, type=Path)
    publish.add_argument("--task", required=True, type=Path)
    return parser.parse_args()


def initialize(args: argparse.Namespace) -> int:
    if not args.run_id.strip():
        raise SemanticContractError("--run-id must be non-empty")
    if args.temperature is not None and (not math.isfinite(args.temperature) or args.temperature < 0):
        raise SemanticContractError("--temperature must be finite and non-negative")
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists():
        raise SemanticContractError(f"semantic run directory already exists: {output_dir}")
    reference = read_json(args.reference_bundle.expanduser().resolve())
    reference_hash = reference.get("content_hash")
    if reference_hash != reference_bundle_hash(reference):
        raise SemanticContractError("reference bundle content_hash does not match the locked graph")
    breakdown = source_record(args.breakdown_video)
    storyboard = source_record(args.storyboard_pdf)
    creator = source_record(args.creator_video)
    if args.fixed_evidence_hash is not None and (
        len(args.fixed_evidence_hash) != 64
        or any(character not in "0123456789abcdef" for character in args.fixed_evidence_hash.lower())
    ):
        raise SemanticContractError("--fixed-evidence-hash must be a SHA-256 digest")
    if args.fixed_evidence_hash is not None and args.fixed_evidence is None:
        raise SemanticContractError("--fixed-evidence-hash requires --fixed-evidence")
    fixed_evidence = source_record(args.fixed_evidence) if args.fixed_evidence is not None else None
    if fixed_evidence is not None and args.fixed_evidence_hash is not None and fixed_evidence["sha256"] != args.fixed_evidence_hash:
        raise SemanticContractError("--fixed-evidence-hash does not match --fixed-evidence")
    scoring_config = default_scoring_config(reference)
    if args.scoring_config is not None:
        scoring_config = read_json(args.scoring_config.expanduser().resolve())
    if not isinstance(scoring_config, dict):
        raise SemanticContractError("--scoring-config must contain an object")
    scoring_config.setdefault("calibration_registry", None)
    anchor_placement = default_anchor_placement()
    if args.anchor_placement is not None:
        value = read_json(args.anchor_placement.expanduser().resolve())
        anchor_placement = value.get("anchor_placement", value)
        if not isinstance(anchor_placement, dict):
            raise SemanticContractError("--anchor-placement must contain an object")
    registry_hash = None
    registry_path = scoring_config.get("calibration_registry")
    if registry_path is not None:
        registry = Path(str(registry_path)).expanduser().resolve()
        scoring_config["calibration_registry"] = str(registry)
        registry_hash = sha256_file(registry)
    scoring_config_hash = sha256_bytes(canonical_json(scoring_config).encode("utf-8"))
    anchor_placement_hash = sha256_bytes(canonical_json(anchor_placement).encode("utf-8"))
    context_id = args.execution_context_id or f"ctx-{uuid.uuid4().hex}"
    manifest = {
        "schema_version": SEMANTIC_RUN_SCHEMA_VERSION,
        "run_id": args.run_id,
        "experiment_id": args.experiment_id,
        "execution_context_id": context_id,
        "video_id": args.video_id,
        "round": args.round,
        "replicate_index": args.replicate_index,
        "reference_bundle_hash": reference_hash,
        "creator_video_sha256": creator["sha256"],
        "fixed_evidence_path": fixed_evidence["path"] if fixed_evidence else None,
        "fixed_evidence_hash": fixed_evidence["sha256"] if fixed_evidence else args.fixed_evidence_hash,
        "model_id": args.model_id,
        "prompt_version": args.prompt_version,
        "extraction_version": args.extraction_version,
        "observation_method": args.observation_method,
        "temperature": args.temperature,
        "seed": args.seed,
        "compiler_version": COMPILER_VERSION,
        "task_schema_version": SEMANTIC_TASK_SCHEMA_VERSION,
        "scoring_config": scoring_config,
        "scoring_config_hash": scoring_config_hash,
        "anchor_placement": anchor_placement,
        "anchor_placement_hash": anchor_placement_hash,
        "calibration_registry_sha256": registry_hash,
        "source_hashes": {
            "breakdown_video": breakdown,
            "storyboard_pdf": storyboard,
            "creator_video": creator,
        },
    }
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=str(parent)))
    try:
        (temporary_dir / "tasks").mkdir()
        write_json_atomic(temporary_dir / "run.json", manifest)
        os.rename(temporary_dir, output_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    print(json.dumps({"run_id": args.run_id, "execution_context_id": context_id, "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


def publish(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    run = load_run_manifest(run_dir / "run.json")
    task = read_json(args.task.expanduser().resolve())
    validated = validate_task(task, run, path=args.task)
    task_id = validated["task_id"]
    if not SAFE_TASK_ID.fullmatch(task_id):
        raise SemanticContractError("task_id contains unsafe filename characters")
    tasks_dir = run_dir / "tasks"
    if not tasks_dir.exists():
        tasks_dir.mkdir(parents=False, exist_ok=False)
    target = tasks_dir / f"{task_id}.json"
    if target.exists():
        raise SemanticContractError(f"refusing to overwrite existing semantic task: {target}")
    write_json_atomic_no_overwrite(target, validated)
    print(json.dumps({"task_id": task_id, "task_type": validated["task_type"], "path": str(target)}, ensure_ascii=False))
    return 0


def main() -> int:
    try:
        args = parse_args()
        if args.command == "init":
            return initialize(args)
        return publish(args)
    except (OSError, ValueError, json.JSONDecodeError, SemanticContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
