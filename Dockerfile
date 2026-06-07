FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Runtime environment (override via docker-compose or K8s secrets)
ENV COGNITECT_CLAUDE_API_KEY=""
ENV DATABASE_URL="postgresql://cognitect:cognitect@postgres:5432/cognitect"
ENV REDIS_URL="redis://redis:6379/0"
ENV FREECAD_APPIMAGE_PATH="/data/workspace/freecad/FreeCAD.AppImage"
ENV LOG_LEVEL="INFO"
ENV PORT="8000"

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
