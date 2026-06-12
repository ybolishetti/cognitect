"""
2D room placement engine for the constraint solver.

Computes suggested (x, y) origins before kiwisolver runs. Pure function — no LLM,
no solver state. Uses shelf-first-fit bin packing with adjacency cluster grouping.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .graph import CoordinateGraph, RoomNode

MIN_ROOM_DIM_FT = 4.0
MAX_PLAN_DIM_FT = 200.0
DEFAULT_ASPECT_RATIO = 1.3
# Target aspect ratio for the overall plan canvas (width / height).
# Shelf-packing wraps when the row exceeds this width, producing a roughly
# rectangular layout instead of a single horizontal strip.
TARGET_CANVAS_ASPECT = 1.6


@dataclass
class PlacementMap:
    """Suggested (x, y) origin for each room before kiwisolver runs."""

    positions: dict[str, tuple[float, float]] = field(default_factory=dict)


def _estimate_dims(node: RoomNode) -> tuple[float, float]:
    area = node.target_area_sqft or 100.0
    aspect = node.aspect_ratio or DEFAULT_ASPECT_RATIO
    w = math.sqrt(area * aspect)
    h = math.sqrt(area / aspect)
    return max(w, MIN_ROOM_DIM_FT), max(h, MIN_ROOM_DIM_FT)


def _build_clusters(graph: CoordinateGraph, room_ids: list[str]) -> list[list[str]]:
    """Partition rooms into connected components via BFS on adjacency edges."""
    room_set = set(room_ids)
    visited: set[str] = set()
    clusters: list[list[str]] = []

    for start in room_ids:
        if start in visited:
            continue
        cluster: list[str] = []
        queue: deque[str] = deque([start])
        while queue:
            rid = queue.popleft()
            if rid in visited or rid not in room_set:
                continue
            visited.add(rid)
            cluster.append(rid)
            for adj in graph.adjacent_rooms(rid):
                if adj not in visited and adj in room_set:
                    queue.append(adj)
        if cluster:
            clusters.append(cluster)

    return clusters


def _rects_overlap(
    ax: float, ay: float, aw: float, ah: float,
    bx: float, by: float, bw: float, bh: float,
) -> bool:
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


class RoomLayoutEngine:
    """
    Computes 2D placement suggestions for rooms prior to kiwisolver.

    Pure function — no solver state, no LLM. Only reads CoordinateGraph node/edge data.
    """

    def compute_placement(
        self,
        graph: CoordinateGraph,
        room_ids: list[str],
        pinned: dict[str, tuple[float, float]] | None = None,
    ) -> PlacementMap:
        pinned = pinned or {}
        positions: dict[str, tuple[float, float]] = dict(pinned)
        placed: set[str] = set(pinned.keys())

        # Occupied regions from pinned rooms (estimated dimensions)
        obstacles: list[tuple[float, float, float, float]] = []
        for rid, (px, py) in pinned.items():
            if rid not in graph.nodes:
                continue
            w, h = _estimate_dims(graph.nodes[rid])
            obstacles.append((px, py, w, h))

        unpinned = [rid for rid in room_ids if rid not in placed]

        # Derive a target wrap width so the layout forms a roughly rectangular
        # canvas rather than a single horizontal strip.  Use sqrt(total_area) *
        # TARGET_CANVAS_ASPECT as the wrap threshold, clamped to MAX_PLAN_DIM_FT.
        total_area = sum(
            (graph.nodes[rid].target_area_sqft or 100.0) for rid in unpinned
        )
        wrap_width = min(
            math.sqrt(total_area) * TARGET_CANVAS_ASPECT,
            MAX_PLAN_DIM_FT,
        )
        # Never wrap tighter than the widest single room estimate
        if unpinned:
            max_single_w = max(_estimate_dims(graph.nodes[rid])[0] for rid in unpinned)
            wrap_width = max(wrap_width, max_single_w)

        clusters = _build_clusters(graph, unpinned)

        shelf_y = 0.0
        shelf_height = 0.0
        cursor_x = 0.0

        # Advance cursor past pinned obstacles on the first shelf
        if obstacles:
            cursor_x = max(px + w for px, py, w, h in obstacles)
            shelf_height = max(h for _, _, _, h in obstacles)
            shelf_y = min(py for _, py, _, _ in obstacles)

        for cluster in clusters:
            if not cluster:
                continue

            cluster_order = self._cluster_bfs_order(cluster[0], cluster, graph)
            cluster_placed: set[str] = set()
            cluster_start_x = cursor_x
            cluster_max_height = 0.0

            for rid in cluster_order:
                if rid in placed:
                    continue
                node = graph.nodes[rid]
                w, h = _estimate_dims(node)

                # Wrap to next shelf if this room won't fit horizontally
                if cursor_x > 0 and cursor_x + w > wrap_width:
                    shelf_y += shelf_height if shelf_height > 0 else h
                    cursor_x = 0.0
                    shelf_height = 0.0
                    cluster_start_x = 0.0

                x, y = self._place_room(
                    rid, w, h, graph, positions, cluster_placed, obstacles,
                    cursor_x, shelf_y, shelf_height, wrap_width,
                )

                positions[rid] = (x, y)
                placed.add(rid)
                cluster_placed.add(rid)
                obstacles.append((x, y, w, h))

                cursor_x = max(cursor_x, x + w)
                cluster_max_height = max(cluster_max_height, h)
                shelf_height = max(shelf_height, h)

            # Advance cursor with gap after cluster (horizontal packing across clusters)
            if cursor_x == cluster_start_x:
                cursor_x = max(cursor_x, cluster_start_x)

        # Any disconnected unpinned rooms missed by cluster loop
        for rid in room_ids:
            if rid in positions:
                continue
            node = graph.nodes[rid]
            w, h = _estimate_dims(node)
            x, y = cursor_x, shelf_y
            if x + w > wrap_width:
                shelf_y += shelf_height if shelf_height > 0 else h
                x, y = 0.0, shelf_y
                cursor_x = 0.0
                shelf_height = 0.0
            positions[rid] = (x, y)
            cursor_x = x + w
            shelf_height = max(shelf_height, h)

        return PlacementMap(positions=positions)

    def _cluster_bfs_order(
        self,
        root: str,
        cluster: list[str],
        graph: CoordinateGraph,
    ) -> list[str]:
        cluster_set = set(cluster)
        result: list[str] = []
        visited: set[str] = set()
        queue: deque[str] = deque([root])
        while queue:
            rid = queue.popleft()
            if rid in visited or rid not in cluster_set:
                continue
            visited.add(rid)
            result.append(rid)
            for adj in graph.adjacent_rooms(rid):
                if adj not in visited and adj in cluster_set:
                    queue.append(adj)
        for rid in cluster:
            if rid not in visited:
                result.append(rid)
        return result

    def _place_room(
        self,
        rid: str,
        w: float,
        h: float,
        graph: CoordinateGraph,
        positions: dict[str, tuple[float, float]],
        cluster_placed: set[str],
        obstacles: list[tuple[float, float, float, float]],
        cursor_x: float,
        shelf_y: float,
        shelf_height: float,
        wrap_width: float = MAX_PLAN_DIM_FT,
    ) -> tuple[float, float]:
        """Place a room adjacent to a placed neighbor when possible, else on shelf."""
        # Try adjacency placement relative to already-placed neighbors (incl. pinned)
        for adj in graph.adjacent_rooms(rid):
            if adj not in positions:
                continue
            adj_node = graph.nodes[adj]
            aw, ah = _estimate_dims(adj_node)
            ax, ay = positions[adj]

            candidates = [
                (ax + aw, ay),       # right of neighbor
                (ax, ay + ah),       # below neighbor
                (ax - w, ay),        # left of neighbor
                (ax, ay - h),        # above neighbor
            ]
            for cx, cy in candidates:
                if cx < 0 or cy < 0:
                    continue
                if cx + w > wrap_width or cy + h > MAX_PLAN_DIM_FT:
                    continue
                if any(_rects_overlap(cx, cy, w, h, ox, oy, ow, oh) for ox, oy, ow, oh in obstacles):
                    continue
                return cx, cy

        # Default shelf placement
        x, y = cursor_x, shelf_y
        if x + w > wrap_width:
            y = shelf_y + (shelf_height if shelf_height > 0 else h)
            x = 0.0

        # Nudge past any overlapping obstacle on the shelf
        for ox, oy, ow, oh in obstacles:
            if _rects_overlap(x, y, w, h, ox, oy, ow, oh):
                x = max(x, ox + ow)

        if x + w > wrap_width:
            y = shelf_y + (shelf_height if shelf_height > 0 else h)
            x = 0.0

        return x, y
