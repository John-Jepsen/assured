# Insurance AI — React frontend, built then served by nginx (proxies API + WS).
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
# API base is same-origin in the container; nginx proxies /api and /ws to the API.
ENV VITE_API_BASE=""
ENV VITE_WS_BASE=""
RUN npm run build

FROM nginx:1.31-alpine@sha256:72ba65eb42c10344912a84ff42408db7d34f2feb642204570ab8fc5ffd29f1d3
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
