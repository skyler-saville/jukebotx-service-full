# Activity app

A minimal Astro front end for the JukeBotx activity view.

## Run locally

```bash
cd apps/activity
npm install
npm run dev
```

Astro will start the dev server at <http://localhost:4321>.

## Environment variables

- `PUBLIC_ACTIVITY_CLIENT_ID` lives in `apps/activity/.env` and is exposed to the browser.
  It must match the API's `DISCORD_ACTIVITY_CLIENT_ID` so both use the same Discord Activity app.
- `PUBLIC_API_BASE_URL` must be a publicly reachable API origin when running the Activity in Discord (for example a Cloudflared URL).
- `apps/activity/.env.production.example` is a template for tunnel/production values.
- When running inside Discord, ensure the API CORS settings include the Activity origin and Discord embed origins
  (for example `https://jukebotx.cortocast.com` and `https://discord.com`).
- Secrets like `DISCORD_OAUTH_CLIENT_SECRET` and `DISCORD_ACTIVITY_CLIENT_SECRET` should stay in the root `.env`.
