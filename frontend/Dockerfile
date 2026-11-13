# AskInglis frontend: build the React site, then serve the files with nginx.
# Build context: the frontend/ folder.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# The backend address is written into the site at build time, so build once per place you deploy.
ARG VITE_BACKEND_URL=http://localhost:8000
ENV VITE_BACKEND_URL=$VITE_BACKEND_URL
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
# vite.config.js sets base '/ask_inglis_chatbot/', so the site must live under that path.
COPY --from=build /app/dist /usr/share/nginx/html/ask_inglis_chatbot
EXPOSE 8080
