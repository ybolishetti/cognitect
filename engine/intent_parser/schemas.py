"""
Cognitect Op Schema — the contract between the LLM intent parser and the constraint solver.

The LLM produces a FloorPlanOp. The constraint solver consumes FloorPlanState.
The CAD generator consumes the coordinate_matrix from FloorPlanState.

Key rule: LLM never touches geometry. Geometry never touches LLM.
"""

from __future__ import annotations

from typing import Literal, Optional, Union
from pydantic import BaseModel, Field, model_validator


class RoomSpec(BaseModel):
    """Specification for a single room. Area constraints are soft by default."""

    name: str = Field(..., description="Human-readable room name, e.g. 'Master Bedroom'")
    room_type: Literal[
        "bedroom", "bathroom", "kitchen", "living", "dining",
        "hallway", "office", "garage", "other"
    ]
    area_sqft: Optional[float] = Field(
        None, gt=0, description="Target area in square feet (soft constraint)"
    )
    min_area_sqft: Optional[float] = Field(None, gt=0)
    max_area_sqft: Optional[float] = Field(None, gt=0)
    aspect_ratio: Optional[float] = Field(
        None, gt=0, description="width / height ratio; None means unconstrained"
    )
    adjacency_requirements: list[str] = Field(
        default_factory=list,
        description="List of room names this room must share a wall with",
    )

    @model_validator(mode="after")
    def validate_area_bounds(self) -> "RoomSpec":
        if self.min_area_sqft and self.max_area_sqft:
            if self.min_area_sqft > self.max_area_sqft:
                raise ValueError("min_area_sqft must be <= max_area_sqft")
        return self


class ConstraintSpec(BaseModel):
    """A single constraint binding one or more rooms. Maps to kiwisolver variables."""

    constraint_type: Literal[
        "min_area", "max_area", "adjacency", "separation", "aspect_ratio", "orientation"
    ]
    room_id: str = Field(..., description="ID of the primary room this constraint applies to")
    value: Union[float, str] = Field(
        ...,
        description=(
            "Numeric value (sqft, ratio, degrees) or string directive "
            "('north', 'south', 'east', 'west') depending on constraint_type"
        ),
    )
    strength: Literal["required", "strong", "medium", "weak"] = Field(
        "strong",
        description=(
            "Maps to kiwisolver strengths: "
            "required=REQUIRED, strong=STRONG, medium=MEDIUM, weak=WEAK"
        ),
    )


class ConnectionSpec(BaseModel):
    """A door/archway/opening between two rooms."""

    room_a_id: str
    room_b_id: str
    connection_type: Literal["door", "archway", "wall_opening"]
    width_ft: Optional[float] = Field(None, gt=0, description="Opening width in feet")

    @model_validator(mode="after")
    def validate_distinct_rooms(self) -> "ConnectionSpec":
        if self.room_a_id == self.room_b_id:
            raise ValueError("room_a_id and room_b_id must be different rooms")
        return self


class FloorPlanOp(BaseModel):
    """
    A single atomic operation emitted by the intent parser.

    One NL utterance produces exactly one FloorPlanOp.
    The constraint solver applies it to the current FloorPlanState.
    """

    op_type: Literal[
        "add_room", "remove_room", "resize_room", "move_room",
        "add_connection", "set_constraint"
    ]
    target_room_id: Optional[str] = Field(
        None,
        description=(
            "ID of existing room to operate on. "
            "Required for remove_room, resize_room, move_room, set_constraint."
        ),
    )
    room_spec: Optional[RoomSpec] = Field(
        None,
        description="Room spec to add or merge. Required for add_room; optional for resize_room.",
    )
    constraint_spec: Optional[ConstraintSpec] = Field(
        None, description="Constraint to add or update. Required for set_constraint."
    )
    connection_spec: Optional[ConnectionSpec] = Field(
        None, description="Connection to add. Required for add_connection."
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Free-form metadata: confidence score, raw NL, parse timestamp, etc.",
    )

    @model_validator(mode="after")
    def validate_op_fields(self) -> "FloorPlanOp":
        if self.op_type == "add_room" and self.room_spec is None:
            raise ValueError("add_room requires room_spec")
        if self.op_type in ("remove_room", "resize_room", "move_room") and not self.target_room_id:
            raise ValueError(f"{self.op_type} requires target_room_id")
        if self.op_type == "set_constraint" and self.constraint_spec is None:
            raise ValueError("set_constraint requires constraint_spec")
        if self.op_type == "add_connection" and self.connection_spec is None:
            raise ValueError("add_connection requires connection_spec")
        return self


class RoomCoordinates(BaseModel):
    """Resolved 2D position and size for a room, in feet. Origin is (0, 0) bottom-left."""

    x: float = Field(..., description="Left edge x-coordinate in feet")
    y: float = Field(..., description="Bottom edge y-coordinate in feet")
    width: float = Field(..., gt=0, description="Room width (x-axis) in feet")
    height: float = Field(..., gt=0, description="Room height (y-axis) in feet")

    @property
    def area_sqft(self) -> float:
        return self.width * self.height

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height


class FloorPlanState(BaseModel):
    """
    Complete state of a floor plan at a point in time.

    rooms: dict mapping room_id (slug) → RoomSpec
    constraints: list of active constraints
    connections: list of room connections
    coordinate_matrix: populated after constraint resolution
    """

    plan_id: str
    rooms: dict[str, RoomSpec] = Field(default_factory=dict)
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    connections: list[ConnectionSpec] = Field(default_factory=list)
    coordinate_matrix: Optional[dict[str, dict]] = Field(
        None,
        description=(
            "After solver runs: {room_id: {x, y, width, height}} in feet. "
            "None if solver hasn't run yet."
        ),
    )
    version: int = Field(0, description="Incremented on each successful op application")
