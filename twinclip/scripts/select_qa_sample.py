#!/usr/bin/env python3
"""Build an auditable five-report non-anchor QA sample from batch manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from contracts import BAND_LABELS, SCHEMA_VERSION  # noqa: E402


MAX_BYTES = 20 * 1024 * 1024


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise RuntimeError(f"batch JSON must be a regular file no larger than {MAX_BYTES} bytes: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read batch JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"batch JSON root must be an object: {path}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select five non-anchor reports for TwinClip QA.")
    parser.add_argument("--batch-json", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--population-size", type=int, choices=(20, 50), required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        batches = [read_json(path) for path in args.batch_json]
        if not batches:
            raise RuntimeError("at least one batch is required")
        scope = (
            batches[0].get("reference_bundle_content_hash"),
            batches[0].get("method_fingerprint"),
        )
        if not all(isinstance(value, str) and value for value in scope):
            raise RuntimeError("batches must declare reference_bundle_content_hash and method_fingerprint")

        pool: dict[str, dict[str, Any]] = {}
        for batch in batches:
            if batch.get("schema_version") != SCHEMA_VERSION:
                raise RuntimeError(f"batch schema_version must be {SCHEMA_VERSION}")
            current_scope = (
                batch.get("reference_bundle_content_hash"),
                batch.get("method_fingerprint"),
            )
            if current_scope != scope:
                raise RuntimeError("all batches in a QA sample must use the same analysis scope")
            reports = batch.get("reports")
            if not isinstance(reports, list):
                raise RuntimeError("batch.reports must be an array")
            if batch.get("creator_video_count") != len(reports):
                raise RuntimeError("batch.creator_video_count must match the number of reports")
            for report in reports:
                if not isinstance(report, dict):
                    raise RuntimeError("every batch report must be an object")
                analysis_id = report.get("analysis_id")
                if not isinstance(analysis_id, str) or not analysis_id:
                    raise RuntimeError("every batch report must have an analysis_id")
                if report.get("has_anchors") is not False:
                    raise RuntimeError("QA samples may contain non-anchor reports only")
                if report.get("review_status") != "completed":
                    raise RuntimeError("QA samples may contain completed reports only")
                if report.get("band") not in BAND_LABELS:
                    raise RuntimeError("every batch report must have a canonical band")
                if analysis_id in pool:
                    raise RuntimeError(f"duplicate analysis_id in QA population: {analysis_id}")
                pool[analysis_id] = report

        if len(pool) != args.population_size:
            raise RuntimeError(
                f"QA population must contain exactly {args.population_size} unique non-anchor reports; got {len(pool)}"
            )
        population_ids = list(pool)
        selected_ids = secrets.SystemRandom().sample(population_ids, 5)
        sample = {
            "schema_version": SCHEMA_VERSION,
            "reference_bundle_content_hash": scope[0],
            "method_fingerprint": scope[1],
            "creator_video_count": 5,
            "qa_sample": {
                "selection_method": "system_random",
                "population_size": args.population_size,
                "population_analysis_ids": population_ids,
                "selected_analysis_ids": selected_ids,
            },
            "reports": [pool[analysis_id] for analysis_id in selected_ids],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote QA sample with 5 reports from {args.population_size} non-anchor analyses: {args.output}")
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
