# Insurance AI — React frontend, built then served by nginx (proxies API + WS).
FROM node:20-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
# API base is same-origin in the container; nginx proxies /api and /ws to the API.
ENV VITE_API_BASE=""
ENV VITE_WS_BASE=""
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/web-nginx.conf /etc/nginx/nginx.conf
COPY --chown=nginx:nginx --from=build /web/dist /usr/share/nginx/html

# The custom configuration keeps all nginx runtime state under /tmp, allowing both
# an unprivileged process and a read-only root filesystem.
USER nginx

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1:8080/ >/dev/null 2>&1 || exit 1

ENTRYPOINT ["nginx", "-g", "daemon off;"]
