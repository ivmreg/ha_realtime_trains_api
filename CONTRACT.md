# The `next_trains` contract

This is the canonical description of the data contract between
`ha_realtime_trains_api` (producer) and any consumer — primarily the
[`ha-train-departure-board`](https://github.com/ivmreg/ha-train-departure-board)
Lovelace card, whose `src/types.ts` (`TrainDeparture`) mirrors this document
and whose `tests/contract.test.ts` validates the shared `sample_entity.json`
against it.

**Rule: changes are additive only.** New fields may be added with safe
defaults; existing fields never change meaning, type, or format.

All datetimes are strings formatted `DD-MM-YYYY HH:MM` (Europe/London local
time) unless noted as ISO 8601.

## Sensor entity attributes

| Attribute | Type | Notes |
|---|---|---|
| `journey_start` | string | Origin CRS code |
| `journey_end` | string | Destination CRS code; absent for station-wide queries |
| `next_trains` | list | See per-train fields below; always present, empty list when no departures exist |
| `platforms_of_interest` | list[string] | Only when a platform filter is set |
| `pinned_train` | object | The matching pinned train (same shape as a `next_trains` entry); only when `pinned_departure_time` is configured and matched |
| `current_polling_interval` | int | Seconds |
| `next_update_at` | ISO datetime | When the next refresh is due |
| `data_stale` | bool | True while serving last-known data because the RTT API is down or rate-limited |
| `last_successful_update` | ISO datetime | Only after at least one successful refresh |
| `error` | string or null | `"Rate Limited"` / `"Credentials invalid"` when journey enrichment failed; the state stays numeric regardless |

The sensor **state** is minutes until the next matching departure (numeric),
or unknown when there are none.

## Per-train fields (always present)

| Field | Type | Notes |
|---|---|---|
| `origin_name`, `destination_name` | string | Human-readable station names |
| `service_uid` | string | RTT service identity, e.g. `P63128` |
| `headcode` | string | Train reporting identity, e.g. `2A69` |
| `type` | string | RTT mode type, e.g. `TRAIN` |
| `operator_name` | string | Operating company; also scopes the card's stock styling |
| `scheduled`, `estimated` | datetime string | Departure times (`DD-MM-YYYY HH:MM`) |
| `scheduled_iso`, `estimated_iso` | ISO datetime string | Departure times formatted ISO-8601 with explicit Europe/London UTC offset (e.g. `2025-11-15T21:51:00+00:00`) |
| `minutes` | number | Minutes until estimated departure at fetch time |
| `lateness` | number or null | RTT advertised lateness |
| `is_cancelled` | bool | Cancellation flag |
| `platform` | string or null | Actual, falling back to planned |
| `length` | number or null | Number of vehicles |
| `stock` | string or null | Rolling-stock branding, e.g. `City Beam` |

## Per-train fields (journey enrichment; first `journey_data_for_next_X_trains` trains only)

| Field | Type | Notes |
|---|---|---|
| `scheduled_arrival`, `estimate_arrival` | datetime string | At the query's destination (`DD-MM-YYYY HH:MM`) |
| `scheduled_arrival_iso`, `estimate_arrival_iso` | ISO datetime string | Destination arrival times formatted ISO-8601 with explicit Europe/London UTC offset |
| `journey_time_mins` | number | Estimated arrival minus estimated departure |
| `stops` | number | Location count for the service |
| `status` | string | `OK` \| `Delayed` \| `Cancelled` — arrival status at the destination |
| `reason` | string | Disruption reason short text, when RTT provides one |
| `subsequent_stops` | list | `{stop, name, scheduled, estimated, scheduled_iso, estimated_iso}` — upcoming calling points |
| `last_report_station` | string | CRS of the last actual report |
| `last_report_type` | string | `Arrival` \| `Departure` \| `Pass` |
| `last_report_time` | datetime string | Time of that report (`DD-MM-YYYY HH:MM`) |
| `last_report_time_iso` | ISO datetime string | Time of that report formatted ISO-8601 with explicit Europe/London UTC offset |

## Per-train fields (pinning)

| Field | Type | Notes |
|---|---|---|
| `is_pinned` | bool | Present (true) only on the query's pinned train |

## Status vocabulary

Two related but distinct notions of "status" exist; consumers should not mix
them up:

- **Producer arrival status** (`status`, journey enrichment only): whether
  the train reaches the *destination* on time — `OK` / `Delayed` /
  `Cancelled`.
- **Consumer display status** (derived by the card from `scheduled` vs
  `estimated` and `is_cancelled`): on time / delayed / early / cancelled at
  the *origin*, with deviations of ≤1 minute treated as on time.

A train can legitimately be "on time" at the origin and `Delayed` at the
destination.
