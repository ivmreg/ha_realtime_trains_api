# The `next_trains` contract (Contract Version 2)

This is the canonical description of the data contract between
`ha_realtime_trains_api` (producer) and any consumer — primarily the
[`ha-train-departure-board`](https://github.com/ivmreg/ha-train-departure-board)
Lovelace card, whose `src/types.ts` (`TrainDeparture`, `CallingPoint`) mirrors this document
and whose `tests/contract.test.ts` validates the shared `sample_entity.json`
against it.

> [!WARNING]
> **Breaking Change (Contract Version 2)**
> Railway-domain normalization, filtering, delay calculation, status labeling,
> destination arrival estimation, and calling point progress tracking are owned
> canonically by the API integration. The card consumes these display-ready fields
> directly and retains only presentation/household logic (walking-time eligibility,
> countdowns, rolling-stock badge styling, and layout).
>
> **Coordinated upgrade requirement:** Updating `ha_realtime_trains_api` to Contract v2
> requires updating `ha-train-departure-board` to Contract v2, and vice versa. Legacy
> fallback parsing for Schema v1 has been intentionally removed.

All full datetimes are ISO-8601 strings with explicit timezone offsets (e.g. `2026-06-10T12:00:00+01:00`).
All display clock times are normalized `"HH:MM"` 24-hour strings.

## Sensor entity attributes

| Attribute | Type | Notes |
|---|---|---|
| `contract_version` | integer | Canonical contract version (`2`) |
| `journey_start` | string | Origin CRS code |
| `journey_end` | string \| null | Destination CRS code; absent or null for station-wide queries |
| `next_trains` | list[object] | See per-train fields below; always present, empty list when no departures exist |
| `platforms_of_interest` | list[string] | Only when a platform filter is set |
| `pinned_train` | object \| null | The matching pinned train (same shape as a `next_trains` entry); only when `pinned_departure_time` is configured and matched |
| `current_polling_interval` | integer | Active polling cadence in seconds |
| `next_update_at` | ISO datetime string | When the next refresh is due |
| `data_stale` | boolean | True while serving last-known data because the RTT API is down or rate-limited |
| `last_successful_update` | ISO datetime string | Only after at least one successful refresh |
| `error` | string \| null | Error status string when journey enrichment failed (e.g. `"Rate Limited"`) |
| `service_status` | string | Canonical rail status enum: `"normal"` \| `"delayed"` \| `"disrupted"` \| `"engineering_work"` \| `"station_closed"` \| `"no_departures"` |
| `station_messages` | list[string] | Additive station-level announcements/NRCC notices |
| `disruptions` | list[object] | Additive route/station disruption incidents (see schema below) |

The sensor **state** is the integer minutes until the next matching departure, or `None` when there are none.

## Disruption fields (`disruptions` items)

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique incident identifier |
| `title` | string | Short headline/summary of the disruption |
| `is_planned` | boolean | True for planned engineering work; false for unplanned incidents |
| `summary` | string | Detailed explanation normalized from HTML/text |
| `alternative_travel` | string \| null | Ticket acceptance or replacement bus guidance |
| `url` | string \| null | Official National Rail info link (validated `http:`/`https:`) |

## Per-train fields (`next_trains` items)

| Field | Type | Description |
|---|---|---|
| `origin_name` | string | Human-readable origin station name |
| `destination_name` | string | Human-readable destination station name |
| `service_uid` | string | RTT service identity, e.g. `"P63128"` |
| `headcode` | string | Train reporting identity, e.g. `"2A69"` |
| `type` | string | RTT mode type, e.g. `"TRAIN"` |
| `operator_name` | string | Operating company (e.g. `"Southeastern"`); scopes card rolling-stock styles |
| `scheduled` | ISO datetime string | Scheduled departure timestamp with offset |
| `estimated` | ISO datetime string \| null | Real-time estimated departure timestamp with offset |
| `scheduled_time` | string | Display-ready scheduled departure time (`"HH:MM"`) |
| `estimated_time` | string \| null | Display-ready estimated departure time (`"HH:MM"`) |
| `minutes` | integer | Minutes until departure from now |
| `delay_minutes` | integer \| null | Canonical departure delay in minutes (`null` if cancelled) |
| `status` | string | Canonical service status: `"on_time"` \| `"delayed"` \| `"early"` \| `"cancelled"` |
| `status_class` | string | CSS class for status badge: `"on-time"` \| `"delayed"` \| `"early"` \| `"cancelled"` |
| `status_label` | string | Display-ready status label (e.g. `"On Time"`, `"Exp 12:05"`, `"Early 11:55"`, `"Cancelled"`) |
| `offset_label` | string \| null | Display-ready offset string (e.g. `"+5m"`, `"-3m"`, or `null`) |
| `lateness` | integer \| null | RTT advertised lateness |
| `is_cancelled` | boolean | True if the service or departure is cancelled |
| `platform` | string \| null | Platform number |
| `length` | integer \| null | Number of carriages / vehicles |
| `stock` | string \| null | Rolling-stock branding (e.g. `"City Beam"`, `"Networker"`, `"Javelin"`) |
| `calling_points` | list[object] | Display-ready list of calling points; empty list if un-enriched (see below) |
| `destination_arrival_scheduled` | ISO datetime string \| null | Scheduled arrival at destination |
| `destination_arrival_estimated` | ISO datetime string \| null | Estimated arrival at destination |
| `destination_arrival_time` | string \| null | Display-ready clock arrival time at destination (`"HH:MM"`) |
| `destination_status` | string \| null | Arrival status at destination: `"on_time"` \| `"delayed"` \| `"early"` \| `"cancelled"` |
| `destination_delay_minutes` | integer \| null | Delay minutes upon reaching destination |
| `journey_duration_minutes` | integer \| null | Total journey duration from origin to destination in minutes |
| `stops_count` | integer \| null | Intermediate stops count between origin and destination |
| `disruption_reason` | string \| null | Disruption explanation text when provided by operator |
| `last_report_station` | string \| null | Station code of the last actual report |
| `last_report_type` | string \| null | Last report type: `"Arrival"` \| `"Departure"` \| `"Pass"` |
| `last_report_time` | ISO datetime string \| null | Timestamp of last report |
| `last_report_time_label` | string \| null | Display-ready clock time of last report (`"HH:MM"`) |
| `is_pinned` | boolean \| undefined | Present and `true` only on the query's pinned train |

## Calling Point fields (`calling_points` items)

Each item in `calling_points` is fully normalized, chronologically ordered, and enriched with live tracking state:

| Field | Type | Description |
|---|---|---|
| `station_name` | string | Station name / description (e.g. `"Lewisham"`) |
| `crs` | string \| null | 3-letter CRS code (e.g. `"LEW"`) |
| `tiploc` | string \| null | TIPLOC code (e.g. `"LEWISHM"`) |
| `scheduled` | ISO datetime string | Scheduled arrival/departure timestamp |
| `estimated` | ISO datetime string \| null | Estimated arrival/departure timestamp |
| `time` | string | Display-ready scheduled/booked clock time (`"HH:MM"`) |
| `delay_minutes` | integer \| null | Delay at this stop in minutes |
| `status` | string | Stop status: `"on_time"` \| `"delayed"` \| `"early"` \| `"cancelled"` |
| `status_class` | string | Stop status CSS class: `"on-time"` \| `"delayed"` \| `"early"` \| `"cancelled"` |
| `status_label` | string | Display-ready label (e.g. `"On time"`, `"Exp 12:20"`, `"Early 12:14"`, `"Cancelled"`) |
| `is_passed` | boolean | True if the train has already passed or departed this station |
| `is_current` | boolean | True if the train is currently arrived/standing at this station |
| `is_between_previous` | boolean | True if the live train position is currently between the preceding station and this station |

## Pinned train disruption binary sensor attributes

When `pinned_departure_time` is configured on a query, a binary sensor (`binary_sensor.your_<time>_from_<origin>_to_<destination>_disrupted`, device class `problem`) is created.

**State:** `on` when the pinned train is cancelled or delayed by 5+ minutes (threshold defined by `PINNED_DELAY_THRESHOLD_MINUTES`); `off` otherwise.

**Attributes:**

| Attribute | Type | Description |
|---|---|---|
| `pinned_departure_time` | string | Configured departure clock time (`"HH:MM"`) |
| `reason` | string \| null | Disruption summary (e.g. `"Cancelled"`, `"Delayed 8 min"`, or `null`) |
| `scheduled` | ISO datetime string \| null | Canonical scheduled departure timestamp |
| `estimated` | ISO datetime string \| null | Canonical estimated departure timestamp |
| `delay_minutes` | integer \| null | Departure delay in minutes |
| `platform` | string \| null | Departure platform |
| `is_cancelled` | boolean \| null | Cancellation flag |
| `destination_name` | string \| null | Final destination station name |
| `service_uid` | string \| null | Service identifier |
| `disruption_reason` | string \| null | Operator disruption explanation text when available |
| `train_found` | boolean | Present and `false` only when the pinned train was not found in departures |
