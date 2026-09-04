# DDD Guide

## Why This Repo Uses DDD-ish Boundaries

JukeBotx is not trying to be "enterprise DDD theater". The goal is practical:

- keep Discord, FastAPI, SQLAlchemy, and storage concerns from leaking into business rules
- keep important concepts named consistently across bot, API, worker, and web surfaces
- make new features easier to place without turning the repo into a transport-driven tangle

This repo mixes clean architecture and lightweight DDD:

- `packages/core` holds the business-facing contracts and application use cases
- `packages/infra` implements persistence, scraping, storage, and other adapters
- `apps/*` translate runtime-specific inputs into calls on core and infra

## Ubiquitous Language

These names should mean the same thing everywhere in the codebase:

- `Track`: the canonical stored representation of a Suno track, keyed by `suno_url`
- `Submission`: a track posted in a specific guild/channel/message context
- `QueueItem`: a guild-scoped queue entry; playback status is per guild, not global
- `WebSession`: a persisted public listening session keyed by app-level `session_id`
- `OpusJob`: a background job for derived audio artifacts
- `SessionState`: the in-memory bot-side runtime state for a guild listening session

Important distinction:

- `WebSession` is persisted public listener state
- `SessionState` is in-memory bot orchestration state

They are related, but they are not the same object and should not be collapsed together casually.

## Current Bounded Contexts

The codebase is still compact, so the contexts are intentionally lightweight.

### Track Ingestion Context

Owns:

- resolving Suno metadata
- upserting canonical `Track` records
- recording `Submission` records
- optionally creating queue entries

Primary code:

- `packages/core/jukebotx_core/use_cases/ingest_suno_links.py`
- `packages/core/jukebotx_core/ports/suno_client.py`
- `packages/infra/jukebotx_infra/suno/`
- `packages/infra/jukebotx_infra/repos/track_repo.py`
- `packages/infra/jukebotx_infra/repos/submission_repo.py`

### Playback and Session Context

Owns:

- guild-local queue policy
- autoplay and DJ mode
- now-playing transitions
- Discord voice orchestration
- persisted public web session activation and current-track linkage

Primary code:

- `apps/bot/jukebotx_bot/discord/session.py`
- `apps/bot/jukebotx_bot/discord/audio.py`
- `apps/bot/jukebotx_bot/voice/service.py`
- `packages/infra/jukebotx_infra/repos/queue_repo.py`
- `packages/infra/jukebotx_infra/repos/web_session_repo.py`

### Public Listener Context

Owns:

- session-first web routing
- public session snapshots
- anonymous listener playback for an active session
- authenticated guild/user controls through FastAPI

Primary code:

- `apps/api/jukebotx_api/main.py`
- `apps/web/server.py`
- `apps/web/static/index.html`

### Media Artifact Context

Owns:

- Opus and browser-oriented Ogg generation
- cache and object-storage publication
- worker retry/failure handling
- metadata writes back to canonical `Track` records

Primary code:

- `apps/worker/jukebotx_worker/main.py`
- `apps/worker/jukebotx_worker/transcode.py`
- `packages/infra/jukebotx_infra/storage/opus_storage.py`
- `packages/infra/jukebotx_infra/repos/opus_job_repo.py`

## Where Logic Belongs

### Put Logic In Core When

- the rule describes business behavior instead of framework wiring
- the rule should stay true whether the caller is Discord, HTTP, worker, or tests
- the rule needs to be unit-testable without SQLAlchemy, Discord, or FastAPI

Examples:

- duplicate-within-guild behavior for track submissions
- queue preview semantics
- rules about when queue items are created or skipped

### Put Logic In Apps When

- the code translates framework events, requests, commands, or responses
- the code shapes output for Discord embeds, FastAPI schemas, or frontend payloads
- the behavior is transport-specific even if it touches business data

Examples:

- slash-command parsing
- cookie and OAuth flow handling
- HTTP response selection such as redirect vs file response

### Put Logic In Infra When

- the code talks to databases, object storage, external HTTP services, or browser automation
- the code maps between storage models and core-facing records
- the code is an implementation of a port defined by core

Examples:

- SQLAlchemy repositories
- Suno HTTP/browser clients
- S3/MinIO upload and signed URL logic

## Guardrails For New Code

### Do

- pass transport-neutral data into core use cases
- keep SQLAlchemy models inside infra
- keep FastAPI schemas inside the API app
- translate between infra models and core records at repository boundaries
- keep Discord-specific objects and embed formatting inside the bot app

### Do Not

- import Discord classes into `packages/core`
- import FastAPI request/response types into `packages/core`
- return SQLAlchemy models from core-facing repository methods
- hide business rules inside route handlers, cogs, or worker loops if the same rule should apply elsewhere
- treat "it only happens in one place today" as a reason to skip proper boundaries

## Reality Check: Where The Codebase Is Today

This repo is still in a transitional stage.

- `packages/core/jukebotx_core/domain/` is currently thin
- many important domain concepts are represented as immutable records in `ports/repositories.py`
- several app modules still carry orchestration that may eventually deserve promotion into richer core services

That is acceptable for now. The goal is not to force artificial abstractions. The goal is to keep the direction correct so the next feature does not increase coupling.

## Decision Checklist For Future Changes

Before adding a new class or function, ask:

1. Is this rule about the business, or about the framework/runtime?
2. Would we want the same rule if the caller changed from Discord to HTTP or worker?
3. Does this need a new core port, or just a new infra implementation?
4. Are we naming the concept using existing ubiquitous language?
5. Are we leaking storage or transport details across a boundary?

If the answer to question 2 is yes, the code probably belongs closer to `packages/core`.

## Documentation Expectations

When a change introduces a new concept or expands an existing context:

- update `architecture.md` if ownership or boundaries changed
- update the relevant feature guide in `docs/`
- update this file if the DDD rules or bounded-context map changed
