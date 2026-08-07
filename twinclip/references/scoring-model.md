# TwinClip Scoring Model

## Contents

1. Unified evidence principle
2. Teaching-point score L
3. Storyboard score S
4. Total learning score T
5. Borrowing analysis
6. Adaptation diagnosis
7. Confidence

## 1. Unified evidence principle

Extract observable creator-video facts once. Let L and S read the same evidence links through different views:

- L asks whether an explicitly taught mechanism was adopted.
- S asks whether the full Storyboard content skeleton and sales logic were realized.

A Storyboard node may be covered while its attached teaching point receives only depth 1. This is not a contradiction: the creator included the segment but did not implement the emphasized mechanism correctly.

Use a guided candidate only after blind extraction. Require human confirmation before it becomes scoring-eligible.

The report validator is the enforcement boundary. Every scoring-eligible evidence record must have a real creator-video path, a finite timestamp range, an independently recognizable observed function, a non-empty inspection scope, and explicit Storyboard-node links. Use a `segment` record for a functional observation and a `full_video` record only for complete-scope absence checking; `full_video` must span the declared creator-video duration. Blind records marked rejected or pending are ineligible. A zero score with `evidence_clarity=clear` still needs an eligible, complete-scope `full_video` observation before it can count as a verified absence.

L and S must consume the same evidence records and the same functional observations. Positive node scores must use evidence linked to that node, positive relationship scores must use evidence for both endpoints, and a positive overall logic assessment must use segment evidence. If a teaching point claims depth 2 or 3 while every linked Storyboard node is scored absent, the report is invalid. A primary failure also gets one unique failure ID; the same evidence cannot carry different failure dimensions.

### Third-party recognizability rule

Count an allowed substitution only when a viewer who was not given the reference list could independently recognize the intended persuasion function. If the connection appears only after forcing the clip onto the reference list, assign depth 0 and optionally create a manual candidate.

Common category behavior or surface similarity can never establish functional adoption by itself. Cap it at depth 1.

## 2. Teaching-point score L

Assign every teaching point exactly one depth:

| Depth | Meaning | Required evidence |
|---|---|---|
| 0 | Not adopted | No independently recognizable use of the mechanism |
| 1 | Surface or incorrect imitation | A related cue exists, but required components or intended function are missing |
| 2 | Functional adoption | The taught tactic is structurally complete and its intended function is recognizable |
| 3 | Transformative adoption | Depth 2 holds, and the creator meaningfully changes the form while preserving or strengthening the mechanism |

Do not judge creator credibility inside L. Judge whether the tactic was attempted and implemented with the required structural components.

Use only `breakdown_explicit` or `user_provided` teaching points in N. When a PRD or user request enumerates a teaching-unit list, preserve that list one-to-one. Never inflate N by converting each Storyboard action, claim, prop, or inferred mechanism into a separate point. Storyboard-only candidates are forbidden in L; store them under node requirements, relationships, or reference notes for S.

Require each point to cite a source video timestamp or a document section and item number in `source_locator`. Do not treat hypothetical examples, quoted agent proposals, or prior draft answers embedded in a PRD as authoritative teaching units.

Calculate with equal teaching-point weights by default:

```text
L = 100 * sum(depth) / (3 * teaching_point_count)
```

Calculate four mutually exclusive borrowing counts:

- missing: depth 0;
- surface: depth 1;
- effective: depth 2;
- innovative: depth 3.

Also calculate:

```text
coverage_rate           = count(depth >= 1) / N
effective_coverage_rate = count(depth >= 2) / N
innovation_rate         = count(depth == 3) / N
surface_share           = count(depth == 1) / N
surface_error_rate      = count(depth == 1) / max(count(depth >= 1), 1)
```

`surface_share` and `surface_error_rate` answer different questions and must both
be retained:

- `surface_share` uses all teaching points as the denominator. It shows how much
  of the reference teaching list is occupied by surface or incorrect imitation.
- `surface_error_rate` uses adopted points as the denominator. It shows how often
  an observed adoption attempt is only surface-level, excluding points that were
  completely missed.

Do not rename one metric to the other or compare their values without naming the
denominator.

## 3. Storyboard score S

Ignore exact duration and fixed order in the numeric score. Keep them only as diagnostic observations. Permit reordered sections when the sales logic remains coherent.

Score every Storyboard node from 0 to 3 on these node dimensions:

### Content function

- 0: missing;
- 1: present only as a fragment or surface cue;
- 2: function is understandable and mostly complete;
- 3: function is complete and clearly contributes to the sale.

### Key persuasion elements

- 0: required element absent or wrong;
- 1: partially present but weak or ambiguous;
- 2: correctly used;
- 3: clearly and efficiently used.

Use `null` when a node has no required persuasion element. Exclude null values from the element average.

### Presentation support

- 0: visuals or speech contradict or obscure the message;
- 1: message is recoverable but poorly supported;
- 2: presentation supports the message;
- 3: presentation makes the message especially clear.

Score the whole video from 0 to 3 for sales-logic coherence:

- 0: no coherent route from attention to purchase;
- 1: understandable fragments with major unsupported jumps;
- 2: a complete and understandable sales argument;
- 3: a clear, convincing chain in which claims, proof, reasons to buy, and CTA support one another.

Normalize each dimension to 0-100. Use these initial, versioned priors:

```text
S = 35% sales_logic
  + 30% mean(node_content_function)
  + 25% mean(non_null_node_element_use)
  + 10% mean(node_presentation_support)
```

Validate these weights against anchors. Do not treat them as universal constants.

The sales-logic component is assessed through the explicit reference-graph relationships. There is one relationship assessment per relationship, and the overall logic score is the unrounded mean of those assessments. This keeps the preference for a smooth selling argument continuous and separate from exact Storyboard order or duration.

If a node has required persuasion elements, it must receive a non-null element score. If no node has required elements, set the elements weight to zero and renormalize the remaining S weights. A null element score cannot remove a required node from the denominator.

If the reference graph has no relationships, set the logic score and logic weight to zero and renormalize the remaining S dimensions. Never replace missing relationships with a free-form logic impression.

## 4. Total learning score T

Use these initial priors:

```text
T_center = 70% L + 30% S
```

Keep L at or above 50% during calibration. Without anchors, use exactly the 70/30 prior. A calibration record must be versioned and bound to the reference bundle; a report cannot invent a new weight version by editing its own JSON.

Do not include adaptation diagnosis in T.

Display one of these bands:

| Range | Band |
|---|---|
| 0-19 | 未采纳 |
| 20-39 | 表层模仿 |
| 40-59 | 单点机制迁移 |
| 60-79 | 多点结构化迁移 |
| 80-100 | 二次创新 |

Treat the center as an internal ranking index, not an objective truth. Display an interval:

- high confidence: center +/- 3;
- medium confidence: center +/- 6;
- low confidence: center +/- 10 and mandatory review.

Round interval endpoints to whole numbers using deterministic positive-value half-up rounding, then clamp them to 0-100. Use the same rule when mapping the center to a band. Version and recalibrate these widths with anchor evidence.

When anchor comparison establishes a band, treat it as primary only when the candidate center is bracketed by the selected anchors and the displayed interval overlaps the human-resolved band. If the formula center or interval falls outside that band, report a calibration conflict and keep the report pending rather than silently changing either result.

When no anchors exist, derive a provisional band from the default numeric ranges and cap confidence at medium.

The interval is deterministic: round and clamp center +/- 3 for high, +/- 6 for medium, or +/- 10 for low. Low confidence and resolved manual-review decisions require a completed review. Unresolved guided candidates and formula-anchor conflicts keep the report pending and block final delivery.

## 5. Borrowing analysis

For every teaching point record:

- stage and teaching-point name;
- depth and status;
- creator-video timestamps and evidence IDs;
- what was done correctly;
- what was missing or misused;
- linked Storyboard node IDs;
- primary failure dimension;
- adaptation applicability and result;
- manual-review state.

For every Storyboard relationship record its score, evidence, and manual-review state. The relationship records are the auditable source for the overall sales-logic score.

Generate narrative detail from this structure. Do not make free text the source of scores.

## 6. Adaptation diagnosis

Do not calculate an A score. Keep adaptation outside T and the four core numeric outputs.

Separate applicability from failure ownership:

- `adaptation_required`: `yes`, `no`, or `unclear`;
- `adaptation_result`: `not_needed`, `successful`, `partial`, `failed`, or `pending`;
- `primary_failure_dimension`: `L`, `S`, `A`, or null.

Use `primary_failure_dimension=A` only when the creator, claim, evidence, or context fit is the primary cause of failure. Do not use it as the denominator for adaptation statistics.

Calculate:

```text
confirmed_required = successful + partial + failed
compensation_hit_rate = successful / confirmed_required
```

Report partial and pending counts separately. Do not assign them fractional success without calibrated evidence.

Output one categorical status:

- `aligned`: no material fit problem or compensation succeeds;
- `conditional`: a material fit issue is partially compensated or requires a bounded change;
- `mismatch`: the current claim or evidence strategy is not credible for this creator;
- `unknown`: media or evidence is insufficient.

Add a short backend explanation and a recommended evidence strategy when status is conditional or mismatch.

The categorical status is derived from the adaptation counts. Any pending or unclear applicability yields `unknown`; failed with no successful compensation yields `mismatch`; partial or mixed failure yields `conditional`; otherwise the status is `aligned`. Do not let an independently entered status contradict the counts.

### Failure ownership examples

- Missing the before or after side of a taught comparison: L.
- Both sides exist, but the creator's actual state cannot support the claim: A.
- Lighting, framing, or editing makes the comparison unreadable: L.
- The creator replaces unsuitable self-proof with credible data or third-party proof: L can be 2 or 3; adaptation is successful; no A failure.

## 7. Confidence

Calculate confidence from decision records, not prose labels.

```text
E = clear, evidence-supported decisions / all scored decisions
M = anchor boundary clarity: 1, 0.5, or 0
R = review-required decisions and pending candidates / all decisions and pending candidates
```

A depth-0 or node-score-0 decision can count as evidence-supported only when `absence_verified=true`, meaning the complete relevant video scope was inspected.

Determine the overall level by the weakest gate:

| Level | Requirements |
|---|---|
| high | E >= 0.85, M = 1, R <= 0.10 |
| medium | E >= 0.65, M >= 0.5, R <= 0.30 |
| low | otherwise |

Cap confidence at medium without anchors. Require human review for low confidence. Show E, M, and R with the level.

Final delivery requires `review_status=completed` whenever the weakest gate is low or a manual-review decision has been resolved. A guided candidate that is pending, or a formula-versus-anchor conflict, keeps the report pending and blocks final delivery. Use `--allow-draft` only for an intermediate report that has not yet passed this gate.
