FROM node:22-alpine AS build

WORKDIR /app

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY app/web/package.json ./app/web/package.json
COPY packages/shared-types/package.json ./packages/shared-types/package.json

RUN corepack enable \
    && corepack prepare pnpm@11.11.0 --activate \
    && pnpm install --frozen-lockfile --filter @ai-interviewer/web...

COPY app/web ./app/web
COPY packages/shared-types ./packages/shared-types

RUN pnpm --dir app/web build

FROM nginx:1.27-alpine

COPY app/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/app/web/dist /usr/share/nginx/html