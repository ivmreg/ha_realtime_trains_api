# Realtime Trains API Integration Capabilities

This document outlines the capabilities of the `realtime_trains_api` Home Assistant custom component. It is intended to inform other agents or developers about what data and configuration options are available so they can build UI components or automations on top of this integration.

## Overview

The integration connects to the Realtime Trains API (api.rtt.io) to provide detailed live train departure information and journey statistics for UK railway stations. It functions as a Home Assistant `sensor` entity.

## Configuration Capabilities

The integration can be configured via Home Assistant's UI (config flow) or YAML. The following configuration parameters are supported per query (sensor):

*   **`origin`** (Required): The 3-letter CRS code (e.g., `WAT` for London Waterloo) of the departure station.
*   **`destination`** (Optional): The 3-letter CRS code of the arrival station. If omitted, the sensor monitors all trains departing from the `origin` station.
*   **`sensor_name`** (Optional): A custom name for the sensor entity (e.g., "My Commute"). If omitted, a name is automatically generated based on the origin and destination/platforms.
*   **`journey_data_for_next_X_trains`** (Optional): An integer specifying for how many upcoming departures detailed journey data (stops, estimated arrival time) should be fetched. Defaults to 0. Fetching journey data requires additional API calls.
*   **`stops_of_interest`** (Optional): A list of 3-letter CRS codes for intermediate stations. If specified, the sensor will check if the train calls at these stations and include their arrival times in the train's data. (Requires `journey_data_for_next_X_trains` > 0).
*   **`platforms_of_interest`** (Optional): A list of platform numbers (as strings). If specified, the sensor will only monitor trains departing from these specific platforms.
*   **`time_offset`** (Optional): A time offset (e.g., in minutes) to look for trains departing in the future. Useful if the user needs time to travel to the station.

### Global Configuration

*   **`scan_interval`**: How often the API is polled (default: 60 seconds).
*   **`auto_adjust_scans`** (Boolean): If enabled, the integration backs off the polling interval (to 30 minutes) when no departures are found, saving API quotas.

## Sensor Entity Model

Each configured query creates a Home Assistant `sensor` entity.

### Primary State

The main state of the sensor represents the **number of minutes until the next matching train departs** from the origin station.
*   **Unit of Measurement**: `min`
*   **Icon**: `mdi:train`

### State Attributes

The sensor exposes rich data through its state attributes, which can be parsed to build detailed UIs:

*   **`journey_start`**: The origin station code (e.g., `WAT`).
*   **`journey_end`**: The destination station code (e.g., `WAL`). Omitted if no destination is set.
*   **`platforms_of_interest`**: A list of filtered platforms, if configured.
*   **`next_trains`**: A list of dictionaries, where each dictionary represents an upcoming train.

#### `next_trains` Object Structure

Each item in the `next_trains` list contains:

*   **`origin_name`** (String): Full name of the train's starting point (e.g., "London Waterloo").
*   **`destination_name`** (String): Full name of the train's final destination.
*   **`service_uid`** (String): Unique identifier for the train service (e.g., "Q46478").
*   **`scheduled`** (String): Scheduled departure time from the origin station (Format: `DD-MM-YYYY HH:MM`).
*   **`estimated`** (String): Estimated departure time (Format: `DD-MM-YYYY HH:MM`).
*   **`minutes`** (Integer): Minutes until estimated departure.
*   **`platform`** (String): Departure platform number.
*   **`operator_name`** (String): Train operating company (e.g., "South Western Railway").

**Extended Journey Data (if `journey_data_for_next_X_trains` > 0):**

*   **`scheduled_arrival`** (String): Scheduled arrival time at the destination station.
*   **`estimate_arrival`** (String): Estimated arrival time at the destination station.
*   **`journey_time_mins`** (Integer): Estimated total journey time in minutes.
*   **`stops`** (Integer): Number of stops between origin and destination.
*   **`stops_of_interest`** (List of Objects): Data for matched intermediate stops.
    *   **`stop`** (String): CRS code of the intermediate stop.
    *   **`name`** (String): Full name of the intermediate stop.
    *   **`scheduled_stop`** (String): Scheduled arrival time at the stop.
    *   **`estimate_stop`** (String): Estimated arrival time at the stop.
    *   **`journey_time_mins`** (Integer): Journey time from origin to this stop.
    *   **`stops`** (Integer): Number of stops before reaching this intermediate stop.

### Realtime Position Information
If `journey_data` is fetched, the integration will attempt to provide the latest reporting location of the train. The frontend should handle displaying this information:
*   `last_report_station`: The CRS code of the last reported station.
*   `last_report_type`: The type of report.
*   `last_report_time`: The time of the report.

## Usage for UI Generation

An agent building a UI component for this integration should:
1.  Read the main state to show a "Next train in X mins" badge.
2.  Iterate over the `next_trains` list in the entity's attributes to display a list or table of upcoming departures.
3.  Display platform, scheduled, and estimated times.
4.  If extended journey data is present (`estimate_arrival`, `journey_time_mins`), show the expected arrival time and duration.
5.  If `stops_of_interest` is populated, display calling points with their respective arrival times.
