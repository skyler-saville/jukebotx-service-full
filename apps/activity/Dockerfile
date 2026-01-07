FROM node:20-alpine

WORKDIR /app/apps/activity

COPY apps/activity/package*.json ./
RUN npm ci

COPY apps/activity/ ./

EXPOSE 4321

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "4321"]
