FROM node:20-slim AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
# Vite inlines import.meta.env.* into the bundle at BUILD time, not runtime --
# a docker-compose `environment:` entry would have no effect on the already-
# built static files. Must be a build ARG, passed via compose `build.args`.
ARG VITE_API_BASE=http://localhost:8000
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM node:20-slim
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist
EXPOSE 5173
CMD ["serve", "-s", "dist", "-l", "5173"]
