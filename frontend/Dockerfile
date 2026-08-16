FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /frontend
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm config set registry https://mirrors.cloud.tencent.com/npm/ \
    && pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /frontend/dist /usr/share/nginx/html
EXPOSE 80
