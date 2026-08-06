# Anchor Calibration and Quality Assurance

## Initial anchors

Bind every anchor set to one exact `reference_bundle.id` and `reference_bundle.version`. Do not reuse product-specific anchors across materially different source videos, Storyboards, teaching-point graphs, prompts, or models. Store the locked anchor-set record in an independent calibration registry JSON and reference that file from the report. The registry contains the anchor-set ID, lower and upper anchor records, resolved band, boundary clarity, and weights version; the validator checks the report against the registry rather than trusting a boolean `has_anchors` flag.

Select about five representative creator videos for the initial five bands. Ask human reviewers only to confirm:

- the appropriate band;
- whether one video demonstrates learning more clearly than another;
- whether a difference is decisive or too close to call.

Do not ask reviewers to assign exact scores.

## Weight veto test

Run the default scoring formula on the initial anchors. Treat five anchors as a veto test, not a training set.

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

1. After every 20 completed non-anchor analyses, randomly sample five for band-only human review.
2. Pass when at least four of five bands agree and no result differs by two or more bands.
3. Pause batch scoring and recalibrate after a failed sample.
4. After three consecutive passing samples, reduce monitoring to five random reports per 50 analyses.

Use `scripts/qa_check.py` to record this band-only comparison and the current cadence in a history JSON. It deliberately does not compare exact L, S, or T values.

Re-run all anchors whenever any of these changes:

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

Keep the QA sample decisions and completion state with the batch manifest. A green validator run proves structural consistency; it does not replace the band-only human sample review described above.

When low confidence remains high, improve evidence extraction or reference definitions before relaxing confidence thresholds.
