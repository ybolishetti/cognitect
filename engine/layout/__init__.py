"""Architecture C — typed Layout schema.

The ground truth of the generate flow. Produced by LayoutGenerator, verified
by Layer A / B / C, ranked by best-of-N, exported by the exporter.
"""

from engine.layout.schemas import (
    Room,
    Wall,
    Opening,
    GridLine,
    StructuralGrid,
    Exit,
    Layout,
    RoomRequirement,
    SiteConstraints,
    FloorPlanSpec,
)
from engine.layout.audit import LayoutAuditManifest, VerifierResult
from engine.layout.errors import GenerationFailure, SchemaViolation

__all__ = [
    "Room", "Wall", "Opening", "GridLine", "StructuralGrid", "Exit",
    "Layout", "RoomRequirement", "SiteConstraints", "FloorPlanSpec",
    "LayoutAuditManifest", "VerifierResult",
    "GenerationFailure", "SchemaViolation",
]
