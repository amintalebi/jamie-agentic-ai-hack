# syntax=docker/dockerfile:1
# Dashboard PWA: build with Node, serve the static files with nginx (non-root, port 8080).
# Build context is web/:  docker build -t jamie-web ./web
# /api/ is proxied to API_URL (default http://server:8000, the compose service), so the
# app and its API share one origin: no CORS, and the service worker scope stays /app/.

# --- 1. build -------------------------------------------------------------------
FROM node:24.22.1-alpine AS build
WORKDIR /web

# Dependencies first: this layer is reused until package*.json change.
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

COPY index.html vite.config.ts tsconfig.json ./
COPY public/ public/
COPY src/ src/
RUN npm run build \
 # Precompress text files once, so nginx serves .gz without compressing per request.
 && find dist -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.svg' \
      -o -name '*.webmanifest' -o -name '*.json' \) -size +1k \
      -exec sh -c 'gzip -9 -n -c "$1" > "$1.gz"' _ {} \;

# --- 2. serve -------------------------------------------------------------------
FROM nginxinc/nginx-unprivileged:1.30.6-alpine

ENV API_URL=http://server:8000
# The image renders templates/*.template into conf.d/ with envsubst at start (only API_URL here).
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /web/dist /usr/share/nginx/html/app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
