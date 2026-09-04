# Media Pipeline

## Purpose

JukeBotx stores canonical track metadata once, then derives playback artifacts for different consumers.

Today the important derived media outputs are:

- Opus audio for playback-oriented consumers
- browser-oriented Ogg/Opus audio for web listeners
- optional GIF artwork generated from video sources

## Canonical Record

The canonical source of truth is the `Track` record.

It stores:

- source metadata from Suno such as title, artist, lyrics, artwork, video, and MP3 URL
- derived artifact metadata such as Opus and web-audio status, storage paths, and public URLs

The worker does not create a separate media domain model. It enriches the canonical `Track`.

## Pipeline Overview

1. A request for Opus or web-audio arrives, or a background pass discovers missing media.
2. The API or worker enqueues or fetches an `OpusJob`.
3. Worker downloads or streams from the canonical MP3 URL.
4. Worker generates derived artifact files.
5. Artifacts are stored either locally or in object storage.
6. Worker writes resulting metadata back to the `Track`.

This lets the API remain mostly stateless for delivery while the worker owns the heavy processing.

## Responsibilities By Layer

API:

- decide whether a derived artifact is ready
- redirect or serve the artifact when present
- enqueue work when artifacts are missing or stale

Worker:

- perform expensive media processing
- upload artifacts when storage is enabled
- mark success or failure on both job and track metadata

Infra storage/repositories:

- provide object-key generation, freshness checks, upload helpers, and repository updates

## Storage Modes

### Local Cache Mode

When object storage is disabled:

- artifacts are written to local cache paths
- API can serve files directly
- freshness is based on cache existence and TTL

### Object Storage Mode

When `OPUS_STORAGE_PROVIDER=s3`:

- artifacts are uploaded to MinIO or S3
- API generally redirects to storage-backed URLs
- freshness is based on object existence and TTL rules

The API does not need to know how uploads happen. It only needs to know whether a usable artifact is available.

## Failure Semantics

The media pipeline is intentionally resilient:

- Opus failures mark the job and track as failed
- web-audio failures should not necessarily break the entire Opus pipeline
- worker loops should keep running even when individual jobs fail

This is why the worker logs failures and writes status metadata instead of crashing the loop on first error.

## Playlist Archive Delivery

Playlist export is part of the broader media-delivery story:

- the bot can build downloadable playlist archives
- if Discord delivery is too large, archives are uploaded to object storage
- API download tokens provide time-bounded access to stored archives

That flow should continue to use signed or scoped access patterns rather than making archives globally discoverable.

## GIF Backfill

Optional worker passes can enrich media metadata further:

- fetch missing media assets from browser automation
- generate GIF artwork from video URLs
- write updated media URLs back to the canonical track record

This should remain a background enrichment concern, not something required for the core queue or listener flow.

## Operational Signals

When debugging this pipeline, the most useful questions are:

- does the track have a source `mp3_url`?
- is there already a fresh artifact in cache or storage?
- is the `OpusJob` pending, completed, or failed?
- did the worker update both artifact metadata and job status?
- is storage configured consistently across API and worker?

Those questions usually identify whether the problem is ingestion, job orchestration, storage, or delivery.
