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
- Secrets like `DISCORD_CLIENT_SECRET` should stay in the root `.env` and never use the `PUBLIC_` prefix.
