# Operational Logging Policy

This policy defines what JukeBotx logs at runtime and how we avoid noisy logs while preserving incident visibility.

## 1) Always log (every occurrence)

Emit structured logs for all of the following:

- Exceptions and unhandled command/runtime errors.
- Failed external calls (Suno scrape/playlist fetch, HTTP failures, upstream errors).
- Permission failures (Discord check failures, forbidden operations).
- Failed voice channel join/leave operations.
- Playback errors and playback startup failures.

## 2) Sample / aggregate normal success paths

For high-volume normal behavior, emit event-level debug/canonical logs and rely on periodic aggregate summaries:

- Successful URL ingests.
- Normal command usage.

This keeps observability while reducing alert fatigue.

## 3) Periodic per-guild summaries (5–10 minutes)

Emit a summary log every 5–10 minutes per guild with reset windows for:

- ingest attempted / succeeded / failed counts,
- queue growth for the window,
- auto-leave reasons (reason -> count),
- command usage count.

## 4) DM-only critical alerts

Send direct-message alerts to the master/mod owner for critical sustained failures only:

- sustained ingestion failure spikes,
- repeated 403/429 style upstream denial/ratelimit failures,
- repeated playback startup failures.

These alerts should be deduplicated and rate-limited.

## 5) In-memory per-guild counters

Maintain per-guild metric-like counters in memory and reset them at each summary window boundary.

Current implementation location:

- `SessionState` window counters for ingest, command usage, queue baseline, and auto-leave reasons.
- `JukeBot._emit_periodic_summaries()` for interval-based summary emission + window reset.
