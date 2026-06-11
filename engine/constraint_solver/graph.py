"""
Coordinate graph data structures for the constraint solver.

Rooms are nodes; shared walls / adjacency requirements are edges.
This module is pure data — no kiwisolver variables live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RoomNode:
    """A room in the floor plan graph."""

    room_id: str
    name: str
    room_type: str

    # Soft targets from RoomSpec (in sqft / ratios)
    target_area_sqft: Optional[float] = None
    min_area_sqft: Optional[float] = None
    max_area_sqft: Optional[float] = None
    aspect_ratio: Optional[float] = None  # width / height

    # Adjacency edges (room_ids this room must share a wall with)
    adjacency: list[str] = field(default_factory=list)

    # Position lock from prior coordinate_matrix (feet). Width/height remain free.
    pinned_position: tuple[float, float] | None = None
    # When True, pinned x/y use "strong" instead of "required" (neighbor of mutated room).
    is_flexible_pin: bool = False


@dataclass
class WallEdge:
    """Shared wall between two rooms (from ConnectionSpec or adjacency_requirements)."""

    room_a_id: str
    room_b_id: str
    shared_axis: Optional[str] = None  # "x" or "y" — resolved during layout


@dataclass
class CoordinateGraph:
    """
    The complete graph fed to the constraint solver.

    nodes: room_id → RoomNode
    edges: list of shared-wall relationships
    """

    nodes: dict[str, RoomNode] = field(default_factory=dict)
    edges: list[WallEdge] = field(default_factory=list)

    def add_room(self, node: RoomNode) -> None:
        self.nodes[node.room_id] = node

    def add_edge(self, edge: WallEdge) -> None:
        # Deduplicate
        for existing in self.edges:
            pair_existing = {existing.room_a_id, existing.room_b_id}
            pair_new = {edge.room_a_id, edge.room_b_id}
            if pair_existing == pair_new:
                return
        self.edges.append(edge)

    def adjacent_rooms(self, room_id: str) -> list[str]:
        """Return all room IDs that share a wall with room_id."""
        result = []
        for edge in self.edges:
            if edge.room_a_id == room_id:
                result.append(edge.room_b_id)
            elif edge.room_b_id == room_id:
                result.append(edge.room_a_id)
        return result
