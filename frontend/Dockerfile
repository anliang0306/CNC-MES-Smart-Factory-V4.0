# Production image: build the bundle once, then serve it with `vite preview`.
# (The old `npm run dev` container ran a dev server with HMR in production.)
FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build
EXPOSE 4173
CMD ["npm", "run", "preview"]
