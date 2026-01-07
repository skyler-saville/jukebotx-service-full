## jukebotx-service-full

A mono-repo for **JukeBotx**: a Discord music bot + companion API, built with a clean architecture mindset (domain-first, ports/adapters, use cases) and an “HTTP-only ingestion” approach for Suno links.

This repo is set up so you can:

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

* **API** (`apps/api`)

  * FastAPI service intended to expose ingestion/config/queue endpoints later
  * Currently structured to follow the same domain-first boundaries

* **Activity app** (`apps/activity`)

  * Astro front end for a session activity/landing experience

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

### Why this matters (practically)

* You can unit test use cases with fake ports.
* You can swap infra (in-memory vs Redis vs Postgres) without touching core.
* Discord logic stays in the bot layer, not mixed into domain logic.

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

You should **copy** `.env.example` to `.env`:

```bash
cp .env.example .env
```

### Common env vars (expected)

These names may evolve, but the usual suspects are:

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

> Do not commit `.env`. The repo should ignore it.

---

## Run locally

### PYTHONPATH (important)

This repo uses a multi-package layout. Most commands assume:

```bash
PYTHONPATH=apps/bot:apps/api:packages/core:packages/infra
```

You typically won’t type that manually—use `make` targets.

---

## Bot

Example (exact target names may differ based on your Makefile):

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

* `;join` — join your current voice channel
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

**What you should expect:**

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

If you see `__pycache__` or `*.pyc` in your repo tree:

* confirm `.gitignore` ignores them
* delete them:

```bash
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
```

---

## Make targets

Your repo already uses Make (good). Typical targets to include:

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

If a target doesn’t exist yet, add it—Make is your “team interface” even if the team is just you.

---

## Docker

If you’re using Docker Compose, typical commands:

```bash
docker compose up --build
```

You’ll likely run either:

* `bot` service
* `api` service
* `db` service (Postgres)

By default, the app expects Postgres via `DATABASE_URL` (see `.env.example`).

---


---

## Common issues

### “Module not found” / imports failing

This repo relies on `PYTHONPATH` pointing at `apps/*` and `packages/*`.

Use `make ...` commands so you don’t forget it.

### Lyrics sometimes show `None`

That’s expected.
Suno pages frequently return server HTML without hydrated DOM elements. Lyrics can appear in:

* Next.js streaming payload (`self.__next_f.push([...])`) ✅ (what your current solution targets)
* embedded JSON (`__NEXT_DATA__`) sometimes
* rarely in DOM paragraphs when fully rendered

Also: some songs are truly instrumental or don’t expose lyrics via the payload.

### Don’t commit `.env`

If you accidentally staged it:

```bash
git restore --staged .env
```

If you committed secrets, rotate them.

---

## Roadmap

Short-term (high confidence):

* Wire Suno ingestion into a core use case (`ingest_suno_links`)
* Add an in-memory queue repo adapter (already implied)
* Improve Suno lyric extraction heuristics and add tests for multiple URL types

Mid-term:

* Add persistence (Redis or Postgres) behind repository ports
* Add API endpoints for queue/config operations
* Add structured logging and correlation IDs

Long-term:

* Multi-guild config support with a real config repository
* Better audio pipeline and voice stability (FFmpeg lifecycle management)
* Observability: metrics + health checks

---

## Contributing

If you want outside contributions later:

* require PRs
* require `make lint` + `make test`
* keep changes inside layer boundaries (core vs infra vs apps)

---

## License

TBD.

---

## Discord Activity (Session View)
