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
- `PUBLIC_API_BASE_URL` must be a publicly reachable API origin when running the Activity in Discord (for example a Cloudflared URL).
- When running inside Discord, ensure the API CORS settings include the Activity origin (`CORS_ALLOWED_ORIGINS`)
  and Discord embed origins (for example `https://discord.com` and `https://.*\.discordsays\.com`).
- Secrets like `DISCORD_CLIENT_SECRET` should stay in the root `.env` and never use the `PUBLIC_` prefix.
