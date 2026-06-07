"""
CAD Generator — interface to FreeCAD headless subprocess.

SLA: 5–15s
Architecture rule: this module never reads conversational state.
It only consumes the coordinate matrix and plan metadata.

STUB: Full implementation pending — see DRAFT_CAD_GENERATOR.md for Cursor Composer spec.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..intent_parser.schemas import FloorPlanState

logger = logging.getLogger(__name__)


class CADGenerationError(Exception):
    """Raised when FreeCAD subprocess fails to generate a model."""

    def __init__(self, message: str, stderr: str = "", exit_code: int = 0):
        super().__init__(message)
        self.stderr = stderr
        self.exit_code = exit_code


class CADGenerator:
    """
    Generates a 3D BRep floor plan model using FreeCAD headless.

    The generator invokes FreeCAD as a subprocess via AppImage,
    passing the coordinate matrix as JSON via stdin.
    Output is a .FCStd file.

    SLA: 5–15s
    """

    # FreeCAD is invoked via its extracted squashfs-root (not AppImage directly)
    # because the server lacks libGL.so.1. The extracted AppImage bundles its own libs.
    SQUASHFS_ROOT = Path("/data/workspace/cognitect/squashfs-root")
    FREECADCMD = SQUASHFS_ROOT / "usr/bin/freecadcmd"
    FREECAD_LIBS = f"{SQUASHFS_ROOT}/usr/lib:{SQUASHFS_ROOT}/usr/lib/x86_64-linux-gnu"

    def __init__(self, freecad_appimage_path: str | Path | None = None):
        """
        Args:
            freecad_appimage_path: Path to FreeCAD AppImage (used for reference/re-extraction).
                Defaults to FREECAD_APPIMAGE_PATH env var or
                /data/workspace/freecad/FreeCAD.AppImage

        NOTE: On this server, FreeCAD is run via the extracted squashfs-root
        with LD_LIBRARY_PATH set to its bundled libs (libGL not available system-wide).
        Invocation: cd squashfs-root && LD_LIBRARY_PATH=... ./usr/bin/freecadcmd -c "..."
        """
        import os
        if freecad_appimage_path:
            self.appimage_path = Path(freecad_appimage_path)
        else:
            self.appimage_path = Path(
                os.environ.get(
                    "FREECAD_APPIMAGE_PATH",
                    "/data/workspace/freecad/FreeCAD.AppImage",
                )
            )

    def generate(self, coordinate_matrix: dict, plan_state: FloorPlanState) -> Path:
        """
        Run FreeCAD headless subprocess to generate a 3D BRep model.

        Args:
            coordinate_matrix: Resolved room coordinates from constraint solver.
                Format: {room_id: {"x": float, "y": float, "width": float, "height": float}}
            plan_state: Current floor plan state (used for metadata/labeling).

        Returns:
            Path to generated .FCStd file.

        Raises:
            CADGenerationError: If FreeCAD subprocess fails.
            NotImplementedError: Until Cursor Composer implements this.

        SLA: 5–15s
        """
        # TODO: Implement via Cursor Composer — see DRAFT_CAD_GENERATOR.md
        # Implementation outline:
        # 1. Check self.FREECADCMD exists (squashfs-root extracted)
        # 2. Write coordinate_matrix to a temp JSON file
        # 3. env = {**os.environ, "LD_LIBRARY_PATH": self.FREECAD_LIBS}
        #    subprocess.run(
        #        [str(self.FREECADCMD), "-c", f"exec(open('{script_path}').read())"],
        #        env=env, cwd=str(self.SQUASHFS_ROOT),
        #        input=json_payload, capture_output=True, timeout=30
        #    )
        # 4. Parse stdout for "FREECAD_OK: /path/to/file"
        # 5. Return Path to .FCStd
        # NOTE: Do NOT use --appimage-extract-and-run — use squashfs-root directly
        raise NotImplementedError(
            "CADGenerator.generate() is pending Cursor Composer implementation. "
            "See DRAFT_CAD_GENERATOR.md for the full spec."
        )
