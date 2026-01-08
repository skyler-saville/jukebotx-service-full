# Bot service

Discord music bot for JukeBotx. Handles playback, queue commands, and ingestion triggers.

## Run locally

```bash
make bot
```

Or manually:

```bash
PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra \
poetry run python -m jukebotx_bot.main
```

## Docker

```bash
docker compose up bot
```

## Environment

Uses the root `.env` (or `.env.development` / `.env.production` with compose overrides). Common vars:

- `DISCORD_TOKEN` or `DEV_DISCORD_TOKEN`
- `DISCORD_GUILD_ID`
- `OPUS_API_BASE_URL`
- `WEB_BASE_URL` (used by `;web` / `;sessionurl`)
