# Operations Runbook

## Local Startup

Recommended local path:

```bash
docker compose up -d --build
```

## Quick Health Checks

- Service status:

```bash
docker compose ps
```

- Lavalink smoke (direct audio URL):

```bash
make smoke-lavalink URL="https://cdn1.suno.ai/<track>.mp3"
```

- Suno metadata smoke:

```bash
make smoke-suno URL="https://suno.com/song/<id>"
```

- Worker GIF pipeline smoke (Suno or direct video URL):

```bash
make smoke-worker-gif URL="https://suno.com/song/<id>"
# Optional upload validation:
make smoke-worker-gif URL="https://suno.com/song/<id>" UPLOAD=1
```

## Worker Media Backfill (Optional)

The worker can run an intensive browser scrape pass for tracks missing artwork/video.

Enable in `.env`:

```bash
MEDIA_BACKFILL_ENABLED=true
MEDIA_BACKFILL_POLL_SECONDS=30
MEDIA_BACKFILL_MIN_TRACK_AGE_SECONDS=600
```

Install browser dependency in the runtime image/env:

```bash
poetry add playwright
poetry run playwright install chromium
```

## Worker GIF Conversion (Phase 2 Optional)

When a track has `video_url` but no GIF artwork, worker can generate and upload GIFs.

Enable in `.env`:

```bash
MEDIA_GIF_ENABLED=true
MEDIA_GIF_POLL_SECONDS=45
MEDIA_GIF_MIN_TRACK_AGE_SECONDS=600
MEDIA_GIF_FFMPEG_PATH=ffmpeg
MEDIA_GIF_FPS=10
MEDIA_GIF_WIDTH=512
MEDIA_GIF_STORAGE_PREFIX=media/gif
```

Notes:

- Requires object storage to be configured (`OPUS_STORAGE_PROVIDER=s3` + bucket creds).
- Uses ffmpeg in the worker runtime; verify binary exists in container/image.
- GIF URL is written into `track.image_url` while preserving `track.video_url`.

## Discord Canary Checklist

1. `;join`
2. queue one track
3. `;p`
4. `;n`
5. `;s`
6. `;leave`

## Troubleshooting Logs

Capture recent logs:

```bash
docker compose logs --tail=300 bot
docker compose logs --tail=300 lavalink
```

Key signals:

- Bot `discord.voice_state` reconnect loops
- Lavalink websocket/session disconnects
- Player voice-state update failures prior to play

## Known Gotchas

- Lavalink smoke requires a direct audio URL, not a Suno page URL.
- `VOICE_BACKEND` must remain `lavalink` for this codebase.
- If code changed in bot app, rebuild bot service:

```bash
docker compose up -d --build bot
```
