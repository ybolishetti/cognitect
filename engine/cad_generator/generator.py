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
            # When a custom AppImage path is supplied, derive squashfs-root relative to it
            # so that the missing-binary check reflects the override (important for tests).
            custom_root = self.appimage_path.parent / "squashfs-root"
            self.SQUASHFS_ROOT = custom_root
            self.FREECADCMD = custom_root / "usr/bin/freecadcmd"
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

        SLA: 5–15s
        """
        import json
        import os
        import subprocess
        import tempfile

        from engine.setup_env import ensure_libgl

        # 1. Ensure libGL is available
        libgl_dir = ensure_libgl()

        # 2. Verify FreeCAD binary exists
        if not self.FREECADCMD.exists():
            raise CADGenerationError(
                f"FreeCAD binary not found: {self.FREECADCMD}. "
                "Is the squashfs-root extracted?",
                exit_code=1,
            )

        # 3. Write coordinate_matrix + output path to a temp input JSON file
        output_dir = Path(tempfile.gettempdir()) / "cognitect_output"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"{plan_state.plan_id}.FCStd"

        payload = {
            "coordinate_matrix": coordinate_matrix,
            "output_path": str(output_path),
            "plan_id": plan_state.plan_id,
            "rooms": {
                room_id: {
                    "name": spec.name,
                    "room_type": spec.room_type,
                }
                for room_id, spec in plan_state.rooms.items()
            },
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(payload, f)
            input_json_path = f.name

        # 4. Build the FreeCAD script invocation command
        script_path = Path(__file__).parent / "freecad_scripts" / "generate_plan.py"

        env = {
            **os.environ,
            "LD_LIBRARY_PATH": f"{libgl_dir}/usr/lib/x86_64-linux-gnu",
            "QT_QPA_PLATFORM": "offscreen",
        }

        cmd = [
            str(self.FREECADCMD),
            "-c",
            (
                f"import json, sys; sys.stdin = open('{input_json_path}'); "
                f"exec(open('{script_path}').read())"
            ),
        ]

        # 5. Run FreeCAD subprocess
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                cwd=str(self.SQUASHFS_ROOT),
            )
        except subprocess.TimeoutExpired:
            raise CADGenerationError(
                f"FreeCAD subprocess timed out after 30s for plan {plan_state.plan_id}",
                exit_code=-1,
            )
        finally:
            try:
                os.unlink(input_json_path)
            except OSError:
                pass

        # 6. Check for success sentinel in stdout
        stdout = result.stdout or ""
        stderr = result.stderr or ""

        if result.returncode != 0 or "FREECAD_OK:" not in stdout:
            raise CADGenerationError(
                f"FreeCAD subprocess failed for plan {plan_state.plan_id}",
                stderr=stderr,
                exit_code=result.returncode,
            )

        # 7. Parse output path from sentinel line
        for line in stdout.splitlines():
            if line.startswith("FREECAD_OK:"):
                out_path = Path(line.split(":", 1)[1].strip())
                if out_path.exists():
                    return out_path

        raise CADGenerationError(
            f"FreeCAD reported OK but output file not found. stdout: {stdout[:200]}",
            stderr=stderr,
            exit_code=0,
        )
