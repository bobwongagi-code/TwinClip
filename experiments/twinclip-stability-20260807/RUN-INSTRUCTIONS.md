# TwinClip Stability Run Instructions

This experiment measures the distribution of compiled TwinClip judgments. It
does not ask the model to calculate a final score. Every replicate must use a
fresh process or worker and a unique `execution_context_id`.

## Fixed Inputs

- Reference graph: `reference-bundle.json` in this directory.
- One assigned frozen evidence file under `fixed-evidence/`.
- The frozen evidence is the only observation source for this phase. Do not
  re-run ASR/OCR, inspect another run, read the legacy score, or copy a score
  from another report. VidLingo outputs are already bound in the evidence
  snapshot.
- The replicate wrapper must verify the reference hash, fixed-evidence hash,
  creator-video hash, `run_id`, video ID, round, replicate index, and
  `execution_context_id` before any task is published.

## Run Isolation

For each planned run, initialize a semantic run with a genuinely fresh worker:

```bash
python3 twinclip/scripts/semantic_run.py init \
  --run-id r01-v01 \
  --experiment-id twinclip-stability-... \
  --execution-context-id ctx-r01-v01 \
  --video-id creator-id \
  --round 1 \
  --replicate-index 1 \
  --reference-bundle /absolute/path/reference-bundle.json \
  --breakdown-video /absolute/path/breakdown.mp4 \
  --storyboard-pdf /absolute/path/storyboard.pdf \
  --creator-video /absolute/path/creator.mp4 \
  --output-dir /absolute/path/semantic-runs/r01-v01 \
  --model-id model-name \
  --prompt-version twinclip-semantic-0.2 \
  --extraction-version frozen-vidlingo-1 \
  --fixed-evidence /absolute/path/fixed-evidence/creator.json \
  --temperature 0 \
  --seed 1
```

Do not use a prompt reset on a reused agent thread as a substitute for a fresh
worker. If a fresh context is unavailable, stop and mark the run as
non-independent rather than putting it in the formal sample.

## Model Tasks

Publish these files independently through `semantic_run.py publish`:

1. `observation`: frozen creator facts only, with original Malay/Manglish text.
2. `evidence_linking`: evidence IDs to observed Storyboard nodes.
3. One `teaching_point` task for `REF-A` and one for `REF-B`, using atomic
   observed/minimum-evidence/function/transformation states.
4. One `storyboard_node` task for the seven shared nodes, using function,
   element, and presentation-support states.
5. One `relationship` task per reference lane for that lane's sales-logic
   relationships, using `logic_state`. REF-A and REF-B must not share a
   relationship task merely because their Storyboard node IDs are shared.
6. One `adaptation` task, using applicability and compensation states.
7. Optional `candidate_check` for subtle guided-only matches.

The model task files must not contain `depth`, `function_score`,
`element_score`, `support_score`, `score`, `L`, `S`, `T_center`, `band`,
`confidence`, `primary_lane`, or any coverage/borrowing/adaptation aggregate.
The publisher rejects those fields.

## Compile the Result

After all task files are published, compile the report and the stability result
with the code-owned compiler:

```bash
python3 twinclip/scripts/compile_report.py \
  --reference-bundle /absolute/path/reference-bundle.json \
  --breakdown-video /absolute/path/breakdown.mp4 \
  --storyboard-pdf /absolute/path/storyboard.pdf \
  --creator-video /absolute/path/creator.mp4 \
  --semantic-dir /absolute/path/semantic-runs/r01-v01 \
  --duration 52 \
  --output-report /absolute/path/compiled/r01-v01.json \
  --output-stability-result /absolute/path/raw-results/r01-v01.json
```

The compiler derives both-lane L/S/T, primary lane, band, interval,
confidence, coverage, and adaptation aggregates. It then validates the final
report. The stability result is the only file consumed by
`stability_report.py`.

Formal results must use `twinclip-stability-run-0.2` and declare the current
`compiler_version`. A legacy result or a plan without a fixed evidence hash is
compatible-history input only; strict mode rejects it from the formal sample.

The scoring configuration and anchor placement are locked into `run.json` by
the initializer. Do not create or edit `scoring-config.json` or
`anchor-placement.json` inside the run directory; mutable sidecars are
rejected. A calibration registry, when used, is hash-checked against the
initializer snapshot.

## Required Identity Check

Before finishing, verify that the result's nested `run` object matches the
assigned manifest entry exactly. A result with the wrong video, replicate,
round, context, or filename must be rejected and written only to an audit
quarantine. Never overwrite a formal result path; publish to a unique
temporary path and atomically rename it after validation.

Do not average anything. The downstream report uses distributions, within-run
spread, lane/band switches, atomic state stability, and root-cause signals.
