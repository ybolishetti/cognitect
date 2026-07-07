# Production image for Fly.io. Local dev uses Dockerfile.dev + docker-compose
# instead (FreeCAD mounted as a volume there rather than baked in).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates fuse libfuse2 libgl1 libglu1-mesa libxrender1 libxext6 libsm6 libxcb1 \
    libxkbcommon0 libdbus-1-3 libnss3 libasound2 libx11-xcb1 libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*

# NOTE: engine/cad_generator/generator.py hardcodes
# SQUASHFS_ROOT = Path("/data/workspace/cognitect/squashfs-root") as a class
# attribute, and only recomputes it when CADGenerator() is constructed with an
# explicit freecad_appimage_path arg — which nothing in this codebase does
# (PlanManager always calls CADGenerator() with no args). FREECAD_APPIMAGE_PATH
# is therefore NOT read on this path; the squashfs-root must live at this exact
# location for 3D export (mode=3d) to find the FreeCAD binary. engine/ is
# frozen for this work, so the image layout accommodates the hardcoded path
# rather than patching it.
WORKDIR /data/workspace/cognitect
RUN curl -L -o freecad.AppImage \
      https://github.com/FreeCAD/FreeCAD/releases/download/1.0.0/FreeCAD_1.0.0-conda-Linux-x86_64-py311.AppImage \
    && chmod +x freecad.AppImage \
    && ./freecad.AppImage --appimage-extract \
    && rm freecad.AppImage

ENV LD_LIBRARY_PATH=/data/workspace/cognitect/squashfs-root/usr/lib:/data/workspace/cognitect/squashfs-root/usr/lib/x86_64-linux-gnu \
    QT_QPA_PLATFORM=offscreen \
    PYTHONUNBUFFERED=1 \
    FREECAD_APPIMAGE_PATH=/data/workspace/cognitect/freecad.AppImage

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
