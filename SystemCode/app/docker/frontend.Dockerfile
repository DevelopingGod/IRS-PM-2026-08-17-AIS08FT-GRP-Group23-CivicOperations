# The three portals, built once and served by nginx, which also forwards /api to the backend.
FROM node:24-alpine AS build
WORKDIR /web
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci
COPY app/frontend/ ./
RUN npm run build

FROM nginx:1.29-alpine
COPY app/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
