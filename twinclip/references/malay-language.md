# Malay and Manglish Content Handling

Read this reference before processing any breakdown video, Storyboard, or creator
video that contains Malaysian Malay or Malay-English code-switching (Manglish or
rojak). Detect the language profile of each source independently; do not assume
that all files in a batch use the same language.

Language handling is an evidence-layer requirement. It does not change the L/S/T
scoring formulas or turn language fluency into a content score.

## ASR and OCR

- Prefer a transcription capability that supports Malaysian Malay and
  code-switching. Use the locally available VidLingo capability when available;
  do not download or install Whisper just for this workflow.
- Confirm Malaysian Malay (`ms-MY`) versus Indonesian (`id-ID`) before choosing
  a language profile. Similar vocabulary does not make the two varieties
  interchangeable.
- Preserve informal speech and spelling, including forms such as `je`, `kan`, and
  `sbb`. Do not normalize them away before semantic review.
- Keep ASR transcript and burned-in on-screen text as separate evidence channels.
  They are complementary and are often not word-for-word aligned.
- For short-video captions, treat decorative fonts, outlines, stickers, and emoji
  as OCR risk. Send low-confidence OCR for human correction and use Malaysian
  Malay vocabulary during correction.
- Never replace an unavailable channel with a guessed translation. Keep it as
  `unknown` and disclose the missing channel.

## Original-first semantic review

Run blind extraction and reference matching against the original Malay or mixed
text. Do not translate the entire source before deciding what a segment does:
translation can erase particles, slang, indirectness, or local trust cues before
the model evaluates them.

Keep `transcript` and `onscreen_text` in the evidence record as the original
transcription/OCR text. The v1.6 JSON schema has no free-form
`evidence_description` field; in the rendered borrowing/evidence table's
description column, include both pieces in the same entry:

```text
原文: <Malay or mixed-language excerpt>
简译: <Chinese translation for the business reader>
```

Every evidence record also declares the observation channels used (`visual`,
`onscreen_text`, and/or `voiceover`). The Chinese translation is a reader aid only. It must not replace the original evidence or become the basis of blind matching.

## Local persuasion signals

Treat `persuasion_element` as open to new local trust signals during blind
extraction. The model may describe a certification mark, local proof convention,
community signal, or other mechanism not present in an existing taxonomy.

Adding a newly observed persuasion element does not automatically create an L
teaching point. It enters the reference graph only after the breakdown explicitly
teaches it or the user approves it for the reference bundle. This keeps local
discovery open without inflating the teaching-point denominator.

## Confidence and human review

Separate language-quality uncertainty from match uncertainty:

- weak transcription, OCR, or Malay/Manglish comprehension calls for better
  evidence or a language-qualified reviewer;
- a clear original-language observation that still may or may not match a
  teaching point goes to `manual_pending`.

Never relax L/S thresholds because a model is weak in the language. For anchor
calibration and continuous QA, the confirming reviewer must be fluent enough to
read Malay captions and understand the spoken content. Without that reviewer,
leave calibration unresolved and disclose the limitation.

## Starter glossary

| Malay | Meaning | Typical use |
|---|---|---|
| `komedo` | blackheads | effect proof |
| `jerawat` | acne/pimples | pain-point hook, effect proof |
| `lembut` | soft/gentle | usage experience |
| `cerah` | bright/clear | result preview, effect proof |
| `minyak` | oil, especially oily skin | pain-point hook |
| `bilas` | rinse | usage demonstration |
| `tiub` | tube packaging | product evidence |
| `buih` | foam | usage demonstration |

Treat this glossary as a starting point, not a closed dictionary. Expand it only
from reviewed material and keep category-specific additions attributable to the
source bundle.
