#!/usr/bin/env python3
"""Tests for deterministic score aggregation and lane selection."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "twinclip" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from compute_scores import (  # noqa: E402
    LOGIC_CHECK_IDS,
    compute_logic_checklist,
    compute_storyboard,
    confidence_from_decisions,
    select_lane,
)


class ComputeScoresTests(unittest.TestCase):
    def test_storyboard_logic_is_five_atomic_checks(self) -> None:
        nodes = [{"function_score": 3, "element_score": 3, "support_score": 3}]
        checks = {check_id: 1 for check_id in LOGIC_CHECK_IDS}
        full_score, full_stats = compute_storyboard(nodes, checks)
        self.assertAlmostEqual(full_score, 100.0)
        self.assertEqual(full_stats["logic_coherence"], 100.0)

        checks["claims_supported"] = 0
        partial_score, partial_stats = compute_storyboard(nodes, checks)
        self.assertEqual(partial_stats["logic_coherence"], 80.0)
        self.assertAlmostEqual(partial_score, 93.0)

    def test_logic_checklist_rejects_missing_or_non_binary_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly five"):
            compute_logic_checklist({"hook_leads_need": 1})
        checks = {check_id: 1 for check_id in LOGIC_CHECK_IDS}
        checks["cta_has_reason"] = 2
        with self.assertRaisesRegex(ValueError, "must be 0 or 1"):
            compute_logic_checklist(checks)

    def test_node_breakdown_uses_locked_node_weights(self) -> None:
        nodes = [
            {"function_score": 3, "element_score": 0, "support_score": 0},
            {"function_score": 0, "element_score": 3, "support_score": 0},
        ]
        checks = {check_id: 1 for check_id in LOGIC_CHECK_IDS}
        score, stats = compute_storyboard(
            nodes,
            checks,
            s_weights={"logic": 0.35, "function": 0.10, "elements": 0.50, "support": 0.05},
        )
        self.assertAlmostEqual(stats["node_average"], 46.153846, places=5)
        self.assertAlmostEqual(score, 65.0)

    def test_lane_selection_flags_a_narrow_coverage_margin(self) -> None:
        result = select_lane({
            "REF-A": {"effective_coverage_rate": 0.60, "T_center": 62},
            "REF-B": {"effective_coverage_rate": 0.62, "T_center": 55},
        })
        self.assertEqual(result["chosen_lane"], "REF-B")
        self.assertEqual(result["margin_basis"], "effective_coverage_rate")
        self.assertTrue(result["needs_manual_review"])

    def test_confidence_uses_the_weakest_auditable_component(self) -> None:
        result = confidence_from_decisions(
            clear_supported=10,
            decision_count=10,
            manual_review_count=0,
            pending_candidates=0,
            has_anchors=True,
            boundary_clarity=1,
            score_boundary_distance_value=2.0,
            lane_margin=0.01,
        )
        self.assertEqual(result["M"], 0.0)
        self.assertEqual(result["M_components"], {
            "anchor_boundary": 1,
            "score_boundary": 0.0,
            "lane_margin": 0.0,
        })
        self.assertEqual(result["level"], "low")


if __name__ == "__main__":
    unittest.main()
