FROM node:22-bookworm-slim AS builder
WORKDIR /app
ARG VITE_API_BASE
ENV VITE_API_BASE=$VITE_API_BASE
COPY package.json package-lock.json* ./
RUN rm -f package-lock.json npm-shrinkwrap.json && npm install --no-audit --no-fund --cache /tmp/npm-cache
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]