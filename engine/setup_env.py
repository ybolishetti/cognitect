"""
setup_env.py — ensure FreeCAD runtime dependencies are available.

Called by CADGenerator before any subprocess invocation.
Idempotent: safe to call multiple times.
"""
import glob
import logging
import os
import platform
import shutil
import subprocess

LIBGL_TARGET = "/tmp/libgl1_extract/usr/lib/x86_64-linux-gnu/libGL.so.1"
LIBGL_EXTRACT_DIR = "/tmp/libgl1_extract"

logger = logging.getLogger(__name__)


def ensure_libgl() -> str:
    """
    Ensure libGL.so.1 is available for FreeCAD.
    Downloads and extracts libgl1 deb if not present.

    Returns:
        Path to the directory containing libGL.so.1.

    Raises:
        RuntimeError: If download or extraction fails.
    """
    if os.path.exists(LIBGL_TARGET):
        return LIBGL_EXTRACT_DIR

    if platform.system() != "Linux" or shutil.which("apt-get") is None:
        logger.warning(
            "libGL.so.1 not found and apt-get unavailable — skipping extraction "
            "(expected on non-Linux dev hosts)"
        )
        return LIBGL_EXTRACT_DIR

    logger.info("libGL.so.1 not found — downloading and extracting...")

    # Download debs
    debs = ["libgl1", "libglx0", "libglvnd0"]
    for pkg in debs:
        result = subprocess.run(
            ["apt-get", "download", pkg],
            capture_output=True,
            text=True,
            cwd="/tmp",
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to download {pkg}: {result.stderr}")

    # Extract debs
    for deb in glob.glob("/tmp/*.deb"):
        result = subprocess.run(
            ["dpkg", "-x", deb, LIBGL_EXTRACT_DIR],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to extract {deb}: {result.stderr}")

    if not os.path.exists(LIBGL_TARGET):
        raise RuntimeError(f"libGL extraction completed but {LIBGL_TARGET} not found")

    logger.info("libGL ready at %s", LIBGL_EXTRACT_DIR)
    return LIBGL_EXTRACT_DIR
