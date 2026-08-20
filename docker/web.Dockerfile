# Insurance AI — React frontend, built then served by nginx (proxies API + WS).
FROM node:20-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY apps/web/ ./
# API base is same-origin in the container; nginx proxies /api and /ws to the API.
ENV VITE_API_BASE=""
ENV VITE_WS_BASE=""
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/web-nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1
