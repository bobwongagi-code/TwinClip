# Anchor Calibration and Quality Assurance

## Initial anchors

Bind every anchor set to one exact `reference_bundle.id`, `reference_bundle.version`, and `reference_bundle.content_hash`. Do not reuse product-specific anchors across materially different source videos, Storyboards, teaching-point graphs, prompts, or models. Store the locked anchor-set record in an independent calibration registry JSON with its own content hash and reference it from the report. The registry locks the actual calibrated T/S weights, not just a version label. The shared registry contains anchor records and boundary clarity by lower/upper band pair. Candidate-specific placement, resolved band, and formula-conflict state belong in the report. The validator checks numeric bracketing and boundary membership rather than trusting a boolean `has_anchors` flag.

Select about five representative creator videos for the initial five bands. Ask human reviewers only to confirm:

- the appropriate band;
- whether one video demonstrates learning more clearly than another;
- whether a difference is decisive or too close to call.

Do not ask reviewers to assign exact scores.

## Language-qualified review

When any reference or creator material contains Malaysian Malay or Malay-English
code-switching, anchor labels and continuous-QA labels must be confirmed by a
reviewer who can read Malay subtitles and understand the spoken language. A
Chinese- or English-only reviewer cannot close the calibration loop for these
materials.

Keep two causes of uncertainty separate:

- poor ASR/OCR or weak Malay/Manglish comprehension requires re-transcription,
  OCR correction, or a language-qualified reviewer;
- an independently reviewed clip that still has an ambiguous match belongs in
  the normal `manual_pending` flow.

Do not widen score thresholds or treat uncalibrated bands as reliable to
compensate for language-quality problems. If a qualified reviewer is unavailable,
keep the anchor/QA state unresolved and disclose that limitation.

## Weight veto test

Run the default scoring formula on the initial anchors. Treat five anchors as a veto test, not a training set. A candidate placement is valid only when its T center is between the selected lower and upper anchor centers, its band is between their bands, and its displayed interval overlaps its human-resolved band.

The S prior is `35%` five-check logic plus `65%` Storyboard node score. The node
score is `46%` function, `38%` required elements, and `16%` presentation support.
Before changing these weights, inspect the repeated-run distributions for the
corresponding atomic judgments. The first 80-run audit showed drift in all four
old S proxies, so changing only a final S weight would hide the source rather
than repair it. Calibrate the five logic checks and unstable node boundaries
with contrastive examples, then use held-out videos to test distribution width
and band/lane switch rates.

- Keep the prior weights when anchor ordering is correct.
- When a clearly different band is reversed, identify the responsible dimension from evidence.
- Change one relevant weight at a time in increments of 0.10.
- Preserve weights that sum to 1, keep `L >= 0.50`, and never tune away the mechanism-learning dimension.
- Record the old weights, new weights, affected anchor pair, and reason.
- Do not tune weights merely to separate close or disputed anchors.

Without anchors, the only allowed T weights are the versioned 70/30 prior. A calibration change must be published as a new weights version tied to the same reference bundle and applied to all reports in that batch.

## Formal calibration

Wait until every important adjacent band boundary has at least two confirmed examples before formally tuning weights. Keep at least 20% of anchors out of tuning and use them only for validation.

Reject a calibration when it improves tuned anchors but reverses held-out anchor ordering or increases cross-band disagreements.

## Candidate handling

Run blind observation first. Then perform a guided candidate pass.

- Keep guided-only candidates scoring-ineligible.
- Add every pending candidate to the manual-review queue and confidence R.
- Promote a candidate only after human confirmation.
- Record why blind extraction missed every confirmed candidate; use repeated reasons to improve observation instructions.
- Keep rejected candidates as non-scoring audit records.

## Continuous quality assurance

Review 100% of low-confidence reports.

During initial operation:

1. Combine exactly 20 completed non-anchor analysis records and use `scripts/select_qa_sample.py` to randomly sample five. Do not include draft reports, anchor reports, or an analysis ID already present in QA history.
2. Pass when at least four of five bands agree and no result differs by two or more bands.
3. Pause batch scoring and recalibrate after a failed sample.
4. After three consecutive passing samples, reduce monitoring to five random reports per 50 analyses.

Use `scripts/qa_check.py` to record this band-only comparison and the current cadence in a history JSON. It requires the sample manifest, rejects drafts, anchors, duplicate/reused populations, and mixed reference/method scopes, and writes history atomically. It deliberately does not compare exact L, S, or T values.

Re-run all anchors whenever any of these changes, and increment `analysis_version` for analysis-contract changes:

- unified reference graph or teaching points;
- scoring weights or thresholds;
- analysis prompt or workflow;
- model or media-extraction method;
- Storyboard or breakdown-video version.

Track these operational signals:

- high, medium, and low confidence distribution;
- manual-review rate;
- guided-candidate confirmation rate;
- band agreement rate;
- two-band error count;
- repeated blind-miss reasons;
- formula-versus-anchor conflicts.

Keep the QA sample manifest, comparisons, and completion state with the same reference/method scope as the batch manifests. A green validator run proves structural consistency; it does not replace the band-only human sample review described above.

When low confidence remains high, improve evidence extraction or reference definitions before relaxing confidence thresholds.
