# Voice: Lavalink Runtime

## Mode

The bot runs in Lavalink-only mode:

- `VOICE_BACKEND=lavalink`
- Any other value should be treated as invalid startup configuration.

## Playback Flow

1. Bot command/session logic chooses the next track.
2. Bot resolves playback URL (prefers direct source audio URL when backend requests it).
3. `LavalinkPlaybackBackend` ensures a guild player exists.
4. Bot sends Discord voice state (`sessionId`, `endpoint`, `token`, `channelId`) to Lavalink player.
5. Lavalink loads and plays the resolved track.
6. Track-end events flow back through backend hooks to session orchestration.

## Multi-Guild Behavior

- One Lavalink player per guild.
- Multiple guilds can play concurrently, bounded by Lavalink node capacity.
- Queue policy remains in bot session state, not in Lavalink.

## Failure Modes

Common transient issue:

- Discord voice websocket close/reconnect churn (for example close code `4006`).

Current mitigations in backend:

- Player voice-connect verification before play.
- Serialized voice-connect attempts using a lock.
- Short retry loop with backoff before hard failure.

## Practical Guidance

- Keep Lavalink healthy and reachable from bot container/service.
- Prefer direct audio URLs for smoke testing (`make smoke-lavalink URL=...mp3`).
- Suno page URLs are ingest inputs, not direct Lavalink track identifiers.
