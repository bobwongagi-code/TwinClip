# TwinClip 重复运行稳定性复盘

实验 `twinclip-stability-PAPAFEEL-20260806-multi-ref-20260807` 共观察 80 次运行，覆盖 16 条视频。

## 先看结论

本报告不把 5 次结果取平均作为最终分数。均值和中位数只用于描述分布位置；验收和改进依据是极差、总体标准差、平均绝对偏差、两两平均绝对差、档位/标杆切换和逐点不稳定性。

T 的全体运行分布为：中位数 52.96，极差 77.29，总体标准差 17.52，平均绝对偏差 12.76，两两平均绝对差 19.41。
按方差分解，重复判断的组内方差占当前分解方差的 4.6%；这个比例用于判断随机判断噪声相对视频间差异的大小，不用于生成新的总分。
另有 11 条视频出现较高的完整判断指纹重复或连续重复；这部分不能直接当作独立采样后的稳定性。

## 波动最大的样本

| 视频 | T 中位数 | T 极差 | T 总体标准差 | T 两两平均绝对差 | 档位切换率 | 标杆切换率 | 标杆 margin 中位数 | 证据集合两两 Jaccard 中位数 | 精确判断重复率 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `aliceong417` | 87.44 | 18.49 | 8.54 | 10.65 | 25.0% | 25.0% | 0.125 | 0.88 | 40.0% |
| `mai199_7` | 50.47 | 15.85 | 7.12 | 9.51 | 25.0% | 50.0% | 0.071 | 0.78 | 40.0% |
| `sahabanu_79_1` | 58.94 | 10.96 | 3.54 | 4.38 | 25.0% | 25.0% | 0.089 | 1.00 | 40.0% |
| `nizzahhhhh` | 36.69 | 10.69 | 4.18 | 5.11 | 25.0% | 50.0% | 0.071 | 0.88 | 40.0% |
| `daddycengey69` | 46.26 | 10.12 | 3.21 | 4.05 | 0.0% | 0.0% | 0.054 | 0.86 | 20.0% |
| `firdaus.janon` | 10.15 | 9.21 | 3.68 | 3.68 | 0.0% | 0.0% | 0.018 | 1.00 | 20.0% |
| `en_tomi` | 52.57 | 8.90 | 3.31 | 4.22 | 0.0% | 0.0% | 0.214 | 0.88 | 20.0% |
| `sahabanu_79` | 74.55 | 8.67 | 4.08 | 5.06 | 0.0% | 0.0% | 0.500 | 1.00 | 40.0% |
| `inaamalik` | 47.96 | 8.14 | 2.61 | 3.26 | 0.0% | 0.0% | 0.071 | 1.00 | 40.0% |
| `ftiha.sd` | 56.60 | 6.94 | 3.36 | 4.13 | 0.0% | 0.0% | 0.607 | 1.00 | 20.0% |

## 根因信号

- **high / reference_lane_selection_drift**：{'videos': [('aliceong417', 0.25, 0.125), ('glowingfish313', 0.5, 0.071429), ('mai199_7', 0.5, 0.071429), ('nizzahhhhh', 0.5, 0.071429), ('noorshafiraa', 0.25, 0.089286), ('sahabanu_79_1', 0.25, 0.089286)]}
  建议：Make effective-coverage, T, and declared lane order a deterministic post-processing rule; inspect cases with a narrow lane margin or a true tie.
- **high / execution_context_or_duplicate_trajectory**：{'videos': [('aliceong417', 3, 0.4, 0.5, {'282b8479f354cb12': 3, '5c666e1aff598582': 1, 'fbe796aa64c1026b': 1}), ('ayushah3', 3, 0.4, 0.5, {'1005bbb73bf5c3cc': 2, '33b9c67334de992c': 2, 'cc2d5a9c1d36928c': 1}), ('glowingfish313', 3, 0.4, 0.5, {'b283a7d9f3b7a03a': 1, 'b90f0a883f89a6a4': 3, 'e03189fe9c12f17f': 1}), ('inaamalik', 3, 0.4, 0.5, {'169587ff8ba8468a': 3, 'bcc16d6630e67b3c': 1, 'e5dce3b6c7b3d368': 1}), ('mai199_7', 3, 0.4, 0.5, {'0c239847f885f158': 2, '6ca7675844d73d51': 1, 'eea90236b42f78a3': 2}), ('mia_rich', 2, 0.6, 0.75, {'05f6f4eb22a71d0e': 2, 'cc5a96dd87f33654': 3}), ('nizzahhhhh', 3, 0.4, 0.5, {'5656a41a46c16376': 1, '61840b65cb299794': 3, 'd3f85eb438d9c48c': 1}), ('noorshafiraa', 3, 0.4, 0.5, {'0e67a719da0fca6d': 1, '1fe88097d7b6dddc': 3, 'f09680962a5eefbb': 1}), ('sahabanu_79', 3, 0.4, 0.5, {'41cf3ee12286d4b1': 1, '708c160556f6f606': 3, '7f2518fb65fc0b6b': 1}), ('sahabanu_79_1', 3, 0.4, 0.5, {'2c4ed883bf337080': 1, '4d0df412f0590399': 3, '560f298e36bc0290': 1}), ('sifu.yusuf', 2, 0.6, 0.75, {'8b55a9e1e4eccc07': 1, 'ec4373fd5c6f8393': 4})]}
  建议：Treat prompt-reset continuations as non-independent until proven otherwise; rerun with process- or thread-isolated contexts, immutable run identity checks, and a fresh worker for every replicate.
- **high / band_boundary_amplification**：{'videos': [('ftiha.sd', 6.938096, 3.395238), ('mai199_7', 15.851905, 4.21), ('nizzahhhhh', 10.690476, 3.309524), ('sahabanu_79_1', 10.961905, 1.057143)]}
  建议：Keep raw score distribution and boundary distance visible; do not use a single band as the only acceptance output.
- **high / rubric_threshold_or_semantic_judgment_drift**：{'videos': [('aliceong417', 18.49, 0.875), ('daddycengey69', 10.122381, 0.857143), ('en_tomi', 8.895238, 0.875), ('firdaus.janon', 9.209524, 1.0), ('ftiha.sd', 6.938096, 1.0), ('inaamalik', 8.142381, 1.0), ('nizzahhhhh', 10.690476, 0.875), ('sahabanu_79', 8.67, 1.0), ('sahabanu_79_1', 10.961905, 1.0)]}
  建议：Create contrastive boundary examples for the unstable points and require a short rubric reason tied to minimum evidence.
- **high / confidence_output_collapse**：{'confidence_levels': ['medium'], 'component_distinct_counts': {'E': 2, 'M': 1, 'R': 1}}
  建议：Derive confidence from evidence completeness, lane margin, boundary distance, language quality, and manual-review flags; reject a default medium label when its components do not vary.
- **medium / evidence_selection_drift**：{'videos': [('mai199_7', 0.777778)]}
  建议：Add an evidence-linking checklist and stable evidence IDs; keep blind evidence fixed before changing scoring thresholds.
- **medium / teaching_point_threshold_drift**：{'unstable_video_point_count': 31, 'examples': ['ayushah3:B-TP06', 'daddycengey69:B-TP02', 'daddycengey69:B-TP04', 'daddycengey69:B-TP05', 'daddycengey69:B-TP06', 'en_tomi:B-TP06', 'firdaus.janon:B-TP03', 'firdaus.janon:B-TP05', 'ftiha.sd:B-TP02', 'ftiha.sd:B-TP04', 'ftiha.sd:B-TP05', 'ftiha.sd:B-TP07', 'glowingfish313:B-TP05', 'helloxoan2:B-TP01', 'helloxoan2:B-TP02', 'helloxoan2:B-TP03', 'helloxoan2:B-TP04', 'helloxoan2:B-TP05', 'helloxoan2:B-TP07', 'inaamalik:B-TP05']}
  建议：Calibrate each 0-3 teaching-point boundary with positive/negative contrast examples; do not tune only on T.
- **confounder / fixed_evidence_quality_or_language_confounder**：{'evidence_records': 138, 'unknown_channel_count': 138, 'unknown_channel_rate': 0.5, 'interpretation': 'Input/evidence quality is a separate confounder; it is not a model-randomness finding.'}
  建议：Improve VidLingo ASR/OCR or Malay/Manglish review separately before attributing the variation to semantic judgment.

## 解释边界

本轮固定了观察证据，未重新跑 ASR/OCR：`frozen_legacy_multimodal_observation`。因此本轮测到的主要是语义匹配、量表阈值、标杆选择和置信度判断的波动；不能把结果直接解释成端到端 ASR/OCR 稳定性。
执行上下文审计见同目录的 `execution-audit.json`。由于 worker 线程曾被复用，统计独立性目前标记为 `unverified_mixed_context`；这比把所有 80 次直接视为独立样本更保守。
分层根因、修复优先级和修复后验收条件见同目录的 `POST-RUN-IMPROVEMENTS.md`；本轮没有把修复后的结果伪装成已验证。
证据质量检查发现未知通道比例为 0.5。语言或证据质量是混杂因素，需要单独实验验证，不能用放宽评分阈值解决。

## 下一步修复顺序

1. 先修复所有公式残差、标杆选择和重复扣分等可确定性问题。
2. 对极差大且证据集合稳定的教学点，补充 0-3 分对照锚点和最小证据反例。
3. 对证据集合本身波动的样本，先修盲提取和证据 ID 链接，再评估评分波动。
4. 修复后保留未参与调参的留出视频，重新跑同样的 5 次设计；比较分布收窄和档位切换率，而不是比较均值是否更好看。

原始运行记录见同目录的 `raw_runs` JSON；每个运行均保留 `run_id`、轮次、视频、证据集合、L/S/T、档位、置信度和逐点判断。
