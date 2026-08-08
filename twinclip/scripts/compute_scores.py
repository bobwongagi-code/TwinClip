#!/usr/bin/env python3
"""Deterministic TwinClip score aggregation.

The semantic model supplies bounded states and evidence decisions. This module
owns the arithmetic that turns those decisions into L, S, T, intervals,
confidence, and multi-reference selection. Keeping these functions separate
from the task compiler makes the model/code boundary testable without asking a
model to perform arithmetic or make a final band decision.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any, Iterable, Mapping, Sequence


LOGIC_CHECK_IDS = (
    "hook_leads_need",
    "points_answer_problem",
    "claims_supported",
    "cta_has_reason",
    "coherent_if_reordered",
)
LOGIC_CHECK_STATES = {"met", "not_met", "unclear"}
NODE_COMPONENT_WEIGHTS = {
    "function": 0.46,
    "elements": 0.38,
    "support": 0.16,
}
LOGIC_WEIGHT = 0.35
NODE_WEIGHT = 0.65
T_WEIGHT_L = 0.70
T_WEIGHT_S = 0.30
SCORE_BOUNDARY_AMBIGUOUS_DISTANCE = 3.0
SCORE_BOUNDARY_CLEAR_DISTANCE = 6.0
LANE_MARGIN_AMBIGUOUS = 0.05
LANE_MARGIN_CLEAR = 0.10
TIER_BOUNDARIES = (
    (0, 19, "未采纳"),
    (20, 39, "表层模仿"),
    (40, 59, "单点机制迁移"),
    (60, 79, "多点结构化迁移"),
    (80, 100, "二次创新"),
)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _bounded(value: Any, low: float, high: float, label: str) -> float:
    number = _number(value, label)
    if not low <= number <= high:
        raise ValueError(f"{label} must be between {low} and {high}")
    return number


def score_band(value: float | None) -> str | None:
    if value is None:
        return None
    rounded = min(100, max(0, math.floor(_number(value, "T") + 0.5)))
    for low, high, label in TIER_BOUNDARIES:
        if low <= rounded <= high:
            return label
    return None


def score_interval(center: float, level: str) -> list[int]:
    width = {"high": 3, "medium": 6, "low": 10}[level]
    return [max(0, math.floor(center - width + 0.5)), min(100, math.floor(center + width + 0.5))]


def score_boundary_distance(value: float | None) -> float | None:
    """Return distance to the nearest score-band boundary."""
    if value is None:
        return None
    score = _bounded(value, 0, 100, "T")
    return min(abs(score - boundary) for boundary, _, _ in TIER_BOUNDARIES[1:])


def score_boundary_clarity(distance: float | None) -> float:
    """Map interval crossing risk to the same 0/0.5/1 clarity scale as anchors."""
    if distance is None:
        return 1.0
    distance = _number(distance, "score_boundary_distance")
    if distance < 0:
        raise ValueError("score_boundary_distance must be non-negative")
    if distance < SCORE_BOUNDARY_AMBIGUOUS_DISTANCE:
        return 0.0
    if distance < SCORE_BOUNDARY_CLEAR_DISTANCE:
        return 0.5
    return 1.0


def lane_margin_clarity(margin: float | None) -> float:
    """Map multi-reference separation to the 0/0.5/1 clarity scale."""
    if margin is None:
        return 1.0
    margin = _number(margin, "lane_margin")
    if margin < 0:
        raise ValueError("lane_margin must be non-negative")
    if margin < LANE_MARGIN_AMBIGUOUS:
        return 0.0
    if margin < LANE_MARGIN_CLEAR:
        return 0.5
    return 1.0


def compute_learning(depths: Sequence[int]) -> tuple[float | None, dict[str, float]]:
    """Return L and mutually exclusive coverage statistics from 0-3 depths."""
    if not depths:
        return None, {}
    checked = []
    for index, depth in enumerate(depths):
        if isinstance(depth, bool) or not isinstance(depth, int) or depth not in range(4):
            raise ValueError(f"depths[{index}] must be an integer from 0 to 3")
        checked.append(depth)
    count = len(checked)
    return (
        100 * sum(checked) / (3 * count),
        {
            "coverage_rate": sum(depth >= 1 for depth in checked) / count,
            "effective_coverage_rate": sum(depth >= 2 for depth in checked) / count,
            "innovation_rate": sum(depth == 3 for depth in checked) / count,
            "surface_share": sum(depth == 1 for depth in checked) / count,
            "surface_error_rate": sum(depth == 1 for depth in checked) / max(sum(depth >= 1 for depth in checked), 1),
        },
    )


def compute_logic_checklist(checks: Mapping[str, int]) -> tuple[float, dict[str, int]]:
    """Return logic coherence on a 0-100 scale from exactly five 0/1 checks."""
    if set(checks) != set(LOGIC_CHECK_IDS):
        missing = sorted(set(LOGIC_CHECK_IDS) - set(checks))
        extra = sorted(set(checks) - set(LOGIC_CHECK_IDS))
        raise ValueError(f"logic checklist must contain exactly five checks; missing={missing}, extra={extra}")
    values: dict[str, int] = {}
    for check_id in LOGIC_CHECK_IDS:
        value = checks[check_id]
        if isinstance(value, bool) or value not in (0, 1):
            raise ValueError(f"logic checklist {check_id} must be 0 or 1")
        values[check_id] = int(value)
    return sum(values.values()) / len(values) * 100, values


def compute_node_dimensions(nodes: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Normalize 0-3 Storyboard node dimensions to 0-100 values."""
    if not nodes:
        return {"function": 0.0, "elements": 0.0, "support": 0.0}
    values: dict[str, list[float]] = {"function": [], "elements": [], "support": []}
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            raise ValueError(f"nodes[{index}] must be an object")
        for key in ("function_score", "support_score"):
            values[key.removesuffix("_score")].append(_bounded(node.get(key), 0, 3, f"nodes[{index}].{key}") / 3 * 100)
        element = node.get("element_score")
        if element is not None:
            values["elements"].append(_bounded(element, 0, 3, f"nodes[{index}].element_score") / 3 * 100)

    dimensions = {
        key: sum(items) / len(items) if items else 0.0
        for key, items in values.items()
    }
    return dimensions


def compute_storyboard(
    nodes: Sequence[Mapping[str, Any]],
    logic_checks: Mapping[str, int],
    *,
    s_weights: Mapping[str, float] | None = None,
) -> tuple[float, dict[str, float]]:
    """Return S using node function/element/support plus five logic checks.

    The default hierarchy is 35% logic and 65% node score; the node score is
    46/38/16. A locked calibrated flattened weight map may be supplied by the
    run manifest. It is still applied only to the deterministic dimensions.
    """
    logic_coherence, _ = compute_logic_checklist(logic_checks)
    if s_weights is None:
        s_weights = {
            "logic": LOGIC_WEIGHT,
            "function": NODE_WEIGHT * NODE_COMPONENT_WEIGHTS["function"],
            "elements": NODE_WEIGHT * NODE_COMPONENT_WEIGHTS["elements"],
            "support": NODE_WEIGHT * NODE_COMPONENT_WEIGHTS["support"],
        }
        if not any(node.get("element_score") is not None for node in nodes):
            s_weights = {
                "logic": LOGIC_WEIGHT,
                "function": NODE_WEIGHT * NODE_COMPONENT_WEIGHTS["function"]
                / (NODE_COMPONENT_WEIGHTS["function"] + NODE_COMPONENT_WEIGHTS["support"]),
                "elements": 0.0,
                "support": NODE_WEIGHT * NODE_COMPONENT_WEIGHTS["support"]
                / (NODE_COMPONENT_WEIGHTS["function"] + NODE_COMPONENT_WEIGHTS["support"]),
            }
    required = ("logic", "function", "elements", "support")
    if set(s_weights) != set(required):
        raise ValueError(f"s_weights must contain exactly {required}")
    normalized_weights = {key: _bounded(s_weights[key], 0, 1, f"s_weights.{key}") for key in required}
    if not math.isclose(sum(normalized_weights.values()), 1.0, abs_tol=1e-6):
        raise ValueError("s_weights must sum to 1")
    dimensions = compute_node_dimensions(nodes)
    node_weight_total = sum(normalized_weights[key] for key in ("function", "elements", "support"))
    if node_weight_total <= 0:
        raise ValueError("at least one Storyboard node dimension must remain active")
    node_average = sum(
        dimensions[key] * normalized_weights[key] / node_weight_total
        for key in ("function", "elements", "support")
    )
    dimension_values = {
        "logic": logic_coherence,
        **dimensions,
    }
    score = sum(dimension_values[key] * normalized_weights[key] for key in required)
    score = min(100.0, max(0.0, score))
    return score, {
        "logic_coherence": logic_coherence,
        "node_average": node_average,
        "function": dimensions["function"],
        "elements": dimensions["elements"],
        "support": dimensions["support"],
    }


def compute_total(l_score: float, s_score: float, *, l_weight: float = T_WEIGHT_L, s_weight: float = T_WEIGHT_S) -> float:
    l_score = _bounded(l_score, 0, 100, "L")
    s_score = _bounded(s_score, 0, 100, "S")
    l_weight = _bounded(l_weight, 0, 1, "l_weight")
    s_weight = _bounded(s_weight, 0, 1, "s_weight")
    if not math.isclose(l_weight + s_weight, 1.0, abs_tol=1e-6):
        raise ValueError("l_weight and s_weight must sum to 1")
    return min(100.0, max(0.0, l_score * l_weight + s_score * s_weight))


def select_lane(
    lane_summaries: Mapping[str, Mapping[str, Any]],
    *,
    margin_threshold: float = 0.05,
) -> dict[str, Any]:
    """Select a reference lane by coverage, T, then declared mapping order.

    The margin uses the first criterion that separates the winner from the
    runner-up. Coverage is already a 0-1 ratio; T is normalized to 0-1 for a
    comparable review threshold. A narrow margin is surfaced as a review flag,
    while selection itself remains deterministic.
    """
    if not lane_summaries:
        raise ValueError("at least one reference lane is required")
    ordered_ids = list(lane_summaries)
    ranked = sorted(
        ordered_ids,
        key=lambda lane_id: (
            -float(lane_summaries[lane_id]["effective_coverage_rate"]),
            -float(lane_summaries[lane_id]["T_center"]),
            ordered_ids.index(lane_id),
        ),
    )
    chosen = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    effective_gap = None
    t_gap = None
    margin = None
    margin_basis = None
    if runner_up is not None:
        effective_gap = abs(
            float(lane_summaries[chosen]["effective_coverage_rate"])
            - float(lane_summaries[runner_up]["effective_coverage_rate"])
        )
        t_gap = abs(float(lane_summaries[chosen]["T_center"]) - float(lane_summaries[runner_up]["T_center"])) / 100
        if effective_gap > 1e-9:
            margin, margin_basis = effective_gap, "effective_coverage_rate"
        elif t_gap > 1e-9:
            margin, margin_basis = t_gap, "T_center"
        else:
            margin, margin_basis = 0.0, "declared_lane_order"
    return {
        "chosen_lane": chosen,
        "runner_up_lane": runner_up,
        "margin": margin,
        "margin_basis": margin_basis,
        "effective_coverage_gap": effective_gap,
        "T_gap_normalized": t_gap,
        "needs_manual_review": margin is not None and margin < margin_threshold,
        "all_lanes": ranked,
    }


def confidence_from_decisions(
    *,
    clear_supported: int,
    decision_count: int,
    manual_review_count: int,
    pending_candidates: int,
    has_anchors: bool,
    boundary_clarity: float,
    score_boundary_distance_value: float | None = None,
    lane_margin: float | None = None,
) -> dict[str, Any]:
    if decision_count < 0 or clear_supported < 0 or manual_review_count < 0 or pending_candidates < 0:
        raise ValueError("confidence counts must be non-negative")
    if clear_supported > decision_count or manual_review_count > decision_count:
        raise ValueError("confidence counts exceed decision count")
    if boundary_clarity not in {0, 0.5, 1}:
        raise ValueError("boundary_clarity must be 0, 0.5, or 1")
    e_value = clear_supported / decision_count if decision_count else 0.0
    denominator = decision_count + pending_candidates
    r_value = (manual_review_count + pending_candidates) / denominator if denominator else 0.0
    m_components = {
        "anchor_boundary": boundary_clarity,
        "score_boundary": score_boundary_clarity(score_boundary_distance_value),
        "lane_margin": lane_margin_clarity(lane_margin),
    }
    m_value = min(m_components.values())
    if e_value >= 0.85 and m_value == 1 and r_value <= 0.10:
        level = "high"
    elif e_value >= 0.65 and m_value >= 0.5 and r_value <= 0.30:
        level = "medium"
    else:
        level = "low"
    if not has_anchors and level == "high":
        level = "medium"
    return {
        "E": round(e_value, 6),
        "M": m_value,
        "M_components": m_components,
        "R": round(r_value, 6),
        "level": level,
    }


def compute(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Small CLI/library facade for deterministic synthetic or audit inputs."""
    depths = [item["depth"] for item in payload.get("teaching_points", [])]
    nodes = payload.get("nodes", [])
    logic_checks = payload.get("logic_checklist", {})
    l_score, l_stats = compute_learning(depths)
    s_score, s_stats = compute_storyboard(nodes, logic_checks, s_weights=payload.get("s_weights"))
    t_score = compute_total(l_score, s_score) if l_score is not None else None
    confidence = confidence_from_decisions(
        clear_supported=int(payload.get("clear_supported", payload.get("evidence_with_timestamp", 0))),
        decision_count=int(payload.get("decision_count", payload.get("evidence_total", 0))),
        manual_review_count=int(payload.get("manual_review_count", payload.get("review_flag_count", 0))),
        pending_candidates=int(payload.get("pending_candidates", 0)),
        has_anchors=payload.get("anchor_boundary_clarity") is not None,
        boundary_clarity=payload.get("anchor_boundary_clarity", 0.5),
        score_boundary_distance_value=payload.get("score_boundary_distance"),
        lane_margin=payload.get("lane_margin"),
    )
    return {
        "L": round(l_score, 2) if l_score is not None else None,
        "L_stats": l_stats,
        "S": round(s_score, 2),
        "S_stats": s_stats,
        "T": round(t_score, 2) if t_score is not None else None,
        "band": score_band(t_score),
        "confidence": confidence,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: compute_scores.py INPUT.json")
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        print(json.dumps(compute(json.load(handle)), ensure_ascii=False, indent=2))
