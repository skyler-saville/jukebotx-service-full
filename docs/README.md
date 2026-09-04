# Documentation

This directory contains the working architecture, runtime, and feature docs for JukeBotx.

## Start Here

If you are new to the repo, read these in order:

1. `architecture.md`
2. `ddd.md`
3. the feature or runtime guide that matches the area you are touching

## Foundation Docs

- `architecture.md`: system boundaries, runtime surfaces, dependency direction, and where code belongs.
- `ddd.md`: the DDD and clean-architecture rules we want to preserve as the codebase grows.

## Feature Docs

- `web-listener.md`: public session-first web listener flow, API shape, and ownership of session state.
- `media-pipeline.md`: Opus/web-audio artifacts, worker responsibilities, and storage/cache behavior.

## Runtime Docs

- `voice-lavalink.md`: Lavalink-only playback model, guild/player mapping, and known failure modes.
- `operations.md`: local runbook, smoke checks, and log-based troubleshooting.

## How To Use These Docs

- When adding a new feature, start in `ddd.md` and `architecture.md` before writing code.
- When changing behavior, update the feature doc for that flow in the same branch.
- When the code and docs disagree, fix the docs or code immediately instead of letting the mismatch linger.
