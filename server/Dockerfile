# Build context is the repo root (see docker-compose.yml) so the image gets common/ and web/ too.

# 1. Build the dashboard PWA (web/ -> web/dist).
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# 2. The API, serving the PWA at /app/.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY common/ common/
COPY server/ server/
COPY --from=web /web/dist web/dist

EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
