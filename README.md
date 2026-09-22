# realtime_trains_api
api.rtt.io Home Assistant integration

> [!WARNING]
> **Breaking Change (Contract Version 2)**
> The integration now canonically calculates, normalizes, and enriches all railway-domain attributes (display-ready timestamps, delays, canonical statuses, calling points with live progress tracking, and destination arrival).
> **Coordinated Upgrade Required:** The companion Lovelace card [`ha-train-departure-board`](https://github.com/ivmreg/ha-train-departure-board) must be updated to Contract v2 alongside this integration. See [CONTRACT.md](CONTRACT.md) for full schema specifications.

It provides detailed live train departures and journey stats:

```yaml
contract_version: 2
journey_start: WAT
journey_end: WAL
next_trains:
  - origin_name: London Waterloo
    destination_name: Basingstoke
    service_uid: Q46478
    headcode: "1B50"
    type: TRAIN
    operator_name: South Western Railway
    scheduled: "2026-06-10T20:12:00+01:00"
    estimated: "2026-06-10T20:12:00+01:00"
    scheduled_time: "20:12"
    estimated_time: "20:12"
    minutes: 3
    delay_minutes: 0
    status: on_time
    status_class: on-time
    status_label: "On Time"
    offset_label: null
    is_cancelled: false
    platform: "10"
    length: 8
    stock: null
    calling_points: []
    destination_arrival_scheduled: "2026-06-10T20:37:00+01:00"
    destination_arrival_estimated: "2026-06-10T20:36:00+01:00"
    destination_arrival_time: "20:36"
    destination_status: on_time
    destination_delay_minutes: -1
    journey_duration_minutes: 24
    stops_count: 2
unit_of_measurement: min
icon: mdi:train
friendly_name: Next Waterloo train data
```

This Home Assistant integration is only made possible by the brilliant Realtime Trains API (https://api.rtt.io also see https://www.realtimetrains.co.uk) which is maintained by Tom Cairns under swlines Ltd (https://twitter.com/swlines).

Alternatively, you can use the built-in `uk_transport` integration (see https://www.home-assistant.io/integrations/uk_transport/).  NOTE: Unlike this `realtime_trains_api` integration, `uk_transport` cannot provide additional journey details such as stops, journey durations and arrival times.

# Guide

## Features

### UI Configuration Support

Sensors created via config entries have unique IDs, allowing you to:
- Rename entities from the UI
- Customize entity settings
- Enable/disable entities
- Move entities to different areas

Note: Sensors configured via YAML (legacy method) do not have unique IDs and cannot be managed from the UI.

### Monitor All Trains from a Station or Platform

You can now monitor all trains passing through a station, with optional filtering by:

- **All trains from a station**: Omit the `destination` parameter to get all departures
- **Specific platform(s)**: Use `platforms_of_interest` to filter by platform numbers
- **Destination filtering**: Include `destination` for traditional origin-to-destination queries

This is useful for:
- Monitoring a busy station to see all available services
- Tracking trains from a specific platform at your local station
- Planning journeys when you have multiple destination options

### Pin "your" train

Add `pinned_departure_time: "07:42"` (the scheduled HH:MM departure) to a query
to pin a recurring service you actually care about:

- The matching entry in `next_trains` gains `is_pinned: true`, and the sensor
  exposes it directly as a `pinned_train` attribute.
- A `binary_sensor` ("Your 07:42 from DFD to CST disrupted", device class
  *problem*) turns on when that train is cancelled or running 5+ minutes
  late — trigger your "leave earlier" automation from it directly, no
  templating needed.

This supersedes the `blueprint.yaml` + `rest_command` approach for the common
"tell me if my usual train is disrupted" case: no extra secrets, and it reuses
the integration's own authenticated, rate-limit-aware client. The blueprint
remains for advanced cases (e.g. tracking a service the sensor's station pair
doesn't cover).

### Resilience & freshness attributes

Each sensor exposes attributes a dashboard can use to judge how fresh the data is:

- `current_polling_interval` / `next_update_at` — the active polling cadence and when the next refresh is due.
- `data_stale` / `last_successful_update` — when the RTT API is down or rate-limited, the integration keeps serving the last-known departures (instead of the sensor going unavailable) and sets `data_stale: true` so a card can show a "last-known data" indicator.

### Diagnostics

The integration implements Home Assistant config-entry diagnostics: on the integration page, use "Download diagnostics" to get a redacted bundle (configured queries, polling state, rate-limit budget) for bug reports.

## Installation & Usage

1. Signup to https://api.rtt.io
2. Add repository to HACS (see https://hacs.xyz/docs/faq/custom_repositories) - use "https://github.com/megakid/ha_realtime_trains_api" as the repository URL.
3. Install the `realtime_trains_api` integration inside HACS
4. To your HA `configuration.yaml`, add the following:
```yaml
sensor:
  - platform: realtime_trains_api
    token: '[Your RTT API Token]' # recommended to use '!secret my_rtt_token' and add to secrets.yaml
    auto_adjust_scans: true # If no departures are retrieved, back off polling interval to 30 mins (until there are some trains)
    queries:
      - origin: WAL
        destination: WAT
        # journey_data_for_next_X_trains is optional but highly recommended, 
        # Defaults to 0. 
        # Entering 5 here means the first 5 departures from the origin 
        # (WAL in this case) to destination (WAT in this case) will hit 
        # the API to lookup the number of stops, journey time and estimated
        # arrival time to the destination (WAT in this case).
        journey_data_for_next_X_trains: 5
      - origin: WAT
        destination: WAL
        sensor_name: My Custom Journey # this will appear as 'sensor.my_custom_journey'
        time_offset:
          minutes: 20 # This will display departures from now+20 minutes - useful if the station is 20 minutes travel/walk away.
      - origin: CLJ
        # destination is optional. If omitted, all trains from the origin station will be monitored
        platforms_of_interest:
          - '10'
          - '11' # Only monitor trains departing from platforms 10 and 11
        journey_data_for_next_X_trains: 3
      - origin: WAT
        # Monitor all trains from Waterloo (no destination, no platform filter)
        journey_data_for_next_X_trains: 10
      - origin: DFD
        destination: CST
        # max_trains caps how many departures are listed in next_trains.
        # If omitted it defaults to journey_data_for_next_X_trains (when set)
        # or 10. journey_data_for_next_X_trains only controls how many of the
        # listed trains get journey details (stops, arrival time, duration).
        max_trains: 8
        journey_data_for_next_X_trains: 3
```
5. Restart HA
6. Your `sensor` will be named something like `sensor.next_train_from_wal_to_wat` (unless you specified a `sensor_name`) for each query you defined in your configuration.

## Blueprint: Track Train by Schedule

This repository also includes a Home Assistant blueprint (`blueprint.yaml`) for tracking a specific scheduled train and receiving notifications for delays, cancellations, or platform changes.

### Blueprint Prerequisites

The blueprint requires configuring `rest_command` in your `configuration.yaml` to interact with the RTT v2 Token API.

1. Add your complete RTT refresh token authorization header to `secrets.yaml`:
```yaml
rtt_refresh_token_header: "Bearer [Your RTT Refresh Token]"
```

2. Add the following to your `configuration.yaml`:
```yaml
rest_command:
  rtt_get_token:
    url: "https://data.rtt.io/api/get_access_token"
    headers:
      accept: "application/json"
      Authorization: !secret rtt_refresh_token_header
  rtt_search:
    url: "https://data.rtt.io/gb-nr/location?code={{ origin }}&filterTo={{ destination }}&timeFrom={{ date }}T{{ '%02d' | format((time | int / 100) | int) }}:{{ '%02d' | format(time | int % 100) }}:00"
    headers:
      Authorization: "Bearer {{ token }}"
  rtt_service:
    url: "https://data.rtt.io/gb-nr/service?identity={{ uid }}&departureDate={{ date }}"
    headers:
      Authorization: "Bearer {{ token }}"
```

3. Import the `blueprint.yaml` into your Home Assistant instance to create automations based on train schedules.
