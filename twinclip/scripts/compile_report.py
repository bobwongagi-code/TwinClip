#!/usr/bin/env python3
"""Compile one atomic TwinClip semantic run into a validated final report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from semantic_pipeline import (  # noqa: E402
    COMPILER_VERSION,
    SemanticContractError,
    compile_report,
    load_run_manifest,
    stability_result,
    write_json_atomic_no_overwrite,
)
from validate_report import validate_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile atomic TwinClip semantic tasks into one final report.")
    parser.add_argument("--reference-bundle", required=True, type=Path)
    parser.add_argument("--breakdown-video", required=True, type=Path)
    parser.add_argument("--storyboard-pdf", required=True, type=Path)
    parser.add_argument("--creator-video", required=True, type=Path)
    parser.add_argument("--semantic-dir", required=True, type=Path)
    parser.add_argument("--duration", required=True, type=float)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int, default=300)
    parser.add_argument("--output-report", required=True, type=Path)
    parser.add_argument("--output-stability-result", type=Path)
    parser.add_argument("--allow-draft", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.allow_draft and args.output_stability_result is not None:
            raise SemanticContractError("formal stability results cannot be emitted from an allow-draft compilation")
        if args.duration <= 0 or args.interval <= 0 or args.max_frames <= 0:
            raise SemanticContractError("duration, interval, and max-frames must be positive")
        report = compile_report(
            reference_path=args.reference_bundle.expanduser().resolve(),
            breakdown_video=args.breakdown_video.expanduser().resolve(),
            storyboard_pdf=args.storyboard_pdf.expanduser().resolve(),
            creator_video=args.creator_video.expanduser().resolve(),
            semantic_dir=args.semantic_dir.expanduser().resolve(),
            duration=args.duration,
            interval=args.interval,
            max_frames=args.max_frames,
        )
        errors, warnings, _ = validate_report(report, allow_draft=args.allow_draft)
        for warning in warnings:
            print(f"warning: {warning}")
        if errors:
            raise SemanticContractError("final report failed validation: " + "; ".join(errors[:8]))
        output = args.output_report.expanduser().resolve()
        if output.exists():
            raise SemanticContractError(f"output report already exists: {output}")
        stability_value = None
        if args.output_stability_result is not None:
            stability_output = args.output_stability_result.expanduser().resolve()
            if stability_output.exists():
                raise SemanticContractError(f"stability result already exists: {stability_output}")
            stability_value = stability_result(
                report, load_run_manifest(args.semantic_dir.expanduser().resolve() / "run.json")
            )
        # Each artifact is prepared and atomically renamed only after all
        # validation and collision checks pass. Batch publication uses a
        # directory transaction in run_analysis.py when both files must move
        # as one unit.
        write_json_atomic_no_overwrite(output, report)
        if stability_value is not None:
            write_json_atomic_no_overwrite(stability_output, stability_value)
        print(f"compiled and validated {COMPILER_VERSION}: {output}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, SemanticContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
