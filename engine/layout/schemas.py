"""Architecture C — typed Layout schema.

The typed ground truth for the generate flow: Room, Wall, Opening,
StructuralGrid, Exit, and the top-level Layout, plus FloorPlanSpec (the
whole-plan intent that a LayoutGenerator consumes to produce candidate
Layouts).

Coordinate system (STRICT): feet, origin (0, 0) at bottom-left, x increases
right, y increases up. Math coords, not screen coords — the previewer flips
y at render time; this schema, the exporter, and the verifiers all use math
coords.

This module is schema-only: type and reference errors are caught here.
Semantic errors (geometric overlap, wall connectivity, code compliance,
structural sanity) are the job of the Layer A/B/C verifiers (later DRAFTs),
not this module.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from engine.layout.audit import LayoutAuditManifest

RoomType = Literal[
    "bedroom", "bathroom", "kitchen", "living", "dining",
    "hallway", "office", "garage", "closet", "utility", "other",
]


def _shoelace_area(vertices: list[tuple[float, float]]) -> float:
    """Return signed area (positive if CCW, negative if CW)."""
    n = len(vertices) - 1  # last vertex == first
    s = 0.0
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[i + 1]
        s += (x1 * y2 - x2 * y1)
    return s / 2.0


class Room(BaseModel):
    id: str = Field(..., pattern=r"^room_[a-z0-9_]+$")
    name: str = Field(..., min_length=1, max_length=64)
    room_type: RoomType
    # Polygon vertices — must be closed (first == last), CCW ordering
    vertices: list[tuple[float, float]] = Field(..., min_length=4)
    area_sqft: float = Field(..., gt=0)
    # Wall IDs that bound this room (references walls[].id)
    boundary_wall_ids: list[str] = Field(..., min_length=3)
    ceiling_height_ft: float = Field(default=9.0, gt=0)
    metadata: dict = Field(default_factory=dict)

    @field_validator("vertices")
    @classmethod
    def _validate_vertices(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if v[0] != v[-1]:
            raise ValueError("vertices must be closed: first vertex must equal last vertex")
        for x, y in v:
            if x < 0 or y < 0:
                raise ValueError(f"vertex coordinates must be non-negative, got ({x}, {y})")
        if _shoelace_area(v) <= 0:
            raise ValueError("vertices must be ordered counter-clockwise (positive shoelace area)")
        return v

    @field_validator("boundary_wall_ids")
    @classmethod
    def _validate_unique_boundary_wall_ids(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("boundary_wall_ids must not contain duplicates")
        return v

    @model_validator(mode="after")
    def _validate_area_matches_shoelace(self) -> "Room":
        computed = abs(_shoelace_area(self.vertices))
        tolerance = 0.005 * computed
        if abs(self.area_sqft - computed) > tolerance:
            raise ValueError(
                f"area_sqft ({self.area_sqft}) does not match the shoelace area "
                f"of vertices ({computed:.4f}) within 0.5% tolerance"
            )
        return self


class Wall(BaseModel):
    id: str = Field(..., pattern=r"^wall_[a-z0-9_]+$")
    start: tuple[float, float]  # (x, y) in feet
    end: tuple[float, float]
    thickness_ft: float = Field(default=0.5, gt=0)
    # Which rooms this wall bounds (0, 1, or 2 rooms — 0 = free-standing, 1 = exterior, 2 = interior)
    bounds_rooms: list[str] = Field(..., max_length=2)
    # Wall load-bearing status (advisory, populated by Layer B)
    is_load_bearing: Optional[bool] = None
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_endpoints(self) -> "Wall":
        for x, y in (self.start, self.end):
            if x < 0 or y < 0:
                raise ValueError(f"wall coordinates must be non-negative, got ({x}, {y})")
        if self.start == self.end:
            raise ValueError("start and end must differ (no zero-length walls)")
        return self

    @computed_field  # type: ignore[misc]
    @property
    def length_ft(self) -> float:
        dx = self.end[0] - self.start[0]
        dy = self.end[1] - self.start[1]
        return round((dx * dx + dy * dy) ** 0.5, 4)


class Opening(BaseModel):
    id: str = Field(..., pattern=r"^opening_[a-z0-9_]+$")
    opening_type: Literal["door", "window", "archway", "wall_opening"]
    wall_id: str  # references walls[].id
    # Position along the wall, measured from wall.start
    offset_ft: float = Field(..., ge=0)
    width_ft: float = Field(..., gt=0)
    # Height of the opening (bottom edge) from floor
    sill_height_ft: float = Field(default=0.0, ge=0)
    # Height of the opening itself
    height_ft: float = Field(default=6.67, gt=0)  # 6'8" standard door
    # Swing direction for doors: which room the door opens into
    swings_into_room_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class GridLine(BaseModel):
    id: str = Field(..., pattern=r"^grid_[a-z0-9_]+$")
    axis: Literal["x", "y"]
    position_ft: float = Field(..., ge=0)
    label: str = Field(..., min_length=1, max_length=4)  # "A", "B", "1", "2"


class StructuralGrid(BaseModel):
    lines: list[GridLine] = Field(default_factory=list)
    # Advisory column positions inferred by Layer B, not authoritative
    inferred_column_positions: list[tuple[float, float]] = Field(default_factory=list)


class Exit(BaseModel):
    id: str = Field(..., pattern=r"^exit_[a-z0-9_]+$")
    opening_id: str  # references an Opening of type "door"
    exit_type: Literal["primary", "emergency", "egress_window"]
    # Egress path in feet (from farthest interior point to this exit)
    max_egress_distance_ft: Optional[float] = Field(None, gt=0)
    metadata: dict = Field(default_factory=dict)


class Layout(BaseModel):
    """Complete typed floor plan state — the ground truth of Architecture C."""

    plan_id: str = Field(..., pattern=r"^plan_[a-z0-9_]+$")
    schema_version: Literal["1.0"] = "1.0"

    rooms: list[Room]
    walls: list[Wall]
    openings: list[Opening] = Field(default_factory=list)
    structural_grid: StructuralGrid = Field(default_factory=StructuralGrid)
    exits: list[Exit] = Field(default_factory=list)

    # Overall plan extent (bounding box)
    extent_x_ft: float = Field(..., gt=0)
    extent_y_ft: float = Field(..., gt=0)

    # Provenance (populated by best-of-N; None on raw generation output)
    audit: Optional[LayoutAuditManifest] = None

    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_cross_references(self) -> "Layout":
        room_ids = [r.id for r in self.rooms]
        wall_ids = [w.id for w in self.walls]
        opening_ids = [o.id for o in self.openings]
        exit_ids = [e.id for e in self.exits]

        for ids, label in (
            (room_ids, "room"),
            (wall_ids, "wall"),
            (opening_ids, "opening"),
            (exit_ids, "exit"),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} IDs found: {ids}")

        rooms_by_id = {r.id: r for r in self.rooms}
        walls_by_id = {w.id: w for w in self.walls}
        door_opening_ids = {o.id for o in self.openings if o.opening_type == "door"}

        for room in self.rooms:
            for wall_id in room.boundary_wall_ids:
                if wall_id not in walls_by_id:
                    raise ValueError(f"Room {room.id} references non-existent wall {wall_id}")

        for wall in self.walls:
            for room_id in wall.bounds_rooms:
                if room_id not in rooms_by_id:
                    raise ValueError(f"Wall {wall.id} references non-existent room {room_id}")

        for opening in self.openings:
            opening_wall = walls_by_id.get(opening.wall_id)
            if opening_wall is None:
                raise ValueError(
                    f"Opening {opening.id} references non-existent wall {opening.wall_id}"
                )
            if opening.offset_ft + opening.width_ft > opening_wall.length_ft:
                raise ValueError(
                    f"Opening {opening.id} (offset {opening.offset_ft} + width "
                    f"{opening.width_ft}) does not fit on wall {opening_wall.id} "
                    f"(length {opening_wall.length_ft})"
                )
            if opening.opening_type == "door" and opening.swings_into_room_id is not None:
                if opening.swings_into_room_id not in opening_wall.bounds_rooms:
                    raise ValueError(
                        f"Opening {opening.id} swings_into_room_id "
                        f"{opening.swings_into_room_id} is not among wall "
                        f"{opening_wall.id}'s bounds_rooms {opening_wall.bounds_rooms}"
                    )
            if opening.opening_type == "window" and len(opening_wall.bounds_rooms) != 1:
                raise ValueError(
                    f"Opening {opening.id} is a window but wall {opening_wall.id} is not "
                    f"an exterior wall (bounds_rooms={opening_wall.bounds_rooms})"
                )

        for exit_ in self.exits:
            if exit_.opening_id not in door_opening_ids:
                raise ValueError(
                    f"Exit {exit_.id} references opening {exit_.opening_id} which "
                    f"does not exist or is not a door"
                )

        max_x = max((x for room in self.rooms for x, _ in room.vertices), default=0.0)
        max_y = max((y for room in self.rooms for _, y in room.vertices), default=0.0)
        if self.extent_x_ft < max_x:
            raise ValueError(
                f"extent_x_ft ({self.extent_x_ft}) is smaller than the max vertex x "
                f"coordinate ({max_x})"
            )
        if self.extent_y_ft < max_y:
            raise ValueError(
                f"extent_y_ft ({self.extent_y_ft}) is smaller than the max vertex y "
                f"coordinate ({max_y})"
            )

        return self


class RoomRequirement(BaseModel):
    """A single room the user wants in the plan."""

    name: str
    room_type: RoomType
    min_area_sqft: Optional[float] = Field(None, gt=0)
    max_area_sqft: Optional[float] = Field(None, gt=0)
    preferred_area_sqft: Optional[float] = Field(None, gt=0)
    aspect_ratio: Optional[float] = Field(None, gt=0)
    adjacencies: list[str] = Field(default_factory=list)  # names of other rooms this must adjoin
    metadata: dict = Field(default_factory=dict)


class SiteConstraints(BaseModel):
    """Lot/site constraints (setbacks, orientation, jurisdiction)."""

    lot_width_ft: Optional[float] = Field(None, gt=0)
    lot_depth_ft: Optional[float] = Field(None, gt=0)
    setback_front_ft: Optional[float] = Field(None, ge=0)
    setback_rear_ft: Optional[float] = Field(None, ge=0)
    setback_side_ft: Optional[float] = Field(None, ge=0)
    max_footprint_sqft: Optional[float] = Field(None, gt=0)
    # Jurisdiction for Layer C code checking
    jurisdiction: str = Field(default="IRC-2021")
    # North direction (degrees from +Y axis, CW)
    north_bearing_deg: float = Field(default=0.0, ge=0.0, lt=360.0)


class FloorPlanSpec(BaseModel):
    """Whole-plan intent — the input to LayoutGenerator.

    Produced from NL by the intent layer. Consumed by LayoutGenerator to produce
    N candidate Layouts.
    """

    spec_id: str = Field(..., pattern=r"^spec_[a-z0-9_]+$")
    room_requirements: list[RoomRequirement] = Field(..., min_length=1)
    site_constraints: SiteConstraints = Field(default_factory=SiteConstraints)
    # Free-form user prose (kept for audit trail — LLMs may re-read this)
    original_nl: str = Field(..., min_length=1)
    # Number of candidates to generate (default 8, capped at 32)
    n_candidates: int = Field(default=8, ge=1, le=32)
    metadata: dict = Field(default_factory=dict)
