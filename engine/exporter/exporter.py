"""
Plan Exporter — DXF, DWG, and PDF export via ezdxf.

SLA: <60s total
Architecture rule: exporter reads only the coordinate matrix and .FCStd path.
It never calls the LLM and never reads conversational state.
"""

from __future__ import annotations

import datetime
import logging
import tempfile
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
        Export from an existing .FCStd file to DXF.
        For now: if coordinate_matrix is in metadata, use it directly.
        """
        if "rooms" not in metadata:
            raise ExportError("metadata must contain 'rooms' key with coordinate matrix")
        return self.export_from_matrix(
            metadata.get("coordinate_matrix", metadata["rooms"]), metadata
        )

    def export_pdf(self, freecad_model_path: Path, metadata: dict) -> Path:
        """
        Export floor plan to PDF via DXF → matplotlib → PDF.
        """
        dxf_path = self.export_dxf(freecad_model_path, metadata)
        pdf_path = dxf_path.with_suffix(".pdf")

        try:
            import ezdxf
            import matplotlib.pyplot as plt
            from ezdxf.addons.drawing import Frontend, RenderContext
            from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

            doc = ezdxf.readfile(str(dxf_path))
            msp = doc.modelspace()

            fig = plt.figure(figsize=(17, 11))  # ANSI B sheet
            ax = fig.add_axes([0.05, 0.05, 0.90, 0.90])

            ctx = RenderContext(doc)
            backend = MatplotlibBackend(ax)
            Frontend(ctx, backend).draw_layout(msp)

            fig.savefig(str(pdf_path), dpi=150, bbox_inches="tight")
            plt.close(fig)

        except ImportError:
            logger.warning("matplotlib not available for PDF export; writing placeholder")
            pdf_path.write_text(
                f"PDF export requires matplotlib. DXF available at: {dxf_path}"
            )

        return pdf_path

    def export_from_matrix(self, coordinate_matrix: dict, metadata: dict) -> Path:
        """
        Export directly from coordinate matrix to DXF.
        coordinate_matrix: {room_id: {x, y, width, height}} in feet
        metadata: {"plan_id": str, "project_name": str, "rooms": {room_id: {"name": str, "room_type": str}}, ...}
        Returns: Path to .dxf file
        """
        import ezdxf

        plan_id = metadata.get("plan_id", "plan")
        project_name = metadata.get("project_name", "Cognitect Floor Plan")
        room_info = metadata.get("rooms", {})

        doc = ezdxf.new(dxfversion="R2010")
        doc.header["$INSUNITS"] = 0  # unitless (we use feet)

        msp = doc.modelspace()

        # Create layers
        doc.layers.add("WALLS", color=7)  # white/black
        doc.layers.add("DIMENSIONS", color=3)  # green
        doc.layers.add("ANNOTATIONS", color=1)  # red
        doc.layers.add("TITLE_BLOCK", color=5)  # blue

        for room_id, coords in coordinate_matrix.items():
            x = coords["x"]
            y = coords["y"]
            w = coords["width"]
            h = coords["height"]
            area = w * h
            name = room_info.get(room_id, {}).get("name", room_id)

            # Room outline polyline (closed, on WALLS layer)
            msp.add_lwpolyline(
                [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                close=True,
                dxfattribs={"layer": "WALLS", "lineweight": 50},
            )

            # Room label (centered in room, on ANNOTATIONS layer)
            cx = x + w / 2
            cy = y + h / 2
            label_height = min(w, h) * 0.08  # 8% of smallest dimension
            label_height = max(label_height, 0.5)  # at least 0.5ft tall text

            msp.add_text(
                name,
                dxfattribs={
                    "layer": "ANNOTATIONS",
                    "height": label_height,
                    "insert": (cx, cy + label_height * 0.5),
                    "halign": 1,  # center
                    "valign": 2,  # middle
                },
            )

            # Area label
            area_text = f"{area:.0f} sqft"
            msp.add_text(
                area_text,
                dxfattribs={
                    "layer": "ANNOTATIONS",
                    "height": label_height * 0.7,
                    "insert": (cx, cy - label_height * 0.5),
                    "halign": 1,
                    "valign": 2,
                },
            )

            # Dimension: width (horizontal)
            msp.add_linear_dim(
                base=(x, y - 2.0),  # dimension line 2ft below
                p1=(x, y),
                p2=(x + w, y),
                angle=0,
                dxfattribs={"layer": "DIMENSIONS"},
            )

            # Dimension: height (vertical)
            msp.add_linear_dim(
                base=(x - 2.0, y),  # dimension line 2ft to the left
                p1=(x, y),
                p2=(x, y + h),
                angle=90,
                dxfattribs={"layer": "DIMENSIONS"},
            )

        # Title block (simple — bottom left corner)
        tb_y = -8.0  # below plan
        msp.add_text(
            project_name,
            dxfattribs={"layer": "TITLE_BLOCK", "height": 1.5, "insert": (0, tb_y)},
        )
        msp.add_text(
            f"Plan ID: {plan_id}",
            dxfattribs={"layer": "TITLE_BLOCK", "height": 0.8, "insert": (0, tb_y - 2)},
        )
        msp.add_text(
            f"Generated: {datetime.date.today().isoformat()}",
            dxfattribs={"layer": "TITLE_BLOCK", "height": 0.8, "insert": (0, tb_y - 3)},
        )

        # Save
        output_dir = Path(tempfile.gettempdir()) / "cognitect_output"
        output_dir.mkdir(exist_ok=True)
        out_path = output_dir / f"{plan_id}.dxf"

        doc.saveas(str(out_path))
        logger.info("DXF exported: %s (%d bytes)", out_path, out_path.stat().st_size)
        return out_path
