---
name: twinclip
description: Analyze creator shoppable short videos against a breakdown or teaching video and a Storyboard PDF. Use when Codex needs to measure overall learning similarity, breakdown-point similarity, storyboard similarity, borrowing evidence, creator adaptation risks, anchor-relative rankings, or batch adoption results with auditable timestamps instead of surface-level visual matching. 当用户提到复盘达人视频、对比达人视频和拆解视频或 Storyboard、评估复刻或学习落地、视频拆解要点相似度、Storyboard 相似度、借鉴点分析或 TwinClip 时使用；不要用于生成全新带货脚本，也不要用于两条无参考关系视频的通用相似度比较。
---

# TwinClip

Analyze whether one or more creator videos learned and applied the mechanisms taught by a breakdown video and Storyboard. Produce one validated report per creator video plus a batch manifest with four core outputs per report: overall learning result, breakdown-point similarity, Storyboard similarity, and structured borrowing analysis.

## 适用范围

本 skill 的中文触发范围包括“复盘达人视频”“看看达人视频复刻得怎么样”“对比达人视频和爆款拆解”“拆解要点相似度”“Storyboard 相似度”“借鉴点分析”“学习落地分”“总落地分”以及“评估这条内容是否适合这个达人”。

它要求存在有明确参考关系的拆解或教学视频和 Storyboard。两条没有参考关系的视频之间的通用相似度比较、全新带货脚本生成和内容策划，应交给其他 skill。

## Required inputs

Require these three inputs:

1. Breakdown or teaching video.
2. Storyboard, normally a PDF.
3. One or more creator videos.

Accept optional anchor videos and prior TwinClip reports for calibration. Do not require creator history, watch logs, or control groups. Assume the creator received the materials, but describe results as adoption or learning signals rather than causal proof. Verify every input is a readable regular local file before analysis.

## Core rules

- Judge content function, meaning, persuasion mechanism, and evidence use before visual resemblance.
- Treat the same product, category conventions, similar people, rooms, camera angles, or common product shots as insufficient evidence of learning.
- Extract creator-video observations once and reuse them for L, S, borrowing analysis, and adaptation diagnosis. The validator rejects L/S contradictions, unrelated guided evidence, and reused failure evidence.
- Keep A as a non-numeric adaptation diagnosis. Never include A in the total learning score.
- Keep every nonzero judgment traceable to creator-video evidence.
- Prefer a band, score interval, and confidence components over a falsely precise standalone score.
- Never infer missing visual proof from speech or missing speech from visuals. Record unavailable ASR/OCR channels as `unknown`.

## Workflow

### 1. Prepare the media

Verify all inputs and identify unreadable, missing, or mismatched files before scoring.

- Run `scripts/prepare_video.py` for the breakdown video and every creator video when direct timeline inspection is unavailable. Use the generated frame manifest and audio track. The helper rejects playlists, limits duration, frames, output size, and subprocess time, and records actual frame timestamps from the media stream.
- Use an available ASR capability for speech and OCR or visual inspection for on-screen text. When unavailable, mark the channel `unknown`; do not guess.
- Visually inspect every relevant Storyboard page. Preserve its nodes, labels, visual actions, spoken meaning, and cross-page rows.
- Separate teaching commentary from full-source replay in the breakdown video. Deduplicate repeated source footage.

### 2. Build the unified reference graph

Use the Storyboard as the primary node skeleton only when it is complete and matches the source discussed by the breakdown video. Otherwise merge both materials into a unified reference graph.

Classify nodes under six reusable stages without requiring every video to contain all six:

`Hook`, `Need`, `Product`, `Demonstration`, `Proof`, `CTA`.

Extract dynamic teaching points and attach each to zero, one, or several Storyboard nodes. Support cross-node mechanisms such as early result preview or claim-to-proof relationships. For every point record:

- content function;
- core meaning;
- persuasion element;
- evidence method;
- logical role;
- allowed substitutions;
- minimum observable evidence;
- false-positive guards;
- source timestamps and linked Storyboard nodes.

Derive teaching-point granularity only from mechanisms explicitly taught in the breakdown video or from a written teaching-unit list supplied in the user's request or PRD. Treat an explicitly enumerated user list as approved for the draft graph. Copy it one-to-one: do not merge, split, rename into finer units, or omit entries. Mark every point's `source_type` as `breakdown_explicit` or `user_provided`.

Before extracting a written list, distinguish authoritative requirements from quoted prior answers, alternative proposals, commentary, and hypothetical examples. Accept a list only when the document identifies it as what the referenced breakdown video actually teaches or the user explicitly approves it for this reference bundle. Phrases such as "for example" or imagined proof elements do not create teaching points. Record a verifiable `source_locator` for every point; reject any point without one.

Never place a mechanism inferred only from the Storyboard into `teaching_points`, even as a candidate. Keep Storyboard-derived details under node requirements, relationships, or reference notes for S. Add a new L point only after the breakdown video explicitly supports it or the user approves it.

Record cross-node logic as explicit reference-graph relationships, including preview-to-payoff, problem-to-solution, claim-to-proof, and reason-to-buy-to-CTA links.

When one breakdown contains multiple complete benchmark replays, lock one lane per benchmark and score the shared creator observations against every lane. Select the primary lane by `effective_coverage_rate`, then `T`; if both are exactly tied, use the declared lane order as a deterministic tie-breaker and record that tie. Keep the alternate lane summaries and their scores in the report. Do not treat mechanisms unique to the unselected lane as primary-lane omissions.

Version the graph as a `reference_bundle`. Lock it before scoring a batch. Bind its `content_hash` to the path-free content identities of the breakdown video and Storyboard. A changed source creates a new bundle identity and requires anchor revalidation. Read [reference-graph-example.md](references/reference-graph-example.md) for a compact end-to-end graph example, and [scoring-model.md](references/scoring-model.md) for point and node rubrics.

### 3. Blindly observe the creator video

Perform the first observation pass without exposing the reference teaching-point list. Record only observable facts:

- start and end time;
- people, product, action, result, and setting;
- on-screen text;
- transcript;
- independently recognizable content function.

Use this blind pass as the only automatic scoring evidence. Then run a reference-guided candidate check for subtle possible matches missed by the blind pass. Put those matches in `manual_pending`; do not score them unless a human confirms them.

### 4. Link evidence once

Link the shared observations to Storyboard nodes and teaching points. Do not run separate L and S extraction passes.

Assign each failure at most one `primary_failure_dimension`: `L`, `S`, or `A`. Record adaptation applicability separately with `adaptation_required` and `adaptation_result`; successful compensation is not a failure and must not depend on an `A` failure label.

### 5. Score and diagnose

Apply [scoring-model.md](references/scoring-model.md).

- Calculate L only from linked teaching points.
- Calculate S from every Storyboard node and the overall sales-logic assessment.
- Calculate T as `70% L + 30% S`, subject to versioned anchor calibration.
- Aggregate missing, surface, effective, and innovative teaching points.
- Produce a non-numeric adaptation status plus compensation counts and a short backend explanation.
- Calculate confidence from E, M, and R using the weakest component.

### 6. Calibrate against anchors

Read [calibration-and-qa.md](references/calibration-and-qa.md) whenever anchors, multiple creator videos, weight changes, prompt changes, model changes, or production monitoring are involved.

Use five initial anchors only as an ordering veto test. Do not freely optimize weights against five samples. Bind anchors to the exact reference-bundle ID, version, and content hash. Candidate anchor placement must be numerically bracketed and cannot leave an unresolved formula conflict in a final report.

### 7. Produce and validate the report

Follow [report-schema.md](references/report-schema.md). Put these four items at the top:

1. T: total learning band, interval, center index, and confidence.
2. L: breakdown-point similarity and coverage statistics.
3. S: Storyboard similarity and sales-logic diagnosis.
4. Borrowing summary: missing, surface, effective, and innovative counts, plus `surface_share` and `surface_error_rate` with their distinct denominators.

Keep full borrowing commentary and adaptation detail in the backend section unless requested. Always include why the video is not one band higher, why it is not one band lower, and up to three highest-impact next actions.

Validate JSON reports before delivery:

```bash
python3 scripts/validate_report.py /absolute/path/to/report.json
```

Fix every validation error. Disclose warnings, missing media channels, provisional weights, absent anchors, and unresolved manual candidates.

Evidence records must declare whether they are a functional `segment` or a complete `full_video` absence scope, and must link the Storyboard nodes they actually observe. A `full_video` record must span the declared creator-video duration, can verify a clear absence only, and cannot support a positive L, S, or relationship score. Positive overall logic assessments need segment evidence; positive relationship assessments must contain eligible segment evidence for both endpoint nodes. This prevents a generic whole-video note from inflating Storyboard or logic scores.

For a batch, bind drafts to the actual source files and produce reports plus group adoption statistics with:

```bash
python3 scripts/run_analysis.py \
  --breakdown-video /absolute/path/breakdown.mp4 \
  --storyboard-pdf /absolute/path/storyboard.pdf \
  --creator-video /absolute/path/creator-01.mp4 \
  --draft-report /absolute/path/creator-01-draft.json \
  --output-dir /absolute/path/twinclip-run
```

Repeat `--creator-video` and `--draft-report` in matching order for multiple creators. The draft contains the structured observations and assessments produced after inspecting the prepared media, plus the observation method, model, prompt, and extraction versions. `run_analysis.py` binds source paths, computes source hashes, derives durations, validates final reports, and writes `batch.json`. Each report contains exactly one creator video. Use `--allow-draft` only while human confirmation is pending. A final run must pass without it; unresolved candidates and formula conflicts are rejected even when `review_status` is incorrectly set to completed.

After 20 completed non-anchor analyses, combine the relevant batch manifests and create a random five-report QA sample:

```bash
python3 scripts/select_qa_sample.py \
  --batch-json /absolute/path/batch-01.json \
  --batch-json /absolute/path/batch-02.json \
  --output /absolute/path/twinclip-qa-sample.json \
  --population-size 20
```

Ask a human reviewer to label only those five bands, then compare those labels with the sample:

```bash
python3 scripts/qa_check.py \
  --batch-json /absolute/path/twinclip-qa-sample.json \
  --expected-bands /absolute/path/human-band-labels.json \
  --history /absolute/path/twinclip-qa-history.json
```

The QA command passes only when at least four of five bands agree and no result is off by two bands. It switches to five per 50 analyses after three consecutive passing samples and resets the cadence after a failure.

## Failure handling

- When the Storyboard and breakdown video describe different source videos, stop scoring and report the mismatch.
- When less than 65% of scoring decisions have clear evidence, mark confidence low and require manual review.
- When an anchor placement conflicts with the formula band, do not silently clamp the score. Mark a calibration conflict and request review.
- When no anchors exist, use the default weights and bands provisionally; cap confidence at medium.
- When a guided-only candidate remains unconfirmed, keep it out of L, S, and T.
- When a final report has low confidence or resolved manual-review decisions, require `review_status=completed`. Pending candidates and anchor conflicts keep it pending and block final delivery.
