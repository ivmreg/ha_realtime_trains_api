# Realtime Trains API — Home Assistant Integration

## What this is

A HACS-distributed Home Assistant custom integration
(`custom_components/realtime_trains_api/`) that polls the
[Realtime Trains Pull API](https://api.rtt.io) for live UK National Rail
departures. Each configured origin/destination "query" becomes a sensor
whose state is minutes-until-next-departure and whose `next_trains`
attribute is the full departures array consumed by the companion
[`ha-train-departure-board`](https://github.com/ivmreg/ha-train-departure-board)
Lovelace card. That attribute shape is a public contract — additive changes
only.

## Commands

- `pytest custom_components/realtime_trains_api/test` — main (stub-based)
  suite. `test/conftest.py` stubs the entire `homeassistant` (and `aiohttp`)
  module tree, so tests run without Home Assistant installed. Test deps in
  `requirements-test.txt`.
- `pytest tests_ha` — real-HA smoke test via
  `pytest-homeassistant-custom-component` (`requirements-test-ha.txt`);
  needs Python ≤3.13 and runs as its own CI job. Kept outside the stub
  conftest's directory on purpose.
- No build step; HACS ships the directory as-is.

## Architecture

- `coordinator.py` — `RealtimeTrainsUpdateCoordinator` does all polling:
  peak/off-peak adaptive interval (`_is_peak`/`_set_polling_interval`),
  per-query fetch/filter/assembly (`_fetch_one_query`, `_build_train`),
  journey-detail enrichment with bounded concurrency
  (`_enrich_journey_data`, semaphore of 2 — mind the RTT rate budget),
  no-departure backoff (`auto_adjust_scans`), and stale-data serving
  (`_serve_stale_data` returns last-known data with `data_stale` set instead
  of going unavailable on rate-limit/API errors).
- `rtt_api.py` — thin async client: bearer auth + refresh-token flow,
  `X-RateLimit-*` parsing, `Retry-After` pre-emptive skip, typed exceptions.
- `sensor.py` — per-query departure sensor + one rate-limit sensor; supports
  both config entries and legacy YAML. `build_query_key` (in
  `sensor_helpers.py`) links sensors to coordinator data — keep it in sync
  with nothing; it is the single source of truth for both sides.
- `config_flow.py` — UI setup wizard + options flow. Field labels live in
  `strings.json` and `translations/en.json` (keep both identical).
- `sensor_helpers.py` / `normalization.py` — pure parsing/coercion helpers;
  prefer extending these (they're the easiest code to test).
- `diagnostics.py` — config-entry diagnostics with redacted tokens.
- `blueprint.yaml` — standalone automation blueprint tracking one scheduled
  train via user-configured `rest_command`s (bypasses this integration's
  client; a known wart).

## Key option semantics

- `journey_data_for_next_X_trains` — how many of the listed trains get
  journey enrichment (stops, arrival, duration). It does NOT control list
  length…
- `max_trains` — …that's this one. Defaults to `journey_data_for_next_X_trains`
  when that is set (historical behavior), else 10.
- `auto_adjust_scans` — back off polling to 30 min while a station has no
  departures at all.

## Conventions & constraints

- Backward compatibility of YAML config keys and the `next_trains` schema is
  a hard constraint; new fields/options only, with defaults preserving
  current behavior.
- Async-only HA patterns; spaces for indentation; `from __future__ import
  annotations` typing style.
- Be polite to the RTT API: any concurrency/request-volume change must stay
  within what the rate-limit handling tolerates.
- `docs/superpowers/` holds prior plan/spec docs (API modernization); read
  before reworking code they cover.
