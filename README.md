## jukebotx-service-full

A mono-repo for **JukeBotx**: a Discord music bot + companion API, built with a clean architecture mindset (domain-first, ports/adapters, use cases) and an “HTTP-only ingestion” approach for Suno links.

Use this repo to:

* run the bot locally,
* run the API locally,
* smoke-test Suno ingestion without any DB dependency,
* iterate safely with Make targets and a predictable PYTHONPATH layout.

---

## Table of Contents

* [What this repo contains](#what-this-repo-contains)
* [Architecture overview](#architecture-overview)
* [Tech stack](#tech-stack)
* [Repository layout](#repository-layout)
* [Local setup](#local-setup)
* [Environment variables](#environment-variables)
* [Quickstart matrix](#quickstart-matrix)
* [Env files map](#env-files-map)
* [Ports and URLs](#ports-and-urls)
* [Run locally](#run-locally)

  * [Bot](#bot)
  * [API](#api)
  * [Activity app](#activity-app)
  * [API endpoints](#api-endpoints)
* [Commands](#commands)
* [Smoke tests](#smoke-tests)
* [Development workflow](#development-workflow)
* [Make targets](#make-targets)
* [Docker](#docker)
* [Common issues](#common-issues)
* [Roadmap](#roadmap)
* [Contributing](#contributing)
* [License](#license)

---

## What this repo contains

**jukebotx-service-full** is the “full service” mono-repo for:

* **Discord Bot** (`apps/bot`)

  * Commands/cogs, queue interactions, voice playback
  * Permissions checks
  * FFmpeg-backed audio playback in voice channels
  * Auto-ingests Suno links into Postgres when the bot is active in a guild
  * Uses core use-cases to avoid bot-specific business logic
  * See `apps/bot/README.md` for bot-specific setup

* **API** (`apps/api`)

  * FastAPI service intended to expose ingestion/config/queue endpoints later
  * Currently structured to follow the same domain-first boundaries
  * See `apps/api/README.md` for API setup and docs

* **Activity app** (`apps/activity`)

  * Astro front end for a session activity/landing experience
  * See `apps/activity/README.md` for front-end specific setup

* **Core domain + use cases** (`packages/core`)

  * The “truth” of the system: entities, ports, use cases
  * No discord.py, no httpx, no infrastructure dependencies

* **Infrastructure adapters** (`packages/infra`)

  * HTTP clients and repo implementations that satisfy core ports
  * Async SQLAlchemy repositories + Postgres models (used by the bot for ingestion)
  * Suno scraping via **httpx** (no browser automation)
  * Postgres is the default persistence target

* **Scripts** (`scripts`)

  * Smoke tests and debugging helpers for incremental wiring

---

## Architecture overview

This project follows a **clean architecture / DDD-ish** approach:

### The rule that matters

**Core does not depend on infra.**

* Core defines **ports** (interfaces) like `SunoClient` and repositories.
* Infra implements those ports.
* Apps (bot/api) depend on core and wire in infra.

### Practical implications

* Enables unit testing use cases with fake ports.
* Allows swapping infra (in-memory vs Redis vs Postgres) without touching core.
* Keeps Discord logic in the bot layer, not mixed into domain logic.

---

## Tech stack

* **Python 3.11**
* **Poetry** for dependency management
* **discord.py** for the bot (in `apps/bot`)
* **FastAPI** for the API (in `apps/api`)
* **httpx** for Suno fetching/scraping (in `packages/infra`)
* **Postgres + SQLAlchemy (async)** for persistence (in `packages/infra`)
* **Makefile** for consistent commands
* **Docker Compose** for local container wiring (optional)

---

## Repository layout

```text
jukebotx-service-full/
├─ apps/
│  ├─ bot/
│  │  ├─ Dockerfile
│  │  └─ jukebotx_bot/
│  │     ├─ main.py
│  │     ├─ settings.py
│  │     └─ discord/
│  │        ├─ checks/
│  │        │  └─ permissions.py
│  │        ├─ cogs/
│  │        └─ events/
│  └─ api/
│     ├─ Dockerfile
│     └─ jukebotx_api/
│        └─ main.py
│  └─ activity/
│     ├─ astro.config.mjs
│     ├─ package.json
│     └─ src/
│        └─ pages/
│           └─ index.astro
├─ packages/
│  ├─ core/
│  │  └─ jukebotx_core/
│  │     ├─ domain/
│  │     ├─ ports/
│  │     └─ use_cases/
│  └─ infra/
│     └─ jukebotx_infra/
│        └─ suno/
│           └─ client.py
├─ scripts/
│  ├─ smoke_suno_client.py
│  ├─ smoke_find_lyrics_marker.py
│  └─ smoke_ingest.py
├─ Makefile
├─ pyproject.toml
├─ poetry.lock
└─ docker-compose.yml
```

---

## Local setup

### Prerequisites

* Python **3.11**
* Poetry installed
* FFmpeg installed (required for voice playback)
* Postgres available (required for Suno ingestion)

### Install dependencies

```bash
poetry install
```

---

## Environment variables

Copy `.env.example` to `.env` (or use the dev/prod templates):

```bash
cp .env.example .env
```

### Common env vars (expected)

Common keys:

* `DISCORD_TOKEN` — Discord bot token
* `DISCORD_GUILD_ID` — optional, for dev/testing slash command sync
* `LOG_LEVEL` — e.g. `INFO`
* `DATABASE_URL` — async SQLAlchemy DSN (defaults to local Postgres)
* `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — used by Docker Compose
* `DISCORD_OAUTH_CLIENT_ID`, `DISCORD_OAUTH_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URI` — OAuth config for the API
* `API_SESSION_SECRET`, `API_SESSION_TTL_SECONDS` — cookie signing + TTL for OAuth sessions
* `OPUS_CACHE_DIR`, `OPUS_CACHE_TTL_SECONDS` — local Opus cache location + TTL (API)
* `OPUS_API_BASE_URL` — base URL for the bot to request cached Opus audio (e.g., `http://localhost:8001`)
* `OPUS_STORAGE_PROVIDER` — set to `s3` to enable object storage for Opus files
* `OPUS_STORAGE_BUCKET` — bucket for Opus files (MinIO/S3)
* `OPUS_STORAGE_PREFIX` — prefix for Opus objects (defaults to `opus`)
* `OPUS_STORAGE_ENDPOINT_URL` — S3 endpoint URL (e.g., `http://localhost:9000` for MinIO)
* `OPUS_STORAGE_ACCESS_KEY_ID`, `OPUS_STORAGE_SECRET_ACCESS_KEY` — S3 credentials
* `OPUS_STORAGE_PUBLIC_BASE_URL` — public base URL for Opus objects (optional)
* `OPUS_STORAGE_SIGNED_URL_TTL_SECONDS` — TTL for signed URLs
* `OPUS_STORAGE_TTL_SECONDS` — TTL for objects before refresh
* `CORS_ALLOWED_ORIGINS` — comma-separated list of allowed browser origins for the API (include the Activity app URL,
  `https://discord.com`, and any Cloudflared Activity URL used inside Discord).
* `CORS_ALLOWED_ORIGIN_REGEX` — optional regex for dynamic Discord embeds
  (for example `https://.*\.discordsays\.com`).

> Do not commit `.env`. The repo should ignore it.

---

## Quickstart matrix

**Local (Docker backend + local Activity)**

```bash
cp .env.development.example .env.development
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
make activity-dev
```

**All-in-Docker (includes Activity)**

```bash
cp .env.development.example .env.development
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

**Tunnels (two hostnames)**

```bash
cp .env.production.example .env.production
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d
```

---

## Env files map

* `.env` — current local runtime config (used by default compose + make targets).
* `.env.development.example` / `.env.production.example` — templates for dev/prod.
* `.env.development` / `.env.production` — actual envs used with `docker-compose.dev.yml` or `docker-compose.prod.yml`.
* `apps/activity/.env` — local Activity frontend envs (public, browser-exposed).
* `apps/activity/.env.example` / `apps/activity/.env.production.example` — Activity templates.

---

## Ports and URLs

* Activity app: `http://localhost:4321`
* API (host): `http://localhost:8001`
* API (container network): `http://api:8000`
* Postgres: `localhost:5432`
* MinIO: `http://localhost:9000` (S3), `http://localhost:9001` (console)

---

## Run locally

### PYTHONPATH (important)

This repo uses a multi-package layout. Most commands assume:

```bash
PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra
```

Most commands assume this; prefer `make` targets.

---

## Bot

Example (target names may differ by Makefile):

```bash
make bot
```

Or manually:

```bash
PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra \
poetry run python -m jukebotx_bot.main
```

---

## API

Example:

```bash
make api
```

Or manually:

```bash
PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra \
poetry run uvicorn jukebotx_api.main:app --reload
```

---

## Activity app

```bash
cd apps/activity
npm install
npm run dev
```

The Activity app runs on <http://localhost:4321> by default.

When running the Activity inside Discord, set `PUBLIC_API_BASE_URL` in the root `.env`
to a publicly reachable API origin (for example the Cloudflared API URL) and rebuild
the Activity container so the client bundle picks up the new base URL
(`docker compose build activity`).

If the Activity runs inside Discord or a Cloudflared tunnel, ensure the API CORS settings
include the Activity origin and Discord embed origins (`CORS_ALLOWED_ORIGINS` and/or
`CORS_ALLOWED_ORIGIN_REGEX`).

---

## API endpoints

The API currently exposes read-only queue/session endpoints plus Discord OAuth for
authenticated access. All endpoints below require a valid `jukebotx_session` cookie
unless otherwise noted.

### Auth + session

* `GET /healthz` — basic health check (no auth required).
* `GET /auth/discord/login` — starts Discord OAuth flow (redirects to Discord).
* `GET /auth/discord/callback` — OAuth callback (sets `jukebotx_session` cookie).
* `POST /auth/logout` — clears session cookie and redirects to `/`.
* `GET /auth/me` — returns the authenticated user profile payload.

### Queue + tracks

* `GET /guilds/{guild_id}/queue?limit=10` — queue preview for a guild.
* `GET /guilds/{guild_id}/queue/next` — next unplayed queue item (if any).
* `GET /guilds/{guild_id}/channels/{channel_id}/session/tracks` — tracks submitted in a session channel.
* `GET /tracks/{track_id}` — track metadata by ID.
* `GET /tracks/{track_id}/audio` — redirects to the track MP3 URL (404 if missing).
* `GET /tracks/{track_id}/opus` — serves cached Opus audio for the track. Cached files are stored at
  `static/opus/{track_id}.opus` for up to `OPUS_CACHE_TTL_SECONDS` seconds before being re-transcoded.
  When `OPUS_STORAGE_PROVIDER=s3`, the API redirects to MinIO/S3 instead.

### Auth requirements

* The API expects Discord OAuth configuration to be present (`DISCORD_OAUTH_CLIENT_ID`,
  `DISCORD_OAUTH_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URI`,
  `DISCORD_GUILD_ID`, `API_SESSION_SECRET`).
* Requests are authorized against the guild IDs in the session payload; non-members
  receive a `403`.

---

## Commands

The bot uses **prefix commands** with `;` (configured in `apps/bot/jukebotx_bot/main.py`).

### Voice + queue

* `;join` — join the current voice channel
* `;leave` — disconnect and reset the session
* `;playlist <url>` — queue tracks from a Suno playlist URL
* `;q` — show now playing + next up
* `;np` — show now playing
* `;p` — start playback
* `;n` — skip (mod-only)
* `;s` — stop playback (mod-only)
* `;clear` — clear queue (mod-only)
* `;remove <index>` — remove item from queue (mod-only)

### Session controls

* `;open` — open submissions
* `;close` — close submissions
* `;limit <count>` — set per-user submission limit (mod-only)
* `;autoplay [count|off]` — auto-play up to `count` tracks or until empty (mod-only)
* `;dj [count|off]` — DJ mode for `count` tracks or until empty (mod-only)

### Web UI

* `;web` / `;sessionurl` — post the session UI link (requires `WEB_BASE_URL`)

### Announcements

* `;ping here <message>` — announce in the jam session channel (mod-only)
* `;ping jamsession <message>` — mention the jam session role (mod-only)

---

## Smoke tests

These are designed for fast feedback while wiring infra/adapters.

### Smoke: Suno client (metadata + lyrics best-effort)

```bash
make smoke-suno URL="https://suno.com/s/..."
```

Or manually:

```bash
PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra \
poetry run python scripts/smoke_suno_client.py "https://suno.com/s/..."
```

**Expected behavior:**

* `Title`, `Artist`, `Artist Username`
* `MP3` and `Image` from OpenGraph tags (reliable)
* `Lyrics` sometimes present, sometimes absent

  * Some tracks are `[Instrumental]` or have no lyric payload available.

### Smoke: detect lyric payload markers

```bash
PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra \
poetry run python scripts/smoke_find_lyrics_marker.py "https://suno.com/s/..."
```

This helps confirm what the server returned:

* whether Next.js streaming payload is present (`self.__next_f.push`)
* whether OpenGraph tags exist
* whether lyric markers exist in escaped payload strings

---

## Development workflow

### Commit strategy (recommended)

Keep commits small and stack them locally before pushing:

1. `chore:` repo hygiene (`.gitignore`, `.env.example`, Makefile)
2. `feat(infra):` Suno client + smoke scripts
3. `feat(core):` ports/use cases
4. `feat(bot):` wiring use cases into cogs
5. `feat(api):` wiring use cases into HTTP endpoints

### Avoid polluting git with caches

If `__pycache__` or `*.pyc` show up in the repo tree:

* confirm `.gitignore` ignores them
* delete them:

```bash
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
```

---

## Make targets

Typical targets:

* `make install` — `poetry install`
* `make bot` — run bot
* `make api` — run API
* `make fmt` — `ruff format .`
* `make lint` — `ruff check .` + `mypy .`
* `make test` — `pytest -q`
* `make smoke-suno URL=...` — run Suno client smoke test
* `make up` / `make up-d` — Docker Compose lifecycle helpers
* `make down` / `make destroy` — stop containers (destroy removes volumes)
* `make logs` / `make ps` / `make restart` — Compose status helpers
* `make db-shell` / `make db-reset` / `make db-backup` / `make db-restore` — Postgres helpers

Add or remove targets as needed for the current workflow.

---

## Docker

Typical Docker Compose commands:

```bash
docker compose up --build
```

Common services to run:

* `bot` service
* `api` service
* `db` service (Postgres)

By default, the app expects Postgres via `DATABASE_URL` (see `.env.example`).

---
---

## Common issues

### “Module not found” / imports failing

This repo relies on `PYTHONPATH` pointing at `apps/*` and `packages/*`.

Prefer `make ...` commands so the environment is consistent.

### Lyrics sometimes show `None`

That’s expected.
Suno pages frequently return server HTML without hydrated DOM elements. Lyrics can appear in:

* Next.js streaming payload (`self.__next_f.push([...])`)
* embedded JSON (`__NEXT_DATA__`) sometimes
* rarely in DOM paragraphs when fully rendered

Also: some songs are truly instrumental or don’t expose lyrics via the payload.

### Don’t commit `.env`

If `.env` was staged by accident:

```bash
git restore --staged .env
```

If secrets were committed, rotate them.

---

## Roadmap

### Done

- [x] Bot + API + Activity app wired in a single mono-repo
- [x] Activity frontend (Astro) with Discord Embedded SDK auth flow
- [x] Activity API endpoints (now playing, queue, state, reactions)
- [x] Docker Compose for core services + cloudflared tunnel services
- [x] Environment templates for dev/prod and per-app configs
- [x] API CORS configuration for Activity + Discord embeds

### In progress

- [ ] Stabilize Discord Activity auth flow and improve diagnostics
- [ ] Harden queue/now-playing polling and error handling
- [ ] Add more Activity UI states (empty queue, errors, loading)

### Next

- [ ] Wire Suno ingestion into a core use case (`ingest_suno_links`)
- [ ] Add an in-memory queue repo adapter
- [ ] Improve Suno lyric extraction heuristics and add tests for multiple URL types
- [ ] Add API endpoints for queue/config operations
- [ ] Add structured logging and correlation IDs

### Future

- [ ] Add persistence (Redis or Postgres) behind repository ports
- [ ] Multi-guild config support with a real config repository
- [ ] Better audio pipeline and voice stability (FFmpeg lifecycle management)
- [ ] Observability: metrics + health checks

---

## Contributing

Contribution guidelines:

* require PRs
* require `make lint` + `make test`
* keep changes inside layer boundaries (core vs infra vs apps)

---

## License

TBD.

---

## Discord Activity (Session View)
