# Architecture

## Overview

JukeBotx is a monorepo with three primary runtime surfaces:

- Bot (`apps/bot`): Discord commands, session orchestration, playback control.
- API (`apps/api`): OAuth/session and queue/track HTTP endpoints.
- Web (`apps/web`): thin listener-facing web shell and API proxy.
- Worker (`apps/worker`): background media jobs.

Shared layers:

- Core (`packages/core`): use cases + ports. No Discord, HTTP clients, or DB dependencies.
- Infra (`packages/infra`): adapters for DB, Suno HTTP scraping, and storage.

## Product Direction

The current product direction is session-first:

- one Discord listening party maps to one persisted app-level `session_id`
- public listening happens through a session route, not through a generic public track browser
- Discord remains the host/control surface
- web remains the low-friction listener surface

That direction should shape future APIs and data modeling decisions.

## Dependency Direction

The dependency rule is strict:

- `core` does not depend on `infra` or app layers.
- app layers depend on `core` and wire infra adapters.
- infra implements core ports.

This keeps business logic testable and infrastructure-swappable.

In practical terms:

- `packages/core` should not import Discord, FastAPI, SQLAlchemy, boto3, or browser automation code
- `packages/infra` can depend on external libraries, but should translate them into core-facing contracts
- `apps/*` can shape runtime-specific behavior, but should avoid becoming the permanent home for reusable business rules

## Runtime Surfaces

### Bot

Owns:

- Discord command handling
- queue/session orchestration inside guilds
- voice join/leave/playback triggers
- sharing listener/session links
- privileged playlist export flows

### API

Owns:

- Discord OAuth and cookie-backed guild access
- public session snapshots
- session-scoped and track-scoped media delivery
- playlist archive downloads through signed access tokens

### Web

Owns:

- public listener entrypoint routing
- serving the web shell
- proxying `/api/*` to the internal FastAPI service

### Worker

Owns:

- transcode job polling
- media backfill
- GIF generation and upload
- updating artifact metadata on canonical tracks

## Core Concepts

These are the main persisted or long-lived concepts in the current codebase:

- `Track`: canonical metadata keyed by `suno_url`
- `Submission`: a guild/channel/message-specific track submission
- `QueueItem`: a guild-local queue entry
- `WebSession`: persisted public listening session state
- `OpusJob`: background work record for derived audio artifacts
- `SessionState`: in-memory guild playback state inside the bot

Notable nuance:

- `Track`, `QueueItem`, and `WebSession` are persisted application concepts
- `SessionState` is a runtime bot structure, not a shared persisted domain model

## Ownership By Layer

### Core

Core should own:

- business-facing vocabulary
- application use cases
- repository and external-service ports
- behavior that should remain stable regardless of transport

Examples:

- ingesting a Suno link
- queue preview logic
- canonical repository contracts

### Infra

Infra should own:

- SQLAlchemy models and queries
- S3/MinIO interactions
- Suno HTTP and browser clients
- mapping storage models to core-facing records

### Apps

Apps should own:

- Discord commands, embeds, and event handlers
- FastAPI request handling and response shaping
- worker loops and process-level orchestration
- static web shell behavior

## Session Model

There are two session concepts that matter:

### Guild Runtime Session

Implemented by bot-side `SessionState`.

Owns:

- submission openness
- cooldowns and per-user limits
- autoplay/DJ mode counters
- in-memory queue and now-playing state

### Public Web Session

Implemented by persisted `WebSession`.

Owns:

- stable public `session_id`
- active/inactive lifecycle
- current track linkage
- guild/channel association for listener access

Future work should keep these concepts connected, but not conflate them.

## Voice/Playback Ownership

Runtime mode is **Lavalink-only** (`VOICE_BACKEND=lavalink`).

- Bot manages: queue state, autoplay/DJ policy, command handling, and now-playing announcements.
- Lavalink manages: per-guild audio player execution and stream playback.
- discord.py voice gateway still manages session signaling/token/endpoint state used to update Lavalink voice state.

## Queue Model

Queues are maintained in bot session state per guild. Lavalink does not hold the source-of-truth queue for this project.

- One `SessionState` per guild.
- One `GuildAudioController` per guild.
- One Lavalink player per guild.

This gives isolated multi-guild playback with independent queues.

## Media Ownership

Canonical metadata and derived artifacts are intentionally separated in responsibility:

- Postgres holds canonical track, queue, submission, session, and job state
- local cache or object storage holds generated media artifacts
- worker owns expensive transformation work
- API owns artifact delivery and fallback behavior

This keeps the storage and delivery concerns explicit instead of scattering media logic across bot and API paths.

## Typical Flows

### Discord Ingestion Flow

1. Discord message or command provides a Suno URL.
2. Bot calls a core use case.
3. Core requests metadata through the `SunoClient` port.
4. Infra repositories upsert `Track` and `Submission`.
5. Queue entries are added per guild when appropriate.

### Public Listener Flow

1. Host activates a web session for a guild/channel.
2. A persisted `WebSession` exposes a stable `session_id`.
3. Listener opens `/listen/{session_id}`.
4. Web shell polls the API snapshot.
5. API resolves the current track and session-scoped audio.

### Media Artifact Flow

1. API or worker identifies missing or stale derived audio.
2. `OpusJob` is enqueued or reused.
3. Worker generates Opus and browser audio artifacts.
4. Metadata is written back to the canonical `Track`.
5. API redirects or serves the artifact on later requests.

## Architecture Guardrails

When adding code, prefer these rules:

- add new business rules in `packages/core` before adding them to route handlers or cogs
- keep framework types out of core
- keep SQLAlchemy models inside infra
- keep response-shaping and embed-building in app layers
- keep naming aligned with the existing `Track` / `Submission` / `QueueItem` / `WebSession` vocabulary

## What To Update When You Change The System

- Update this file when ownership between layers changes.
- Update `ddd.md` when the conceptual rules or bounded contexts change.
- Update feature docs when a user-visible flow changes.
