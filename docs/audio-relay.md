# Browser Audio Relay

JukeBotx can optionally fall back to a live audio relay when a queued track has a
source page URL but no direct playable audio URL. The relay is an independent
sidecar; Lavalink remains the only Discord voice playback backend.

## Playback flow

1. The bot uses the existing direct-audio/Opus path when one is available.
2. Otherwise, it asks the relay to prepare the source page for a guild consumer.
3. The relay returns a live HTTP stream URL reachable from the Lavalink container.
4. The bot gives that stream URL to Lavalink.
5. On finish, skip, stop, or failed playback startup, the bot releases the relay
   stream so browser and encoder processes can be reclaimed.

The relay owns browser sessions, login state, audio-device routing, encoder
processes, and stream delivery. Authentication material must remain inside the
relay; it is never returned to the bot or Lavalink.

## HTTP contract

Start a stream:

```http
POST /v1/streams
Authorization: Bearer <optional AUDIO_RELAY_TOKEN>
Content-Type: application/json

{
  "source_url": "https://example.test/song/123",
  "consumer_id": "discord-guild:123456789"
}
```

Successful response:

```json
{
  "id": "relay-session-id",
  "stream_url": "http://relay:8090/v1/streams/relay-session-id/audio"
}
```

Release a stream:

```http
DELETE /v1/streams/relay-session-id
```

`404` and `410` releases are treated as already complete. A start response must
contain a non-empty `id` and an absolute `http` or `https` `stream_url`.

## Configuration

The feature is opt-in:

```dotenv
AUDIO_RELAY_BASE_URL=http://relay:8090
AUDIO_RELAY_TOKEN=
AUDIO_RELAY_TIMEOUT_SECONDS=30
```

Do not set `AUDIO_RELAY_BASE_URL` until a compatible sidecar is running. The
returned stream URL is consumed by Lavalink, so it must use a hostname and port
reachable on Lavalink's Docker network.

The Compose smoke port binds to `127.0.0.1` by default. Set a strong
`AUDIO_RELAY_TOKEN` before exposing the control API beyond the Pi itself. Live
stream URLs are unguessable, one-shot capability URLs because Lavalink cannot
attach the relay's control bearer token to its media request.

## Current implementation status

The repository includes an opt-in `relay` Compose service with the lifecycle API,
one active stream per consumer, one-shot capability URLs, cancellation, and a
synthetic FFmpeg/Ogg source. The synthetic source is disabled by default and exists
only to prove network and streaming behavior before a browser engine is connected.

Run that plumbing smoke with:

```bash
RELAY_ENABLE_SYNTHETIC_INPUTS=true docker compose --profile relay up -d --build relay
make smoke-relay
```

The sidecar does not yet contain a browser capture engine, so normal web source
URLs return `422` until one is registered. Do not enable `AUDIO_RELAY_BASE_URL` on
the bot in production before that engine and its authenticated browser profile are
ready.

## Relay engine boundary

A compatible implementation may launch a browser against a controlled source,
route that browser's audio into an isolated virtual device, encode the PCM audio
with FFmpeg, and expose the encoded output as the live stream. The initial bot
integration deliberately does not depend on a particular browser, audio server,
site, or capture mechanism. That keeps browser-session churn and platform-specific
breakage outside the listening-party domain and outside the Discord bot process.
