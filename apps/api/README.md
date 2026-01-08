# API service

FastAPI backend for JukeBotx. Provides OAuth, session state, queue, and activity endpoints.

## Run locally

```bash
make api
```

Or manually:

```bash
PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra \
poetry run uvicorn jukebotx_api.main:app --reload
```

## Docker

```bash
docker compose up api
```

## Environment

Uses the root `.env` (or `.env.development` / `.env.production` with compose overrides). Key vars:

- `DISCORD_OAUTH_CLIENT_ID`, `DISCORD_OAUTH_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URI`
- `DISCORD_ACTIVITY_CLIENT_ID`, `DISCORD_ACTIVITY_CLIENT_SECRET`, `DISCORD_ACTIVITY_REDIRECT_URI`
- `API_SESSION_SECRET`, `API_SESSION_TTL_SECONDS`
- `CORS_ALLOWED_ORIGINS`

## Docs

- Docker: `http://localhost:8001/docs`
- Local uvicorn: `http://localhost:8000/docs`
