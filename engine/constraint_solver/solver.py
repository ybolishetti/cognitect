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
from .layout import RoomLayoutEngine, _rects_overlap, TARGET_CANVAS_ASPECT

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


def _push_down_overlaps(matrix: dict) -> dict:
    """Post-process: push rooms down to eliminate vertical overlaps.

    The shelf-packing tiler uses solved sizes, so overlaps shouldn't occur.
    This is a safety net for floating-point edge cases.
    """
    changed = True
    iters = 0
    while changed and iters < 20:
        changed = False
        iters += 1
        rids = sorted(matrix.keys(), key=lambda r: matrix[r]["y"])
        for i, r_bot in enumerate(rids):
            for r_top in rids[:i]:
                c_top = matrix[r_top]
                c_bot = matrix[r_bot]
                x_overlap = not (
                    c_top["x"] + c_top["width"] <= c_bot["x"]
                    or c_bot["x"] + c_bot["width"] <= c_top["x"]
                )
                if not x_overlap:
                    continue
                gap = (c_top["y"] + c_top["height"]) - c_bot["y"]
                if gap > 0.01:  # ignore floating-point exact-touch (gap < 0.01)
                    matrix[r_bot] = dict(c_bot, y=round(c_bot["y"] + gap, 3))
                    changed = True
    return matrix


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
            # Detect if any mutated rooms are newly added (not in prior_matrix).
            # When new rooms are being added, we use flexible pins for all existing rooms
            # so the layout engine can rebalance the whole plan.
            # When it's a pure edit (resize/move), we use required pins for continuity.
            has_new_rooms = any(rid not in prior_matrix for rid in mutated)

            for room_id, coords in prior_matrix.items():
                if room_id not in graph.nodes:
                    continue
                if room_id in mutated:
                    continue
                graph.nodes[room_id].pinned_position = (coords["x"], coords["y"])
                if has_new_rooms:
                    # New room being added — allow existing rooms to shift so the
                    # layout engine can produce a balanced 2D grid
                    graph.nodes[room_id].is_flexible_pin = True

            # Also downgrade neighbors of mutated rooms from required to strong
            if not has_new_rooms:
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
        Two-phase layout pass:
          Phase 1 — kiwisolver resolves WIDTH and HEIGHT only (area + aspect constraints).
          Phase 2 — RoomLayoutEngine tiles rooms using actual solved sizes (no kiwi for x/y).

        This avoids the fundamental mismatch between kiwi's linear position suggestions
        and the non-linear non-overlap requirements of a 2D floor plan.
        """
        solver = kiwisolver.Solver()

        # Create variables for width and height only
        vars_: dict[str, dict[str, kiwisolver.Variable]] = {}
        for rid in room_ids:
            vars_[rid] = {
                "w": kiwisolver.Variable(f"{rid}_w"),
                "h": kiwisolver.Variable(f"{rid}_h"),
            }

        # ── Size constraints ──────────────────────────────────────────────────

        for rid in room_ids:
            node = graph.nodes[rid]
            v = vars_[rid]
            sf = scale_factors[rid]

            # Minimum dimensions (required)
            solver.addConstraint((v["w"] >= MIN_ROOM_DIM_FT) | "required")
            solver.addConstraint((v["h"] >= MIN_ROOM_DIM_FT) | "required")

            # Maximum dimensions (required)
            solver.addConstraint((v["w"] <= MAX_PLAN_DIM_FT) | "required")
            solver.addConstraint((v["h"] <= MAX_PLAN_DIM_FT) | "required")

            target_area = node.target_area_sqft
            if target_area:
                aspect = node.aspect_ratio or 1.0
                target_w = math.sqrt(target_area * aspect) * sf
                target_h = math.sqrt(target_area / aspect) * sf
                target_w = max(target_w, MIN_ROOM_DIM_FT)
                target_h = max(target_h, MIN_ROOM_DIM_FT)
                solver.addConstraint((v["w"] == target_w) | "strong")
                solver.addConstraint((v["h"] == target_h) | "strong")

            if node.min_area_sqft:
                aspect = node.aspect_ratio or 1.0
                solver.addConstraint((v["w"] >= math.sqrt(node.min_area_sqft * aspect)) | "strong")
                solver.addConstraint((v["h"] >= math.sqrt(node.min_area_sqft / aspect)) | "strong")

            if node.max_area_sqft:
                aspect = node.aspect_ratio or 1.0
                solver.addConstraint((v["w"] <= math.sqrt(node.max_area_sqft * aspect)) | "strong")
                solver.addConstraint((v["h"] <= math.sqrt(node.max_area_sqft / aspect)) | "strong")

            if node.aspect_ratio:
                solver.addConstraint(
                    (v["w"] == node.aspect_ratio * v["h"]) | "strong"
                )
            else:
                solver.addConstraint((v["w"] == v["h"]) | "weak")

        solver.updateVariables()

        # Extract solved sizes
        sizes: dict[str, tuple[float, float]] = {}
        for rid in room_ids:
            v = vars_[rid]
            w = max(round(v["w"].value(), 3), MIN_ROOM_DIM_FT)
            h = max(round(v["h"].value(), 3), MIN_ROOM_DIM_FT)
            sizes[rid] = (w, h)

        # ── Phase 2: tile rooms using a shelf-packing layout with actual sizes ──
        #
        # For pure edits (resize/move), respect pinned positions for rooms that
        # weren't touched. For add-room operations, always do a full rebalance.
        has_new_rooms = any(
            graph.nodes[rid].pinned_position is None
            and graph.nodes[rid].is_flexible_pin is False
            for rid in room_ids
        ) or any(
            graph.nodes[rid].is_flexible_pin
            for rid in room_ids
            if graph.nodes[rid].pinned_position is not None
        )

        if has_new_rooms:
            # Full rebalance with actual solved sizes
            positions = self._tile_rooms(room_ids, sizes, graph, pinned={})
        else:
            # Preserve pinned positions; tile only the unpinned (mutated) rooms
            pinned_positions = {
                rid: graph.nodes[rid].pinned_position
                for rid in room_ids
                if graph.nodes[rid].pinned_position is not None
                and not graph.nodes[rid].is_flexible_pin
            }
            positions = self._tile_rooms(room_ids, sizes, graph, pinned=pinned_positions)

        # Build matrix
        matrix: dict[str, dict[str, float]] = {}
        for rid in room_ids:
            x, y = positions[rid]
            w, h = sizes[rid]
            matrix[rid] = {"x": x, "y": y, "width": w, "height": h}

        # Post-process: push any residual overlaps down
        matrix = _push_down_overlaps(matrix)
        return matrix

    def _tile_rooms(
        self,
        room_ids: list[str],
        sizes: dict[str, tuple[float, float]],
        graph: CoordinateGraph,
        pinned: dict[str, tuple[float, float]],
    ) -> dict[str, tuple[float, float]]:
        """
        Place rooms using shelf-first-fit bin packing with actual solved sizes.
        Respects pinned positions for unmutated rooms.
        Returns {room_id: (x, y)}.
        """
        import math as _math
        from collections import defaultdict as _defaultdict

        positions: dict[str, tuple[float, float]] = dict(pinned)
        placed: set[str] = set(pinned.keys())

        unpinned = [rid for rid in room_ids if rid not in placed]
        if not unpinned:
            return positions

        # Derive canvas wrap width from total area of unpinned rooms
        total_area = sum(sizes[rid][0] * sizes[rid][1] for rid in unpinned)
        wrap_width = min(_math.sqrt(total_area) * TARGET_CANVAS_ASPECT, MAX_PLAN_DIM_FT)
        if unpinned:
            max_w = max(sizes[rid][0] for rid in unpinned)
            wrap_width = max(wrap_width, max_w)

        # Build a simple obstacle list from pinned rooms
        obstacles: list[tuple[float, float, float, float]] = []
        for rid, (px, py) in pinned.items():
            w, h = sizes[rid]
            obstacles.append((px, py, w, h))

        shelf_y = 0.0
        shelf_height = 0.0
        cursor_x = 0.0

        if obstacles:
            cursor_x = max(px + w for px, py, w, h in obstacles)
            shelf_height = max(h for _, _, _, h in obstacles)
            shelf_y = min(py for _, py, _, _ in obstacles)

        for rid in unpinned:
            w, h = sizes[rid]

            # Wrap to next shelf if needed
            if cursor_x > 0 and cursor_x + w > wrap_width:
                shelf_y += shelf_height if shelf_height > 0 else h
                cursor_x = 0.0
                shelf_height = 0.0

            x, y = cursor_x, shelf_y

            # Nudge past any overlapping obstacle
            changed = True
            while changed:
                changed = False
                for ox, oy, ow, oh in obstacles:
                    if _rects_overlap(x, y, w, h, ox, oy, ow, oh):
                        x = max(x, ox + ow)
                        changed = True
                if x + w > wrap_width:
                    shelf_y += shelf_height if shelf_height > 0 else h
                    x, y = 0.0, shelf_y
                    shelf_height = 0.0

            positions[rid] = (round(x, 3), round(y, 3))
            placed.add(rid)
            obstacles.append((x, y, w, h))
            cursor_x = x + w
            shelf_height = max(shelf_height, h)

        return positions


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
