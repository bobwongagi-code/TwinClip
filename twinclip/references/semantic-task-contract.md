# Semantic Task Contract

TwinClip has two different contracts:

1. **Semantic tasks**, which are the only files the model may produce.
2. **Final reports**, which are compiled and published by code.

The model must never emit the final report as one large JSON object. A task
file contains one `task_type` and a small set of related semantic judgments.
The compiler rejects derived fields such as `depth`, numeric `score`, `L`, `S`, `T`,
`S_components`, `band`, `E`, `M`, `R`, `M_components`, `confidence`, `coverage`,
`primary_lane`, and `adaptation_diagnostic`.

## Ownership

| Concern | Owner |
|---|---|
| ASR, OCR, raw timestamps, visible facts | VidLingo or the observation pass |
| Whether a function is independently recognizable | model |
| Whether minimum evidence and a mechanism are present | model |
| Five sales-logic states | model, in one small checklist task |
| Node, relationship, and adaptation semantic states | model, in separate task types |
| ID existence, source hashes, time bounds, task identity | code |
| Depth and node/relationship numeric scores | code |
| L, S, T, lane selection, coverage, bands, intervals | code |
| E/R, confidence level, adaptation counts and status | code |
| Human-facing summary and next actions | deterministic templates from compiled fields |

Evidence relevance is still a semantic decision. The model selects evidence
IDs for a judgment; code checks that those IDs exist, are eligible, and are
compatible with the referenced node or relationship.

## Immutable Run

Create a run before asking the model for any task:

```bash
python3 scripts/semantic_run.py init \
  --run-id run-001 \
  --reference-bundle /absolute/path/reference-bundle.json \
  --breakdown-video /absolute/path/breakdown.mp4 \
  --storyboard-pdf /absolute/path/storyboard.pdf \
  --creator-video /absolute/path/creator.mp4 \
  --output-dir /absolute/path/semantic-run \
  --model-id model-name \
  --prompt-version twinclip-semantic-0.2 \
  --extraction-version vidlingo-1
```

The wrapper records a unique `execution_context_id`, source hashes, optional
fixed-evidence path and hash, model, prompt, extraction version, temperature,
and seed when available. Every task must repeat the run identity. A mismatch
is rejected before publication.

Publish each task through the wrapper:

```bash
python3 scripts/semantic_run.py publish \
  --run-dir /absolute/path/semantic-run \
  --task /absolute/path/task.json
```

Publication uses a unique target and atomic no-overwrite creation. Existing
task files cannot be replaced.

## Task Types

Every task has this envelope:

```json
{
  "schema_version": "twinclip-semantic-task-0.3",
  "task_type": "teaching_point",
  "task_id": "teach-ref-a",
  "run": {
    "run_id": "run-001",
    "execution_context_id": "ctx-001",
    "reference_bundle_hash": "...",
    "creator_video_sha256": "..."
  },
  "payload": {}
}
```

The allowed task types are:

- `observation`: blind creator-video evidence only. It must not contain
  Storyboard links or guided candidates.
- `evidence_linking`: maps existing evidence IDs to observed Storyboard nodes.
- `teaching_point`: one lane's teaching points. Use atomic states below, not a
  numeric depth.
- `storyboard_node`: all Storyboard nodes. Use one state for each node
  dimension, not numeric scores.
- `logic_checklist`: exactly five independent sales-logic states shared by all
  reference lanes. Use the check IDs and `met`/`not_met`/`unclear` states below,
  never numeric scores.
- `relationship`: one task per reference lane for that lane's graph. Use
  `logic_state`, not a numeric relationship score. These are an audit view, not
  the numeric S logic component. Shared Storyboard node IDs do not imply that
  REF-A and REF-B share the same sales-logic edges.
- `adaptation`: point-level applicability and compensation states. This is a
  separate question from L and cannot be smuggled into a teaching-point score.
- `candidate_check`: subtle reference-guided candidates and guided evidence;
  pending candidates never score.

The compiler requires exactly one observation, evidence-linking, Storyboard,
logic-checklist, and adaptation task. It requires one teaching-point and one
relationship task per reference lane. Candidate checking is optional.

## Atomic States

Teaching-point judgments contain:

```json
{
  "teaching_point_id": "TP01",
  "observed_state": "observed",
  "minimum_evidence_state": "met",
  "function_state": "landed",
  "transformation_state": "none",
  "evidence_ids": ["EV01"],
  "evidence_clarity": "clear",
  "manual_review": false,
  "failure_dimension_candidate": null,
  "reason": "The independently observed action satisfies the reference function."
}
```

The compiler maps these states to depth only after the task is validated:

- not observed -> depth `0`;
- observed but minimum evidence or function is not met -> depth `1`;
- observed, minimum evidence met, function landed, no meaningful transformation -> depth `2`;
- the same conditions with meaningful transformation -> depth `3`.

`unclear` states or non-clear evidence require `manual_review=true` and are
conservatively kept out of positive scoring until resolved.

Storyboard tasks use these state maps:

- `function_state`: `missing`, `fragment`, `understandable`, `complete`;
- `element_state`: `not_required`, `missing`, `partial`, `correct`, `clear`;
- `support_state`: `contradictory`, `weak`, `supportive`, `especially_clear`.

Relationship tasks use `logic_state`: `broken`, `jump`, `complete`, or
`convincing`. Adaptation tasks use `applicability_state` and
`compensation_state` rather than an A score.

Logic-checklist tasks use these five IDs:

```text
hook_leads_need
points_answer_problem
claims_supported
cta_has_reason
coherent_if_reordered
```

Each item contains `check_id`, `state`, evidence IDs, evidence clarity,
`manual_review`, and a reason. `score` is forbidden in the task and is derived
from a clear `met` state by the compiler. Ambiguous or unavailable evidence is
kept at zero until review.

## Compilation

Compile one report only after all required task files are published:

```bash
python3 scripts/compile_report.py \
  --reference-bundle /absolute/path/reference-bundle.json \
  --breakdown-video /absolute/path/breakdown.mp4 \
  --storyboard-pdf /absolute/path/storyboard.pdf \
  --creator-video /absolute/path/creator.mp4 \
  --semantic-dir /absolute/path/semantic-run \
  --duration 52 \
  --output-report /absolute/path/creator-report.json
```

The run initializer snapshots the scoring configuration, anchor placement, and
calibration-registry identity into the immutable `run.json`. The compiler does
not read mutable `scoring-config.json` or `anchor-placement.json` sidecars.
It derives all numeric and categorical output fields and then runs
`validate_report.py`. For a batch, `run_analysis.py` accepts one
`--semantic-dir` per creator and atomically publishes the complete output
directory. The old `--draft-report` path is intentionally unsupported.
