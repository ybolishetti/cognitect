"""
PlanPreviewer — renders a floor plan coordinate matrix to a PNG image.

Uses matplotlib patches to draw rooms as colored rectangles with labels
and dimension annotations.

Architecture rule: PlanPreviewer never calls the LLM, solver, or CAD kernel.
It only reads coordinate_matrix + room metadata.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# One color per room type — warm, readable palette
ROOM_COLORS = {
    "bedroom":  "#AED6F1",  # soft blue
    "bathroom": "#A9DFBF",  # soft green
    "kitchen":  "#FAD7A0",  # warm orange
    "living":   "#F9E79F",  # warm yellow
    "dining":   "#F5CBA7",  # peach
    "hallway":  "#D7DBDD",  # light grey
    "office":   "#D2B4DE",  # lavender
    "garage":   "#BFC9CA",  # cool grey
    "other":    "#EAEDED",  # near-white
}
DEFAULT_COLOR = "#EAEDED"


class PlanPreviewer:
    """
    Renders a floor plan to PNG bytes.

    Usage:
        previewer = PlanPreviewer()
        png_bytes = previewer.render(coordinate_matrix, room_metadata)
    """

    def render(
        self,
        coordinate_matrix: dict,
        room_metadata: dict,
        width_px: int = 900,
        height_px: int = 700,
        dpi: int = 100,
        title: Optional[str] = None,
        fmt: str = "png",
    ) -> bytes:
        """
        Render the floor plan to image bytes.

        Args:
            coordinate_matrix: {room_id: {x, y, width, height}} in feet
            room_metadata: {room_id: {name, room_type}} — for labels and colors
            width_px: Output image width in pixels
            height_px: Output image height in pixels
            dpi: Render DPI
            title: Optional plan title shown at top
            fmt: Output format — "png" (default) or "pdf"

        Returns:
            Image bytes in the requested format
        """
        fmt = (fmt or "png").lower()
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend — must be before pyplot import
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        fig_w = width_px / dpi
        fig_h = height_px / dpi
        fig, ax = plt.subplots(1, 1, figsize=(fig_w, fig_h))

        if not coordinate_matrix:
            ax.text(0.5, 0.5, "No rooms yet.\nType an instruction below.",
                    ha="center", va="center", fontsize=14, color="#888",
                    transform=ax.transAxes)
            ax.set_axis_off()
            buf = io.BytesIO()
            fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight",
                        facecolor="#F8F9FA")
            plt.close(fig)
            buf.seek(0)
            return buf.read()

        # Compute bounding box for auto-scaling
        all_x = [c["x"] for c in coordinate_matrix.values()]
        all_y = [c["y"] for c in coordinate_matrix.values()]
        all_r = [c["x"] + c["width"] for c in coordinate_matrix.values()]
        all_t = [c["y"] + c["height"] for c in coordinate_matrix.values()]
        min_x, min_y = min(all_x), min(all_y)
        max_x, max_y = max(all_r), max(all_t)
        span_x = max_x - min_x or 1
        span_y = max_y - min_y or 1
        pad = max(span_x, span_y) * 0.08

        ax.set_xlim(min_x - pad, max_x + pad)
        ax.set_ylim(min_y - pad, max_y + pad)
        ax.set_aspect("equal")
        ax.set_facecolor("#F8F9FA")
        ax.grid(False)

        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

        for room_id, coords in coordinate_matrix.items():
            x, y, w, h = coords["x"], coords["y"], coords["width"], coords["height"]
            meta = room_metadata.get(room_id, {})
            room_type = meta.get("room_type", "other")
            name = meta.get("name", room_id.replace("_", " ").title())
            # Escape underscores so matplotlib doesn't interpret them as LaTeX subscripts
            name = name.replace("_", " ")
            color = ROOM_COLORS.get(room_type, DEFAULT_COLOR)

            # Room rectangle
            rect = mpatches.FancyBboxPatch(
                (x, y), w, h,
                boxstyle="square,pad=0",
                linewidth=1.8,
                edgecolor="#2C3E50",
                facecolor=color,
                alpha=0.85,
                zorder=2,
            )
            ax.add_patch(rect)

            # Room name label
            font_size = max(6, min(11, min(w, h) * 1.8))
            cx, cy = x + w / 2, y + h / 2
            ax.text(cx, cy + 0.15, name,
                    ha="center", va="center",
                    fontsize=font_size, fontweight="bold",
                    color="#1A252F", zorder=3)

            # Area label (smaller, below name)
            area = w * h
            ax.text(cx, cy - 0.35, f"{area:.0f} sqft",
                    ha="center", va="center",
                    fontsize=max(5, font_size - 2),
                    color="#5D6D7E", zorder=3)

            # Dimension ticks: width along bottom edge, offset below the room
            tick_color = "#7F8C8D"
            tick_fs = max(5, font_size - 3)
            arrow_y = y - pad * 0.5  # per-room baseline, not shared
            ax.annotate(
                "", xy=(x + w, arrow_y), xytext=(x, arrow_y),
                arrowprops=dict(arrowstyle="<->", color=tick_color, lw=0.8),
                zorder=1,
            )
            ax.text(cx, arrow_y - pad * 0.15, f"{w:.0f}'",
                    ha="center", va="top", fontsize=tick_fs, color=tick_color)

        if title:
            fig.suptitle(title, fontsize=13, fontweight="bold",
                         color="#2C3E50", y=0.98)

        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi, bbox_inches="tight",
                    facecolor="white")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
