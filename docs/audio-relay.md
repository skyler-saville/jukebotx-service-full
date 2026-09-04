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
stream URLs are unguessable capability URLs because Lavalink cannot attach the
relay's control bearer token to its media request. A stream may be reopened from
its in-memory beginning because Lavalink probes the URL before playback; the bot
still explicitly releases it when the track ends.

## Browser capture engine

The relay can launch Chromium against a Suno song or share URL, route Chromium's
decoded audio into a private PulseAudio null sink, and encode that sink directly to
an Ogg/Opus HTTP response. Chromium runs against a private Xvfb display so the
container remains unattended without using Chromium's headless rendering mode.
It never asks the bot for Suno cookies, never
returns browser state to Lavalink, and does not create a song file or cache entry.

The browser uses a persistent profile at `/data/chromium-profile`; Compose stores
that directory in the `relay_browser_data` volume. Directly accessible song pages
do not require a login. If Suno requires authentication for a future URL, the relay
will need an operator-controlled profile bootstrap workflow before that URL can be
played.

Enable the browser engine and start the service:

```dotenv
RELAY_ENABLE_BROWSER_INPUTS=true
AUDIO_RELAY_BASE_URL=http://relay:8090
```

```bash
docker compose --profile relay up -d --build relay bot
make smoke-browser-relay \
  URL=https://suno.com/song/b64a51c8-e618-4b21-a057-7867e6a98e13
```

Only `https://suno.com/song/...` and `https://suno.com/s/...` inputs are accepted.
Chromium profile access is serialized because Chromium locks its user-data
directory. This matches the current single listening-party playback model.

## Synthetic plumbing test

The synthetic source is disabled by default and exists only to prove network and
streaming behavior without contacting a website.

Run that plumbing smoke with:

```bash
RELAY_ENABLE_SYNTHETIC_INPUTS=true docker compose --profile relay up -d --build relay
make smoke-relay
```

## Relay engine boundary

The bot integration deliberately does not depend on Chromium, PulseAudio, or Suno
page structure. Those details remain inside the relay engine so future site or
capture changes do not leak into the listening-party domain or Discord process.
