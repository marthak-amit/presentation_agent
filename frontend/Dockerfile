FROM node:20-slim
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
ENV BACKEND_URL=http://backend:8000
EXPOSE 5173
CMD ["npx", "vite", "--host", "0.0.0.0", "--port", "5173"]
