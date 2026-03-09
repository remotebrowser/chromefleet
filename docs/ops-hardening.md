# Chrome Fleet Ops Hardening

This document summarizes production hardening knobs added to reduce CPU/disk pressure and long-lived container accumulation.

## New environment variables

- `MAX_ACTIVE_BROWSERS` (default: `30`)
  - Caps concurrently running `chromium-*` containers.
  - `POST /api/v1/browsers/{id}` returns `429` when cap is reached.

- `BROWSER_IDLE_TTL_SECONDS` (default: `1800`)
  - TTL used by background cleanup for stale browsers.

- `BROWSER_CLEANUP_INTERVAL_SECONDS` (default: `60`)
  - Frequency of background cleanup loop.

- `USE_ACTIVITY_DB_FOR_IDLE_CLEANUP` (default: `false`)
  - `false`: cleanup uses container launch time label (`chromefleet.launched_at`) only.
  - `true`: attempts to use Chrome History DB activity as the primary idle signal.

- `ENABLE_VERBOSE_APP_LOGS` (default: `false`)
  - Enables noisy app logs such as full container enumeration logs.

- `ENABLE_VERBOSE_CDP_LOGS` (default: `false`)
  - Enables CDP per-message logs.

- `CDP_LOG_SAMPLE_EVERY` (default: `100`)
  - When verbose CDP logs are enabled, prints only every Nth message.

- `ENABLE_CDP_RETRY_LOGS` (default: `false`)
  - Enables retry/error debug logs for CDP connection resolution.

- `UVICORN_RELOAD` (default: `false`)
  - Must stay `false` in production.

## Behavioral changes

1. Duplicate browser creation now returns `409`.
2. Active browser cap enforced with `429`.
3. Background cleanup loop starts on app startup and kills stale `chromium-*` containers.
4. New browser containers are labeled with launch timestamp for cheap TTL cleanup.
5. CDP log volume is greatly reduced by default.
6. Uvicorn reload is controlled by env var and defaults off.

## Suggested Dokku config baseline

```bash
dokku config:set chromefleet \
  MAX_ACTIVE_BROWSERS=20 \
  BROWSER_IDLE_TTL_SECONDS=900 \
  BROWSER_CLEANUP_INTERVAL_SECONDS=60 \
  USE_ACTIVITY_DB_FOR_IDLE_CLEANUP=false \
  ENABLE_VERBOSE_APP_LOGS=false \
  ENABLE_VERBOSE_CDP_LOGS=false \
  ENABLE_CDP_RETRY_LOGS=false \
  UVICORN_RELOAD=false
```

For incident debugging, temporarily enable `ENABLE_CDP_RETRY_LOGS=true` and/or `ENABLE_VERBOSE_CDP_LOGS=true` for a short period only.
