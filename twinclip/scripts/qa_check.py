#!/usr/bin/env python3
"""Evaluate a scoped five-report QA sample and persist monitoring state."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import fcntl
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from contracts import BAND_LABELS, QA_HISTORY_SCHEMA_VERSION, SCHEMA_VERSION  # noqa: E402


BANDS = BAND_LABELS
MAX_BYTES = 5 * 1024 * 1024


def read_json(path: Path) -> Any:
    try:
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > MAX_BYTES:
            raise ValueError(f"file must be a regular file no larger than {MAX_BYTES} bytes: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"cannot read JSON {path}: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check a scoped TwinClip band-only QA sample.")
    parser.add_argument("--batch-json", type=Path, required=True, help="A sample from select_qa_sample.py.")
    parser.add_argument(
        "--expected-bands",
        type=Path,
        required=True,
        help="JSON object keyed by analysis_id, or an array in sample order.",
    )
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


@contextmanager
def history_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def validate_sample(batch: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], tuple[str, str]]:
    if batch.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(f"QA sample schema_version must be {SCHEMA_VERSION}")
    reports = batch.get("reports")
    if not isinstance(reports, list) or len(reports) != 5:
        raise RuntimeError("a QA sample must contain exactly five reports")
    if batch.get("creator_video_count") != 5:
        raise RuntimeError("QA sample creator_video_count must be 5")
    scope = (batch.get("reference_bundle_content_hash"), batch.get("method_fingerprint"))
    if not all(isinstance(value, str) and value for value in scope):
        raise RuntimeError("QA sample must declare reference_bundle_content_hash and method_fingerprint")
    metadata = batch.get("qa_sample")
    if not isinstance(metadata, dict):
        raise RuntimeError("batch.qa_sample metadata is required")
    if metadata.get("selection_method") != "system_random":
        raise RuntimeError("QA sample selection_method must be system_random")
    if metadata.get("population_size") not in {20, 50}:
        raise RuntimeError("QA sample population_size must be 20 or 50")
    population_ids = metadata.get("population_analysis_ids")
    selected_ids = metadata.get("selected_analysis_ids")
    if not isinstance(population_ids, list) or len(population_ids) != metadata["population_size"]:
        raise RuntimeError("QA sample population_analysis_ids must match population_size")
    if not all(isinstance(value, str) and value for value in population_ids) or len(set(population_ids)) != len(population_ids):
        raise RuntimeError("QA sample population_analysis_ids must contain unique non-empty strings")
    if not isinstance(selected_ids, list) or len(selected_ids) != 5:
        raise RuntimeError("QA sample selected_analysis_ids must contain five unique ids")
    if not all(isinstance(value, str) and value for value in selected_ids) or len(set(selected_ids)) != 5:
        raise RuntimeError("QA sample selected_analysis_ids must contain unique non-empty strings")
    report_ids: list[str] = []
    for report in reports:
        if not isinstance(report, dict):
            raise RuntimeError("every QA report must be an object")
        analysis_id = report.get("analysis_id")
        if not isinstance(analysis_id, str) or not analysis_id:
            raise RuntimeError("every QA report must have an analysis_id")
        if report.get("has_anchors") is not False:
            raise RuntimeError("QA samples may contain non-anchor reports only")
        if report.get("review_status") != "completed":
            raise RuntimeError("QA samples may contain completed reports only")
        if report.get("band") not in BANDS:
            raise RuntimeError("every actual band must use the canonical labels")
        report_ids.append(analysis_id)
    if report_ids != selected_ids or any(analysis_id not in population_ids for analysis_id in report_ids):
        raise RuntimeError("QA sample reports must exactly match selected_analysis_ids in order")
    return reports, metadata, scope


def expected_values(value: Any, reports: list[dict[str, Any]]) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value.get(report["analysis_id"]) for report in reports]
    raise RuntimeError("expected bands must be an array or object")


def run_check(args: argparse.Namespace) -> int:
    try:
        batch = read_json(args.batch_json)
        if not isinstance(batch, dict):
            raise RuntimeError("batch JSON root must be an object")
        reports, metadata, scope = validate_sample(batch)
        expected = expected_values(read_json(args.expected_bands), reports)
        if len(expected) != len(reports):
            raise RuntimeError("expected band count must equal the QA sample count")

        history: dict[str, Any]
        if args.history.exists():
            loaded = read_json(args.history)
            history = loaded if isinstance(loaded, dict) else {}
            if history.get("schema_version") != QA_HISTORY_SCHEMA_VERSION:
                raise RuntimeError("QA history schema version is unsupported")
            if history.get("scope") != {
                "reference_bundle_content_hash": scope[0],
                "method_fingerprint": scope[1],
            }:
                raise RuntimeError("QA history belongs to a different reference or analysis method; use a new history file")
        else:
            history = {}

        consecutive_passes = history.get("consecutive_passes", 0)
        if not isinstance(consecutive_passes, int) or consecutive_passes < 0:
            raise RuntimeError("QA history consecutive_passes must be a non-negative integer")
        required_population = 50 if consecutive_passes >= 3 else 20
        if metadata["population_size"] != required_population:
            raise RuntimeError(
                f"this QA sample must represent exactly {required_population} non-anchor analyses"
            )
        audited_population_ids = history.get("audited_population_analysis_ids", [])
        if not isinstance(audited_population_ids, list) or not all(
            isinstance(value, str) and value for value in audited_population_ids
        ):
            raise RuntimeError("QA history audited_population_analysis_ids must be an array of strings")
        if set(metadata["population_analysis_ids"]) & set(audited_population_ids):
            raise RuntimeError("QA population reuses analyses already included in an earlier QA sample")

        comparisons: list[dict[str, Any]] = []
        for report, expected_band in zip(reports, expected):
            if expected_band not in BANDS:
                raise RuntimeError("every expected band must use the canonical labels")
            actual_band = report["band"]
            distance = abs(BANDS.index(actual_band) - BANDS.index(expected_band))
            comparisons.append({
                "analysis_id": report["analysis_id"],
                "creator_video": report.get("creator_video"),
                "actual_band": actual_band,
                "expected_band": expected_band,
                "distance": distance,
                "agree": actual_band == expected_band,
            })
        agree_count = sum(item["agree"] for item in comparisons)
        two_band_errors = sum(item["distance"] >= 2 for item in comparisons)
        passed = agree_count / len(comparisons) >= 0.80 and two_band_errors == 0
        consecutive_passes = consecutive_passes + 1 if passed else 0
        cadence = 50 if consecutive_passes >= 3 else 20
        sample = {
            "passed": passed,
            "population_size": metadata["population_size"],
            "sample_size": len(comparisons),
            "agree_count": agree_count,
            "agreement_rate": round(agree_count / len(comparisons), 4),
            "two_band_errors": two_band_errors,
            "population_analysis_ids": metadata["population_analysis_ids"],
            "selected_analysis_ids": metadata["selected_analysis_ids"],
            "comparisons": comparisons,
        }
        samples = history.get("samples") if isinstance(history.get("samples"), list) else []
        result = {
            "schema_version": QA_HISTORY_SCHEMA_VERSION,
            "scope": {
                "reference_bundle_content_hash": scope[0],
                "method_fingerprint": scope[1],
            },
            "passed": passed,
            "consecutive_passes": consecutive_passes,
            "next_sample_cadence": f"5 per {cadence} non-anchor analyses",
            "audited_population_analysis_ids": [
                *audited_population_ids,
                *metadata["population_analysis_ids"],
            ],
            "samples": [*samples, sample][-20:],
            "latest": sample,
        }
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        atomic_write(args.history, payload)
        if args.output:
            atomic_write(args.output, payload)
        else:
            print(payload, end="")
        return 0 if passed else 1
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def main() -> int:
    args = parse_args()
    try:
        with history_lock(args.history):
            return run_check(args)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
