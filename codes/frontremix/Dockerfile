# CineMeridian console.
#
# Two stages so the runtime image carries no build toolchain and no dev
# dependencies. The console holds no secrets of its own - it reads everything
# through the API - so the only thing it needs at runtime is that API's URL.

FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:20-slim
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY --from=build /app/build ./build

# Cloud Run sets PORT; remix-serve reads it.
ENV PORT=8080
EXPOSE 8080
CMD ["npx", "remix-serve", "./build/server/index.js"]
