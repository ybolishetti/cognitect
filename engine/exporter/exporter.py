"""
Plan Exporter — DXF, DWG, and PDF export via ezdxf.

SLA: <60s total
Architecture rule: exporter reads only the coordinate matrix and .FCStd path.
It never calls the LLM and never reads conversational state.

STUB: Full implementation pending — see DRAFT_EXPORTER.md for Cursor Composer spec.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ExportError(Exception):
    """Raised when export fails."""


class PlanExporter:
    """
    Exports floor plan models to DXF, DWG, and PDF formats.

    DXF output includes:
    - Room outlines with layer-per-room
    - Regulatory annotations (room labels, dimensions, area in sqft)
    - Title block with plan metadata

    SLA: <60s total
    """

    def export_dxf(self, freecad_model_path: Path, metadata: dict) -> Path:
        """
        Export a floor plan to DXF with regulatory annotations.

        Args:
            freecad_model_path: Path to .FCStd file from CADGenerator.
            metadata: Dict with keys: plan_id, rooms (coordinate_matrix),
                      project_name, scale, date, etc.

        Returns:
            Path to generated .dxf file (sibling of input file).

        Raises:
            ExportError: If DXF generation fails.
            NotImplementedError: Until Cursor Composer implements this.

        SLA: <60s total
        """
        # TODO: Implement via Cursor Composer — see DRAFT_EXPORTER.md
        # Implementation outline:
        # 1. Load coordinate_matrix from metadata["rooms"]
        # 2. Create ezdxf document (R2010 for broad compatibility)
        # 3. Add ROOMS layer with room polylines
        # 4. Add DIMENSIONS layer with ezdxf dimension entities
        # 5. Add ANNOTATIONS layer with room labels and area text
        # 6. Add TITLE_BLOCK layer with project metadata
        # 7. Add regulatory callouts (min door widths, egress annotations)
        # 8. Save to freecad_model_path.with_suffix(".dxf")
        raise NotImplementedError(
            "PlanExporter.export_dxf() is pending Cursor Composer implementation. "
            "See DRAFT_EXPORTER.md for the full spec."
        )

    def export_pdf(self, freecad_model_path: Path, metadata: dict) -> Path:
        """
        Export a floor plan to PDF via DXF → PDF conversion.

        Args:
            freecad_model_path: Path to .FCStd file from CADGenerator.
            metadata: Same as export_dxf metadata dict.

        Returns:
            Path to generated .pdf file.

        Raises:
            ExportError: If PDF generation fails.
            NotImplementedError: Until Cursor Composer implements this.

        SLA: <60s total
        """
        # TODO: Implement via Cursor Composer — see DRAFT_EXPORTER.md
        # Implementation outline:
        # 1. Call export_dxf() to get the DXF
        # 2. Use ezdxf's drawing add-on or matplotlib backend to render
        # 3. Save as PDF using reportlab or matplotlib PDF backend
        raise NotImplementedError(
            "PlanExporter.export_pdf() is pending Cursor Composer implementation. "
            "See DRAFT_EXPORTER.md for the full spec."
        )

    def export_from_matrix(self, coordinate_matrix: dict, metadata: dict) -> Path:
        """
        Shortcut: export directly from coordinate matrix without requiring .FCStd.
        Useful when FreeCAD is not available (e.g., testing or 2D-only mode).

        Args:
            coordinate_matrix: {room_id: {x, y, width, height}} in feet
            metadata: Dict with plan_id, project_name, etc.

        Returns:
            Path to generated .dxf file.

        Raises:
            ExportError: If DXF generation fails.
            NotImplementedError: Until Cursor Composer implements this.
        """
        # TODO: Implement via Cursor Composer — see DRAFT_EXPORTER.md
        raise NotImplementedError(
            "PlanExporter.export_from_matrix() is pending Cursor Composer implementation. "
            "See DRAFT_EXPORTER.md for the full spec."
        )
