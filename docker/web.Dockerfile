# Insurance AI — React frontend, built then served by nginx (proxies API + WS).
FROM node:26-alpine@sha256:ef24c5053d50fdc3e4e56eb4e7ddb7861874ab0fdc797046ba897581deb8e868 AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
# API base is same-origin in the container; nginx proxies /api and /ws to the API.
ENV VITE_API_BASE=""
ENV VITE_WS_BASE=""
RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
RUN apk upgrade --no-cache
COPY docker/web-nginx.conf /etc/nginx/nginx.conf
COPY --chown=nginx:nginx --from=build /web/dist /usr/share/nginx/html

# The custom configuration keeps all nginx runtime state under /tmp, allowing both
# an unprivileged process and a read-only root filesystem.
USER nginx

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1:8080/ >/dev/null 2>&1 || exit 1

ENTRYPOINT ["nginx", "-g", "daemon off;"]
