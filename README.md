## jukebotx-service-full

A mono-repo for **JukeBotx**: a Discord music bot + companion API, built with a clean architecture mindset (domain-first, ports/adapters, use cases) and a **Lavalink-only** voice playback stack.

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
* [Documentation](#documentation)
* [Local setup](#local-setup)
* [Environment variables](#environment-variables)
* [Product model](#product-model)
* [Run locally](#run-locally)

  * [Bot](#bot)
  * [API](#api)
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
  * Lavalink-backed playback in voice channels
  * Auto-ingests Suno links into Postgres when the bot is active in a guild
  * Uses core use-cases to avoid bot-specific business logic

* **API** (`apps/api`)

  * FastAPI service intended to expose ingestion/config/queue endpoints later
  * Currently structured to follow the same domain-first boundaries

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
* **Lavalink** for voice playback
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
│  ├─ smoke_lavalink.py
│  ├─ smoke_find_lyrics_marker.py
│  └─ smoke_ingest.py
├─ docs/
│  ├─ README.md
│  ├─ architecture.md
│  ├─ ddd.md
│  ├─ media-pipeline.md
│  ├─ web-listener.md
│  ├─ voice-lavalink.md
│  └─ operations.md
├─ Makefile
├─ pyproject.toml
├─ poetry.lock
└─ docker-compose.yml
```

---

## Documentation

Project docs live in [`docs/`](docs/README.md):

* [`docs/architecture.md`](docs/architecture.md) — system boundaries, runtime surfaces, and ownership by layer
* [`docs/ddd.md`](docs/ddd.md) — DDD and clean-architecture guardrails for future changes
* [`docs/web-listener.md`](docs/web-listener.md) — public listener flow, session model, and auth split
* [`docs/media-pipeline.md`](docs/media-pipeline.md) — worker/audio artifact pipeline, storage modes, and delivery behavior
* [`docs/voice-lavalink.md`](docs/voice-lavalink.md) — Lavalink flow, guild/player model, failure modes
* [`docs/operations.md`](docs/operations.md) — local runbook, smoke tests, logs, incident triage

---

## Local setup

### Prerequisites

* Python **3.11**
* Poetry installed
* Postgres available (required for Suno ingestion)
* Docker (recommended for running Lavalink locally)

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
* `MASTER_USER_ID` — Discord user ID for the “master” profile; this user bypasses normal mod-role checks and is the only one allowed to use playlist export commands
* `LOG_LEVEL` — e.g. `INFO`
* `DATABASE_URL` — async SQLAlchemy DSN (defaults to local Postgres)
* `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — used by Docker Compose
* `DISCORD_OAUTH_CLIENT_ID`, `DISCORD_OAUTH_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URI` — OAuth config for the API
* `API_SESSION_SECRET`, `API_SESSION_TTL_SECONDS` — cookie signing + TTL for OAuth sessions
* `PUBLIC_API_BASE_URL` — public API origin used by the web shell and shareable links (for this setup: `https://jukebotx-api.cortocast.com`)
* `PUBLIC_FRONTEND_URL` — public frontend origin (for this setup: `https://cortocast.com` or your chosen web hostname)
* `WEB_BASE_URL` — session URL base the bot posts into Discord (for this setup: `https://jukebotx.cortocast.com`)
* `OPUS_CACHE_DIR`, `OPUS_CACHE_TTL_SECONDS` — local Opus cache location + TTL (API)
* `OPUS_API_BASE_URL` — base URL for the bot to request cached Opus audio (e.g., `http://localhost:8001`)
* `AUDIO_RELAY_BASE_URL` — optional internal URL for a live browser-audio relay sidecar
* `AUDIO_RELAY_TOKEN` — optional bearer token shared with the relay
* `AUDIO_RELAY_TIMEOUT_SECONDS` — maximum time to wait for a relay stream to become ready (defaults to `30`)
* `OPUS_STORAGE_PROVIDER` — set to `s3` to enable object storage for Opus files
* `OPUS_STORAGE_BUCKET` — bucket for Opus files (MinIO/S3)
* `OPUS_STORAGE_PREFIX` — prefix for Opus objects (defaults to `opus`)
* `OPUS_STORAGE_ENDPOINT_URL` — S3 endpoint URL (e.g., `http://localhost:9000` for MinIO)
* `OPUS_STORAGE_ACCESS_KEY_ID`, `OPUS_STORAGE_SECRET_ACCESS_KEY` — S3 credentials
* `OPUS_STORAGE_PUBLIC_BASE_URL` — public base URL for Opus objects (optional)
* `OPUS_STORAGE_SIGNED_URL_TTL_SECONDS` — TTL for signed URLs
* `OPUS_STORAGE_TTL_SECONDS` — TTL for objects before refresh
* `PLAYLIST_ARCHIVE_STORAGE_PREFIX` — bucket prefix for uploaded playlist zip exports (defaults to `downloads/playlists`)
* `PLAYLIST_DOWNLOAD_LINK_TTL_SECONDS` — how long signed playlist download links stay valid (defaults to `86400`)
* `MEDIA_GIF_ENABLED` — enable worker MP4-to-GIF backfill for track art previews (defaults to `true`)

> Do not commit `.env`. The repo should ignore it.

### Public routing

This repo no longer runs the Cloudflare tunnel connector directly.

Current split:

* `jukebotx_dev` owns app/runtime config such as `PUBLIC_API_BASE_URL`, `PUBLIC_FRONTEND_URL`, and `WEB_BASE_URL`
* the reverse proxy repo (`pi_reverse_proxy`) owns `cloudflared`, Traefik, and tunnel credentials

Public hostnames in the current setup:

* Web UI: `https://jukebotx.cortocast.com`
* API: `https://jukebotx-api.cortocast.com`

---

## Product model

The web experience is **session-first**, not track-first.

### Core session model

* One Discord listening party maps to one persisted app-level `session_id` (UUID).
* Public listener route is intended to be `/listen/{session_id}`.
* A Discord DJ/mod must activate the session before the web route is usable.
* Until activated, the session should be treated as unavailable or waiting for host.

### Listener experience

* The web client should load session state from the persisted `session_id`.
* A listener session should expose:
  * current track
  * queue preview
  * artwork
  * metadata
  * lyrics
  * playback timing/state

### Visibility and discovery

* Sessions should be treated as `unlisted` by default.
* The main `jukebotx.cortocast.com` page should not show a global session directory in the initial version.
* Access should happen through a direct link posted from Discord.
* If public discovery is added later, it should be an explicit visibility setting, not the default.

### Authentication

* Anonymous users should be able to view and listen to an active unlisted/public session.
* Discord authentication should be required only for privileged actions such as activating or managing a session.
* The web listener flow should minimize friction for mobile users coming from Discord.

### Data ownership

* Postgres should hold canonical session and track state.
* MinIO/object storage should hold media artifacts such as Ogg/Opus audio and GIF artwork.
* Near-live playback position should be derived from persisted timing fields such as `started_at` and offsets, not by writing position every second.

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
* `POST /guilds/{guild_id}/channels/{channel_id}/web-session` — activate or refresh a persisted public web listening session for a Discord channel.
* `GET /tracks/{track_id}` — track metadata by ID.
* `GET /tracks/{track_id}/audio` — redirects to the track MP3 URL (404 if missing).
* `GET /tracks/{track_id}/opus` — serves cached Opus audio for the track. Cached files are stored at
  `static/opus/{track_id}.opus` for up to `OPUS_CACHE_TTL_SECONDS` seconds before being re-transcoded.
  When `OPUS_STORAGE_PROVIDER=s3`, the API redirects to MinIO/S3 instead.
* `GET /tracks/{track_id}/web-audio` — authenticated track-level browser audio endpoint. Serves browser-oriented Ogg/Opus audio (`audio/ogg`) when ready; otherwise enqueues generation and falls back to the source MP3 redirect.
* `GET /tracks/{track_id}/web-audio/status` — readiness/status for the browser-oriented Ogg/Opus artifact.

### Public web session endpoints

These endpoints are intended for the web listener flow and do not rely on guild-cookie authorization.

* `GET /sessions/{session_id}` — public listener snapshot by persisted session ID, including session status, current track metadata/lyrics, queue preview, and the current session audio path when a DJ has activated the session.
* `GET /sessions/{session_id}/audio` — public session-scoped browser audio route for the active track. This keeps anonymous listening tied to a live session instead of exposing arbitrary track playback.

### Auth requirements

* The API expects Discord OAuth configuration to be present (`DISCORD_OAUTH_CLIENT_ID`,
  `DISCORD_OAUTH_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URI`,
  `DISCORD_GUILD_ID`, `API_SESSION_SECRET`).
* Requests are authorized against the guild IDs in the session payload; non-members
  receive a `403`.

---

## Commands

The bot supports both:

* **Prefix commands** with `;`
* **Slash commands** (`/`) for the same core playback/session flow

### Permissions model

* Regular hosts/mods/admins/DJs can use the normal session and queue controls.
* The user configured in `MASTER_USER_ID` has full command access and is the only profile allowed to use playlist download/export commands.
* `;playlist <url>` and `/playlist <url>` are session-building tools: they immediately close submissions, clear the current queue, then fetch and queue the playlist tracks.

### Voice + queue

* `;join` — join your current voice channel
* `;leave` — disconnect and reset the session
* `;playlist <url>` — start a new playlist session by closing submissions, clearing the queue, and queueing tracks from a Suno playlist URL
* `;q` — show now playing + next up
* `;np` — show now playing
* `;p` — start playback
* `;n` — skip (mod-only)
* `;s` — stop playback (mod-only)
* `;clear` — clear queue (mod-only)
* `;remove <index>` — remove item from queue (mod-only)

Slash equivalents include:

* `/join`, `/leave`, `/queue`, `/nowplaying`, `/play`, `/skip`, `/stop`, `/playlist`

### Master-only playlist export

These commands are reserved for the `MASTER_USER_ID` profile:

* `;playlist-dl <url>` — fetch a Suno playlist and DM a zip of the audio files; if the archive is too large for Discord, the bot uploads it to object storage and DMs a signed API download link instead
* `/playlist-dl <url>` — slash-command version of the same export flow

### Admin UX (slash)

For role-based controls (admins/mods/DJs), use the `/admin` group:

* `/admin submissions` — open/close submissions
* `/admin limit` — per-user submission limit
* `/admin autoplay` — off / until empty / count
* `/admin dj` — off / until empty / count
* `/admin clear` — clear queue
* `/admin remove` — remove queue item by index

### Session controls

* `;open` — open submissions
* `;close` — close submissions
* `;limit <count>` — set per-user submission limit (mod-only)
* `;autoplay [count|off]` — auto-play up to `count` tracks or until empty (mod-only)
* `;dj [count|off]` — DJ mode for `count` tracks or until empty (mod-only)

### Web UI

* `;web` / `;sessionurl` — post the session UI link (requires `WEB_BASE_URL`)
  The intended public listener route is session-based, for example `/listen/{session_id}`.

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

### Smoke: Lavalink node + track loading

```bash
make smoke-lavalink URL="https://cdn1.suno.ai/<track>.mp3"
```

Use a direct audio URL for this smoke test. Suno page URLs are not valid Lavalink track identifiers.

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
* `docker compose up -d --build bot` — rebuild/recreate the bot container after bot code changes
* `make db-shell` / `make db-reset` / `make db-backup` / `make db-restore` — Postgres helpers

If a target doesn’t exist yet, add it—Make is your “team interface” even if the team is just you.

---

## Docker

This repo supports environment-specific Compose overlays so you can run multiple stacks (for example `development` and `production`) at the same time without port or container-name conflicts.

1. Copy `.env.example` to `.env`.
2. Set `ENV=development` or `ENV=production`.
3. Start services:

```bash
docker compose up --build
```

When you change bot source code and the bot is running in Docker, a plain
`docker compose restart bot` is not enough. The bot service in
[`docker-compose.yml`](docker-compose.yml) is built into an image and does not
bind-mount the repo into `/app`, so code changes require a rebuild:

```bash
docker compose up -d --build bot
```

Use `docker compose restart bot` only when you want to restart the existing image
without picking up local code edits.

Compose reads:

* `COMPOSE_PROJECT_NAME=jukebotx_${ENV}` (isolates container/volume/network names)
* `COMPOSE_FILE=docker-compose.yml:docker-compose.${ENV}.yml` (loads environment-specific port mappings)

Default host ports are separated by environment (dev uses `5432/8001/9000/9001`, prod uses `15432/18001/19000/19001`) and can be overridden in `.env`.

You’ll likely run either:

* `bot` service
* `api` service
* `db` service (Postgres)

By default, the app expects Postgres via `DATABASE_URL` (see `.env.example`).

### Voice backend (Lavalink)

When running the bot with `VOICE_BACKEND=lavalink`,
the Compose stack includes a managed `lavalink` service.
Use these environment values:

* `VOICE_BACKEND=lavalink`
* `LAVALINK_HOST=lavalink`
* `LAVALINK_PORT=2333`
* `LAVALINK_PASSWORD=...`

`docker-compose.yml` maps `LAVALINK_PASSWORD` to Lavalink's `LAVALINK_SERVER_PASSWORD` internally.
The default local JVM setting is `_JAVA_OPTIONS=-Xmx1G` (override with `LAVALINK_JAVA_OPTIONS` if needed).

This codebase now runs in Lavalink-only mode; `VOICE_BACKEND` must remain `lavalink`.

Tracks with a direct MP3 continue through the existing cache/Lavalink path. When a
track has only a source page URL and `AUDIO_RELAY_BASE_URL` is configured, the bot
asks the relay for a live stream URL and hands that URL to Lavalink. See
[`docs/audio-relay.md`](docs/audio-relay.md) for the sidecar contract and lifecycle.

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
* Stronger voice reliability and reconnect behavior around Discord/Lavalink session churn
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
