#!/usr/bin/env python3
"""Evaluate a human band-only QA sample and persist its monitoring state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys
from typing import Any


BANDS = ["未采纳", "表层模仿", "单点机制迁移", "多点结构化迁移", "二次创新"]
MAX_BYTES = 5 * 1024 * 1024


def read_json(path: Path) -> Any:
    try:
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > MAX_BYTES:
            raise ValueError(f"file must be a regular file no larger than {MAX_BYTES} bytes: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"cannot read JSON {path}: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check a TwinClip band-only QA sample.")
    parser.add_argument("--batch-json", type=Path, required=True)
    parser.add_argument(
        "--expected-bands",
        type=Path,
        required=True,
        help="JSON array in the same order as batch.reports, or object keyed by creator_video.",
    )
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        batch = read_json(args.batch_json)
        expected = read_json(args.expected_bands)
        reports = batch.get("reports") if isinstance(batch, dict) else None
        if not isinstance(reports, list) or len(reports) < 5:
            raise RuntimeError("a QA sample must contain at least five batch reports")
        if isinstance(expected, list):
            expected_bands = expected
        elif isinstance(expected, dict):
            expected_bands = [expected.get(report.get("creator_video")) for report in reports]
        else:
            raise RuntimeError("expected bands must be an array or object")
        if len(expected_bands) != len(reports):
            raise RuntimeError("expected band count must equal batch report count")

        comparisons: list[dict[str, Any]] = []
        for report, expected_band in zip(reports, expected_bands):
            actual_band = report.get("band") if isinstance(report, dict) else None
            if actual_band not in BANDS or expected_band not in BANDS:
                raise RuntimeError("every actual and expected band must use the canonical band labels")
            distance = abs(BANDS.index(actual_band) - BANDS.index(expected_band))
            comparisons.append({
                "creator_video": report.get("creator_video"),
                "actual_band": actual_band,
                "expected_band": expected_band,
                "distance": distance,
                "agree": actual_band == expected_band,
            })
        agree_count = sum(item["agree"] for item in comparisons)
        two_band_errors = sum(item["distance"] >= 2 for item in comparisons)
        passed = agree_count / len(comparisons) >= 0.80 and two_band_errors == 0

        history: dict[str, Any]
        if args.history.exists():
            loaded = read_json(args.history)
            history = loaded if isinstance(loaded, dict) else {}
        else:
            history = {}
        samples = history.get("samples") if isinstance(history.get("samples"), list) else []
        consecutive_passes = int(history.get("consecutive_passes", 0)) if isinstance(history.get("consecutive_passes", 0), int) else 0
        consecutive_passes = consecutive_passes + 1 if passed else 0
        cadence = 50 if consecutive_passes >= 3 else 20
        sample = {
            "passed": passed,
            "sample_size": len(comparisons),
            "agree_count": agree_count,
            "agreement_rate": round(agree_count / len(comparisons), 4),
            "two_band_errors": two_band_errors,
            "comparisons": comparisons,
        }
        samples.append(sample)
        result = {
            "schema_version": "1.0",
            "passed": passed,
            "consecutive_passes": consecutive_passes,
            "next_sample_cadence": f"5 per {cadence} non-anchor analyses",
            "samples": samples[-20:],
            "latest": sample,
        }
        args.history.parent.mkdir(parents=True, exist_ok=True)
        args.history.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
        return 0 if passed else 1
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
