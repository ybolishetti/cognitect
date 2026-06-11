"""
Cassowary constraint solver for floor plan layout.

Uses kiwisolver to resolve room dimensions and positions.

SLA: <5s
Architecture rule: this module NEVER calls the LLM. It only consumes FloorPlanState.

## Two-pass area approximation

kiwisolver is a linear constraint solver. Area = width * height is non-linear.
We approximate via:
  Pass 1: sqrt(target_area) → edit variable for side length of a "square equivalent"
           then let aspect_ratio split it into width/height
  Pass 2: check actual area; if ±15% of target, iterate up to MAX_ITERATIONS,
           scaling the edit variable and re-solving.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Optional

import kiwisolver

from ..intent_parser.schemas import ConstraintSpec, FloorPlanState
from .graph import CoordinateGraph, RoomNode, WallEdge
from .layout import RoomLayoutEngine

logger = logging.getLogger(__name__)

SLA_SECONDS = 5.0
MAX_ITERATIONS = 10
AREA_TOLERANCE = 0.15  # 15% tolerance on area targets
MIN_ROOM_DIM_FT = 4.0  # no room dimension smaller than 4 ft
MAX_PLAN_DIM_FT = 200.0  # plan bounding box cap


class ConstraintUnsatisfiableError(Exception):
    """Raised when no valid layout can be found after max iterations."""

    def __init__(self, message: str, iterations: int = 0):
        super().__init__(message)
        self.iterations = iterations


# ── Strength mapping ─────────────────────────────────────────────────────────

_STRENGTH_MAP = {
    "required": kiwisolver.strength.required,
    "strong": kiwisolver.strength.strong,
    "medium": kiwisolver.strength.medium,
    "weak": kiwisolver.strength.weak,
}


def _strength(name: str) -> float:
    return _STRENGTH_MAP.get(name, kiwisolver.strength.medium)


# ── Solver ───────────────────────────────────────────────────────────────────

class ConstraintSolver:
    """
    Resolves room dimensions and positions for a FloorPlanState.

    Returns a coordinate matrix: {room_id: {"x", "y", "width", "height"}} in feet.
    """

    def solve(
        self,
        plan_state: FloorPlanState,
        prior_matrix: dict[str, dict[str, float]] | None = None,
        mutated_rooms: set[str] | None = None,
    ) -> dict[str, dict[str, float]]:
        """
        Main entry point.

        Args:
            plan_state: Current FloorPlanState with rooms and constraints.
            prior_matrix: Previous coordinate matrix for positional continuity.
            mutated_rooms: Room IDs touched since last solve (free to reposition).

        Returns:
            Coordinate matrix dict: {room_id: {x, y, width, height}}.

        Raises:
            ConstraintUnsatisfiableError: If no solution found after MAX_ITERATIONS.
        """
        t0 = time.perf_counter()

        if not plan_state.rooms:
            return {}

        graph = self._build_graph(plan_state, prior_matrix, mutated_rooms)
        matrix = self._solve_with_iterations(graph, plan_state)

        elapsed = time.perf_counter() - t0
        if elapsed > SLA_SECONDS:
            logger.warning("SLA violation: constraint solve took %.2fs (target: %.1fs)", elapsed, SLA_SECONDS)
        else:
            logger.debug("Constraint solve completed in %.3fs", elapsed)

        return matrix

    # ── Graph construction ───────────────────────────────────────────────────

    def _build_graph(
        self,
        plan_state: FloorPlanState,
        prior_matrix: dict[str, dict[str, float]] | None = None,
        mutated_rooms: set[str] | None = None,
    ) -> CoordinateGraph:
        mutated = mutated_rooms or set()
        graph = CoordinateGraph()

        for room_id, spec in plan_state.rooms.items():
            node = RoomNode(
                room_id=room_id,
                name=spec.name,
                room_type=spec.room_type,
                target_area_sqft=spec.area_sqft,
                min_area_sqft=spec.min_area_sqft,
                max_area_sqft=spec.max_area_sqft,
                aspect_ratio=spec.aspect_ratio,
                adjacency=list(spec.adjacency_requirements),
            )

            # Apply constraint overrides
            for cs in plan_state.constraints:
                if cs.room_id == room_id:
                    if cs.constraint_type == "min_area":
                        node.min_area_sqft = float(cs.value)
                    elif cs.constraint_type == "max_area":
                        node.max_area_sqft = float(cs.value)
                    elif cs.constraint_type == "aspect_ratio":
                        node.aspect_ratio = float(cs.value)

            graph.add_room(node)

        # Edges from connections
        for conn in plan_state.connections:
            graph.add_edge(WallEdge(
                room_a_id=conn.room_a_id,
                room_b_id=conn.room_b_id,
            ))

        # Edges from adjacency_requirements in RoomSpec
        for room_id, spec in plan_state.rooms.items():
            for adj_name in spec.adjacency_requirements:
                adj_id = self._find_room_id_by_name(adj_name, plan_state)
                if adj_id:
                    graph.add_edge(WallEdge(room_a_id=room_id, room_b_id=adj_id))

        # Position locks from prior matrix
        if prior_matrix:
            for room_id, coords in prior_matrix.items():
                if room_id not in graph.nodes:
                    continue
                if room_id in mutated:
                    continue
                graph.nodes[room_id].pinned_position = (coords["x"], coords["y"])

            # Downgrade neighbors of mutated rooms from required to strong
            for room_id in graph.nodes:
                node = graph.nodes[room_id]
                if node.pinned_position is None:
                    continue
                neighbors = graph.adjacent_rooms(room_id)
                if any(n in mutated for n in neighbors):
                    node.is_flexible_pin = True

        return graph

    def _find_room_id_by_name(self, name: str, plan_state: FloorPlanState) -> Optional[str]:
        """Find room_id by name (case-insensitive) or by id."""
        name_lower = name.lower().replace(" ", "_")
        for rid, spec in plan_state.rooms.items():
            if rid == name_lower or spec.name.lower() == name.lower():
                return rid
        return None

    # ── Two-pass solving ─────────────────────────────────────────────────────

    def _solve_with_iterations(
        self, graph: CoordinateGraph, plan_state: FloorPlanState
    ) -> dict[str, dict[str, float]]:
        """
        Run the solver, iterating up to MAX_ITERATIONS to converge on area targets.
        """
        room_ids = list(graph.nodes.keys())
        n = len(room_ids)

        # Scale factors for the iterative area approximation (start at 1.0)
        scale_factors: dict[str, float] = {rid: 1.0 for rid in room_ids}

        matrix: dict[str, dict[str, float]] = {}

        for iteration in range(MAX_ITERATIONS):
            matrix = self._run_solver_pass(graph, room_ids, scale_factors)

            # Check area convergence
            all_ok = True
            for rid in room_ids:
                node = graph.nodes[rid]
                if node.target_area_sqft is None:
                    continue
                coords = matrix[rid]
                actual_area = coords["width"] * coords["height"]
                target = node.target_area_sqft
                error = abs(actual_area - target) / target
                if error > AREA_TOLERANCE:
                    all_ok = False
                    # Adjust scale factor proportionally
                    scale_factors[rid] *= math.sqrt(target / actual_area)
                    logger.debug(
                        "Iter %d: room %s area=%.1f target=%.1f err=%.1f%% → scale=%.3f",
                        iteration, rid, actual_area, target, error * 100, scale_factors[rid],
                    )

            if all_ok:
                logger.debug("Converged in %d iteration(s)", iteration + 1)
                break
        else:
            # Check if result is at least within 30% (acceptable with warning)
            for rid in room_ids:
                node = graph.nodes[rid]
                if node.target_area_sqft is None:
                    continue
                coords = matrix[rid]
                actual_area = coords["width"] * coords["height"]
                target = node.target_area_sqft
                error = abs(actual_area - target) / target
                if error > 0.30:
                    raise ConstraintUnsatisfiableError(
                        f"Room {rid}: area target {target} sqft not satisfiable "
                        f"(got {actual_area:.1f} sqft after {MAX_ITERATIONS} iterations)",
                        iterations=MAX_ITERATIONS,
                    )

        return matrix

    def _run_solver_pass(
        self,
        graph: CoordinateGraph,
        room_ids: list[str],
        scale_factors: dict[str, float],
    ) -> dict[str, dict[str, float]]:
        """
        One pass of the kiwisolver.

        Layout strategy: pack rooms left-to-right in a row.
        Rooms with adjacency edges are placed next to each other.
        """
        solver = kiwisolver.Solver()

        # Create variables for each room: x, y, width, height
        vars_: dict[str, dict[str, kiwisolver.Variable]] = {}
        for rid in room_ids:
            vars_[rid] = {
                "x": kiwisolver.Variable(f"{rid}_x"),
                "y": kiwisolver.Variable(f"{rid}_y"),
                "w": kiwisolver.Variable(f"{rid}_w"),
                "h": kiwisolver.Variable(f"{rid}_h"),
            }

        # ── Basic constraints ────────────────────────────────────────────────

        for rid in room_ids:
            node = graph.nodes[rid]
            v = vars_[rid]

            # Non-negativity
            solver.addConstraint((v["x"] >= 0.0) | "required")
            solver.addConstraint((v["y"] >= 0.0) | "required")

            # Minimum dimensions
            solver.addConstraint((v["w"] >= MIN_ROOM_DIM_FT) | "required")
            solver.addConstraint((v["h"] >= MIN_ROOM_DIM_FT) | "required")

            # Maximum bounding box
            solver.addConstraint((v["x"] + v["w"] <= MAX_PLAN_DIM_FT) | "required")
            solver.addConstraint((v["y"] + v["h"] <= MAX_PLAN_DIM_FT) | "required")

        # ── Area constraints via edit variables ──────────────────────────────

        for rid in room_ids:
            node = graph.nodes[rid]
            v = vars_[rid]
            sf = scale_factors[rid]

            # Determine target dimension from area
            target_area = node.target_area_sqft
            if target_area:
                aspect = node.aspect_ratio or 1.0
                # width = sqrt(target_area * aspect), height = sqrt(target_area / aspect)
                target_w = math.sqrt(target_area * aspect) * sf
                target_h = math.sqrt(target_area / aspect) * sf
                target_w = max(target_w, MIN_ROOM_DIM_FT)
                target_h = max(target_h, MIN_ROOM_DIM_FT)
                solver.addConstraint((v["w"] == target_w) | "strong")
                solver.addConstraint((v["h"] == target_h) | "strong")

            # Min area constraint (convert to min dimension)
            if node.min_area_sqft:
                aspect = node.aspect_ratio or 1.0
                min_w = math.sqrt(node.min_area_sqft * aspect)
                min_h = math.sqrt(node.min_area_sqft / aspect)
                solver.addConstraint((v["w"] >= min_w) | "strong")
                solver.addConstraint((v["h"] >= min_h) | "strong")

            # Max area constraint
            if node.max_area_sqft:
                aspect = node.aspect_ratio or 1.0
                max_w = math.sqrt(node.max_area_sqft * aspect)
                max_h = math.sqrt(node.max_area_sqft / aspect)
                solver.addConstraint((v["w"] <= max_w) | "strong")
                solver.addConstraint((v["h"] <= max_h) | "strong")

            # Aspect ratio (hard if provided, medium otherwise)
            if node.aspect_ratio:
                # w = aspect_ratio * h  →  w - aspect_ratio * h = 0
                # kiwisolver doesn't support multiplication of two variables,
                # so we encode as w == aspect_ratio * h using a constant
                # This is a linearized approximation updated each iteration.
                solver.addConstraint(
                    (v["w"] == node.aspect_ratio * v["h"]) | "strong"
                )
            else:
                # Soft square-ish preference
                solver.addConstraint((v["w"] == v["h"]) | "weak")

        # ── Layout: 2D placement via RoomLayoutEngine ────────────────────────

        pinned = {
            rid: graph.nodes[rid].pinned_position
            for rid in room_ids
            if graph.nodes[rid].pinned_position is not None
        }
        placement = RoomLayoutEngine().compute_placement(graph, room_ids, pinned=pinned)

        for rid in room_ids:
            v = vars_[rid]
            node = graph.nodes[rid]
            px, py = placement.positions[rid]
            pin_strength = "strong" if node.is_flexible_pin else "required"

            if node.pinned_position is not None:
                solver.addConstraint((v["x"] == px) | pin_strength)
                solver.addConstraint((v["y"] == py) | pin_strength)
            else:
                solver.addConstraint((v["x"] == px) | "strong")
                solver.addConstraint((v["y"] == py) | "strong")
                solver.addConstraint((v["x"] >= 0.0) | "required")
                solver.addConstraint((v["y"] >= 0.0) | "required")

        # ── Solve ────────────────────────────────────────────────────────────
        solver.updateVariables()

        # Extract results
        matrix: dict[str, dict[str, float]] = {}
        for rid in room_ids:
            v = vars_[rid]
            matrix[rid] = {
                "x": round(v["x"].value(), 3),
                "y": round(v["y"].value(), 3),
                "width": round(v["w"].value(), 3),
                "height": round(v["h"].value(), 3),
            }

        return matrix

    def _topological_order(self, room_ids: list[str], graph: CoordinateGraph) -> list[str]:
        """
        Order rooms so adjacent pairs are placed next to each other.
        Simple greedy approach: BFS from the first room, following adjacency edges.
        """
        if not room_ids:
            return []

        visited: set[str] = set()
        result: list[str] = []
        queue: list[str] = [room_ids[0]]

        while queue:
            rid = queue.pop(0)
            if rid in visited:
                continue
            visited.add(rid)
            result.append(rid)
            for adj in graph.adjacent_rooms(rid):
                if adj not in visited and adj in graph.nodes:
                    queue.append(adj)

        # Add any disconnected rooms at the end
        for rid in room_ids:
            if rid not in visited:
                result.append(rid)

        return result
