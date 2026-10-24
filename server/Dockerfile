# syntax=docker/dockerfile:1
# Build context is the repo root (see docker-compose.yml) so the image gets common/ too.
# The dashboard PWA has its own image (web/Dockerfile).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r server/requirements.txt

COPY common/ common/
COPY server/ server/

EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
