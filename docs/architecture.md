# Architecture

## Overview

JukeBotx is a monorepo with three primary runtime surfaces:

- Bot (`apps/bot`): Discord commands, session orchestration, playback control.
- API (`apps/api`): OAuth/session and queue/track HTTP endpoints.
- Worker (`apps/worker`): background media jobs.

Shared layers:

- Core (`packages/core`): use cases + ports. No Discord, HTTP clients, or DB dependencies.
- Infra (`packages/infra`): adapters for DB, Suno HTTP scraping, and storage.

## Dependency Direction

The dependency rule is strict:

- `core` does not depend on `infra` or app layers.
- app layers depend on `core` and wire infra adapters.
- infra implements core ports.

This keeps business logic testable and infrastructure-swappable.

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
