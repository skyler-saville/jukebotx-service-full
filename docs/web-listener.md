# Web Listener Flow

## Purpose

The public web experience is session-first, not track-first.

The user-facing idea is:

- a Discord host activates a listening session
- that activation creates or refreshes a persisted `session_id`
- listeners open `/listen/{session_id}`
- the web shell polls the API for a public session snapshot and current session audio

This keeps the listener experience tied to a live party instead of exposing arbitrary raw track playback as the primary public model.

## Runtime Surfaces Involved

- Bot: activates and updates persisted session state
- API: exposes public session snapshot and session-scoped audio
- Web shell: serves the listener page and proxies `/api/*`
- Worker: prepares derived audio artifacts used by listener playback
- Postgres: holds canonical session and track state
- Object storage or local cache: holds generated audio artifacts

## Key Routes

Web:

- `/listen/{session_id}`: public listener page route handled by the web shell

API:

- `GET /sessions/{session_id}`: public session snapshot
- `GET /sessions/{session_id}/audio`: current session-scoped browser audio
- `POST /guilds/{guild_id}/channels/{channel_id}/web-session`: authenticated activation or refresh

## Session Model

The important persisted concept is `WebSession`.

Fields that matter operationally:

- `session_id`: public stable identifier shared with listeners
- `guild_id` and `channel_id`: Discord ownership context
- `current_track_id`: current canonical track, if any
- `is_active`: whether the session should currently be considered live
- `activated_at` and `ended_at`: lifecycle markers

The response model for listeners also exposes:

- session `status`: `live`, `waiting`, or `offline`
- current track metadata and lyrics
- queue preview
- `current_audio_url` for the active session track

## Activation Flow

High-level sequence:

1. A host/mod uses a Discord-driven control path.
2. Bot code activates or refreshes a `WebSession` for the guild/channel pair.
3. The API returns a session snapshot that includes `session_id`.
4. The bot can share a listener URL based on that persisted `session_id`.
5. The web shell loads `/listen/{session_id}` and polls the API snapshot.

Important behavior:

- sessions are unlisted by default
- session activation is privileged
- listener access should remain low-friction, especially for mobile users coming from Discord

## Playback Behavior

Public listener playback uses session-scoped audio:

- listeners do not need guild-auth cookies for `GET /sessions/{session_id}` or `GET /sessions/{session_id}/audio`
- the API looks up the active `WebSession`
- if a browser-ready artifact exists, the API serves or redirects to it
- if not, the API enqueues work and falls back to source audio while the derived artifact is prepared

This is intentionally different from the authenticated track-level audio routes.

## Why Session-Scoped Audio Matters

Using `/sessions/{session_id}/audio` instead of only `/tracks/{track_id}/web-audio` preserves the product model:

- the public experience is about a live listening party
- the session decides what is currently playable
- anonymous playback is constrained to the active party context

That keeps public listening aligned with the host's state instead of turning the product into a generic public track CDN.

## Auth Boundaries

Authenticated flows:

- queue preview by guild
- guild-scoped track/session management
- Discord OAuth profile access

Anonymous flows:

- loading the public session page
- fetching the current public session snapshot
- listening to the current session audio

This split is important and should stay explicit in future changes.

## Expected Future Extensions

Likely additions without changing the core model:

- playback timing fields on session snapshots
- richer queue or now-playing metadata
- explicit visibility controls beyond unlisted
- live push updates using WebSocket or SSE instead of polling

Those should extend the `WebSession`-centric model, not replace it with a track-first one.
