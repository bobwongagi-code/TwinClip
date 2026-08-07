# TwinClip Report Schema

Emit UTF-8 JSON with `schema_version: "1.4"`. Use decimal ratios from 0 to 1 and scores from 0 to 100. Use seconds for timestamps. A final report must bind every source path to its current SHA-256 content and must pass `scripts/validate_report.py` without `--allow-draft`.

## Top-level structure

```json
{
  "schema_version": "1.4",
  "reference_bundle": {},
  "scoring_config": {},
  "analysis": {},
  "multi_reference": {}
}
```

`multi_reference` is optional. When one breakdown contains multiple complete benchmark replays, include one lane summary per benchmark. The selected lane must maximize `effective_coverage_rate`, then `T_center`; an exact tie uses the declared lane order and must set `selection_tie=true` with `tie_breaker=declared_lane_order`. The selected lane's `L`, `S`, and `T_center` must equal the report's primary scores. Keep alternate lanes separate and do not treat their unique teaching points as omissions from the selected lane.

## Reference bundle

```json
{
  "id": "product-source-id",
  "version": "1.0",
  "content_hash": "sha256-of-the-locked-reference-graph",
  "source_inputs": {
    "breakdown_video": "/absolute/path/breakdown.mp4",
    "storyboard_pdf": "/absolute/path/storyboard.pdf"
  },
  "source_content_hashes": {
    "breakdown_video": {"sha256": "...", "bytes": 123},
    "storyboard_pdf": {"sha256": "...", "bytes": 123}
  },
  "skeleton_mode": "storyboard",
  "status": "locked",
  "score_ready": true,
  "storyboard_nodes": [
    {
      "id": "SB01",
      "label": "Hook",
      "source_range": [0, 8],
      "function": "Establish the problem and preview the result",
      "required_elements": ["problem evidence", "result preview"]
    }
  ],
  "teaching_points": [
    {
      "id": "TP01",
      "source_type": "breakdown_explicit",
      "source_locator": "breakdown 00:32-00:50, teaching point 1",
      "stage": "Proof",
      "name": "Problem-to-result proof",
      "content_function": "Make the promised change believable",
      "core_meaning": "Show the problem and the resulting change",
      "persuasion_element": "result contrast",
      "evidence_method": "observable before and after comparison",
      "logical_role": "proof",
      "allowed_substitutions": ["another independently recognizable result proof"],
      "minimum_evidence": ["both sides of the comparison are observable"],
      "false_positive_guards": ["a generic product close-up does not count"],
      "source_ranges": [[32, 50]],
      "storyboard_node_ids": ["SB04"]
    }
  ],
  "relationships": [
    {
      "id": "REL01",
      "type": "claim_proof",
      "from_node_ids": ["SB03"],
      "to_node_ids": ["SB04"],
      "teaching_point_ids": ["TP01"],
      "description": "The claim is supported by the later observable proof"
    }
  ]
}
```

Use `skeleton_mode=storyboard` only after completeness and identity checks. Otherwise use `merged`. Allow an empty `storyboard_node_ids` list and multiple linked node IDs. Never put a Storyboard-only inference into `teaching_points`.

`content_hash` is computed from the locked semantic graph plus the path-free content identities in `source_content_hashes`. Runtime source paths are excluded from this graph hash. The same source identities are repeated under `analysis.provenance.source_hashes` with their bound paths for audit. A changed breakdown video or Storyboard therefore creates a new reference identity and requires anchor revalidation. Use `status=draft` and `score_ready=false` while the source identity, teaching list, or required media remains unresolved. A final scored report requires `status=locked`, `score_ready=true`, and a matching graph hash.

Teaching points require all of the semantic fields shown above, a verifiable source locator, source ranges, minimum evidence, and false-positive guards. This prevents an underspecified point from contributing to 70% of T.

## Scoring configuration

```json
{
  "l_weight": 0.70,
  "s_weight": 0.30,
  "s_weights": {
    "logic": 0.35,
    "function": 0.30,
    "elements": 0.25,
    "support": 0.10
  },
  "weights_version": "1.0-default",
  "calibration_registry": null
}
```

Every weight group must sum to 1. L must retain at least 0.50 weight. Reports without anchors must use the default T weights `0.70/0.30` and the default S weights. When no node has required persuasion elements, the S elements weight must be zero and the remaining S weights must be renormalized. When no reference relationships exist, the S logic weight and logic score must be zero and the other S weights must be renormalized. Reports with anchors must point to an independent locked calibration registry file whose own content hash, reference-bundle hash, anchor boundaries, and anchor records all match the report.

## Analysis provenance

Every report must include an `analysis.provenance` object. It identifies the semantic observation method and binds the report to the exact source bytes:

```json
{
  "analysis_id": "deterministic-id-from-reference-creator-and-method",
  "provenance": {
    "analysis_version": "1.3",
    "observation_method": "agent_multimodal_review",
    "model_id": "model-name-or-human-review",
    "prompt_version": "twinclip-prompt-1",
    "extraction_version": "asr-ocr-workflow-1",
    "reference_bundle_hash": "...",
    "media_preparation": {
      "manifest_schema_version": "1.2",
      "interval_seconds": 1.0,
      "max_frames": 300
    },
    "source_hashes": {
      "breakdown_video": {"path": "...", "sha256": "...", "bytes": 123},
      "storyboard_pdf": {"path": "...", "sha256": "...", "bytes": 123},
      "creator_video": {"path": "...", "sha256": "...", "bytes": 123}
    },
    "method_fingerprint": "sha256-of-the-method-fields"
  }
}
```

`analysis_id` and `method_fingerprint` are deterministic. A changed source file, reference graph, prompt, model, extraction workflow, or media-preparation setting must produce a new identity and trigger anchor revalidation.

## Evidence records

```json
{
  "id": "EV01",
  "creator_video": "/absolute/path/creator.mp4",
  "start_seconds": 32.1,
  "end_seconds": 38.5,
  "visual": "Creator shows the problem area and the result",
  "onscreen_text": "Visible subtitle or unknown",
  "transcript": "Spoken content or unknown",
  "observed_function": "Result proof",
  "coverage_scope": "The complete 32-50 second proof segment",
  "scope_complete": true,
  "evidence_scope": "segment",
  "storyboard_node_ids": ["SB04"],
  "observation_mode": "blind",
  "human_confirmation": "not_required",
  "candidate_id": null
}
```

Every evidence record requires a real creator-video file, a finite non-empty time range inside that video's declared duration, all four observation strings, an independently recognizable `observed_function`, a scope description, an `evidence_scope`, and a list of linked Storyboard node IDs. Use `evidence_scope=segment` for a functional clip. Use `evidence_scope=full_video` only for a complete inspected video-wide absence record; it must cover every Storyboard node, span the declared creator-video duration, and cannot support a positive L, S, or relationship score. At least one observation channel must contain something other than `unknown`. Empty evidence cannot support a score or E.

Blind evidence is eligible only with `human_confirmation=not_required` or `confirmed`. Guided evidence is eligible only with `human_confirmation=confirmed` and must be bound to a confirmed candidate with the same teaching point. Rejected or pending evidence never scores.

Positive Storyboard-node evidence must link that node. A positive sales-logic relationship must have eligible segment evidence for both its source and target nodes. A positive overall logic assessment must use eligible segment evidence; a clear zero decision marked `absence_verified=true` must use a `full_video` record.

## Assessments

Teaching-point assessment:

```json
{
  "teaching_point_id": "TP01",
  "depth": 2,
  "evidence_ids": ["EV01"],
  "reason": "The taught proof function is independently recognizable",
  "done_well": "Shows both sides of the comparison",
  "missing_or_misused": "The result contrast is brief",
  "manual_review": false,
  "evidence_clarity": "clear",
  "absence_verified": false,
  "primary_failure_dimension": null,
  "failure_id": null,
  "adaptation_required": "no",
  "adaptation_result": "not_needed"
}
```

Storyboard-node assessment:

```json
{
  "storyboard_node_id": "SB04",
  "function_score": 2,
  "element_score": 2,
  "support_score": 2,
  "evidence_ids": ["EV01"],
  "reason": "The proof function is complete and supported",
  "manual_review": false,
  "evidence_clarity": "clear",
  "absence_verified": false,
  "primary_failure_dimension": null,
  "failure_id": null
}
```

Use integer scores from 0 to 3. A node with required elements must have a non-null `element_score`; a node with no required elements must use `element_score=null`. Every node and teaching point has exactly one assessment.

`evidence_clarity=clear` with a zero score requires `absence_verified=true` and an eligible evidence record whose scope is complete. Ambiguous or unavailable evidence requires `manual_review=true`.

Use one unique `failure_id` per primary failure. Reusing the same evidence for different primary failure dimensions is rejected, so one mistake cannot be deducted twice.

## Sales-logic relationships

When the reference graph contains relationships, add exactly one assessment per relationship:

```json
{
  "relationship_id": "REL01",
  "score": 2,
  "evidence_ids": ["EV01", "EV07"],
  "reason": "The claim is followed by understandable proof",
  "manual_review": false,
  "evidence_clarity": "clear",
  "absence_verified": false,
  "primary_failure_dimension": null,
  "failure_id": null
}
```

`logic_assessment.relationship_ids` must cover every relationship, and its score must equal the unrounded mean of the relationship scores. This keeps S's logic component continuous while making it an aggregation of explicit claim, proof, problem, solution, reason-to-buy, and CTA links rather than an unsupported free-text impression.

## Candidate matches

```json
{
  "candidate_id": "C01",
  "teaching_point_id": "TP03",
  "evidence_ids": ["EV09"],
  "reason": "A subtle social-proof expression appeared only during guided review",
  "status": "manual_pending"
}
```

Allowed statuses are `manual_pending`, `confirmed`, and `rejected`. Candidate IDs and fingerprints must be unique. Pending candidates do not affect scores and make the report a draft until resolved; a report with any pending candidate cannot pass final validation.

## Scores, confidence, and review

```json
{
  "scores": {
    "L": 66.67,
    "S": 72.50,
    "T_center": 68.42,
    "T_range": [62, 74],
    "formula_band": "多点结构化迁移",
    "band": "多点结构化迁移",
    "provisional": true
  },
  "confidence": {
    "E": 0.80,
    "M": 0.5,
    "R": 0.20,
    "level": "medium"
  },
  "review_status": "completed"
}
```

`T_center=70%L+30%S`. `formula_band` is always derived from the center. Without anchors, `band=formula_band`, `provisional=true`, and confidence is capped at medium. The interval must be exactly rounded center +/- 3, 6, or 10 for high, medium, or low confidence, clamped to 0-100.

E, M, and R are recomputed from decision records. Low confidence and resolved manual-review decisions require `review_status=completed` for final delivery. Pending candidates and formula-anchor conflicts keep `review_status=pending` and block final delivery. Use `review_status=pending` only with `--allow-draft`.

## Anchor placement

```json
{
  "has_anchors": false,
  "anchor_set_id": null,
  "reference_bundle_id": null,
  "reference_bundle_version": null,
  "reference_bundle_hash": null,
  "weights_version": null,
  "lower_anchor": null,
  "upper_anchor": null,
  "anchor_band": null,
  "boundary_clarity": 0.5,
  "formula_conflict": false
}
```

When anchors exist, include `anchor_set_id`, matching reference-bundle ID/version/hash, the matching weights version, lower and upper anchor objects with IDs, bands, T centers, and matching bundle metadata, plus the human-resolved `anchor_band`. The candidate `T_center` must be numerically bracketed by the two anchors, and the candidate band must be bracketed by their bands. `scores.band` must equal the human-resolved band, and `T_range` must overlap it. `formula_conflict` must exactly state whether it differs from `formula_band`; it is never a free boolean. A formula conflict keeps the report pending until calibration weights are corrected.

The registry JSON must contain `schema_version: "1.1"`, `status: "locked"`, its own `content_hash`, the same anchor-set/bundle metadata, the exact calibrated `weights` values, a `boundaries` array containing lower/upper band pairs and their clarity, and an `anchors` array containing the referenced lower and upper anchor IDs and values. Candidate-specific `anchor_band` and `boundary_clarity` belong in the report placement, not in the shared registry. The validator checks the report against this file; a matching `weights_version` string alone is insufficient.

## Adaptation diagnosis

Do not calculate an A score or include it in T. Use `adaptation_required`, `adaptation_result`, and `primary_failure_dimension` on teaching-point records. Aggregate the counts into:

```json
{
  "required_count": 2,
  "successful_count": 1,
  "partial_count": 0,
  "failed_count": 1,
  "pending_count": 0,
  "unclear_count": 0,
  "compensation_hit_rate": 0.5,
  "status": "conditional",
  "summary": "The creator needs stronger third-party proof for the primary result claim."
}
```

The status is derived: unresolved applicability is `unknown`; failed with no successful compensation is `mismatch`; any partial or mixed failure is `conditional`; otherwise it is `aligned`.

## Complete analysis fields

The `analysis` object must contain exactly one `creator_videos` path per report, `analysis_id`, `provenance`, `media_durations`, `evidence_records`, all assessment lists, `scores`, `coverage`, `borrowing_summary`, `confidence`, `anchor_placement`, `review_status`, `adaptation_diagnostic`, `why_not_higher`, `why_not_lower`, and one to three `next_actions`. `done_well`, `missing_or_misused`, and adaptation narrative are optional backend detail fields; the structured evidence and counts are the scoring source of truth.

`coverage` must include these ratios:

```json
{
  "coverage_rate": 0.0,
  "effective_coverage_rate": 0.0,
  "innovation_rate": 0.0,
  "surface_share": 0.0,
  "surface_error_rate": 0.0
}
```

`surface_share` is `surface / N`, where `N` is the complete teaching-point
list. `surface_error_rate` is `surface / adopted`, where `adopted` counts points
with depth at least 1; when `adopted` is zero, emit `0.0`. They must not be
treated as interchangeable.

Keep detailed borrowing prose and adaptation explanation under backend fields. Keep the four core outputs at the top of any rendered human report.

## Batch manifest

`scripts/run_analysis.py` emits one validated report per creator video and a `batch.json` containing the shared reference-bundle content hash, analysis-method fingerprint, each video's immutable analysis ID and source hash, T/L/S/band, `surface_share`, `surface_error_rate`, final `review_status`, plus, for every teaching point:

- group adoption rate, effective adoption rate, innovation rate, and persistence rate;
- per-creator depth;
- first appearance timestamp from eligible evidence;
- evidence count and whether persistence was observed through multiple evidence records.

To perform continuous QA, combine exactly 20 or 50 non-anchor batch reports with `scripts/select_qa_sample.py`. It creates a system-random five-report sample with a population manifest. Run `scripts/qa_check.py` on that sample. The QA history is scoped to the exact reference-bundle and analysis-method fingerprints; mixed histories are rejected.
