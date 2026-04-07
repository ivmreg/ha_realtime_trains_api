# Realtime Trains API Modernization Design

## Overview
This design document outlines a comprehensive modernization of the Home Assistant `realtime_trains_api` integration. The goals are to migrate to the standard `DataUpdateCoordinator` pattern, introduce Home Assistant Devices for grouping, completely deprecate legacy YAML configuration, and implement a Diagnostics platform for easier user troubleshooting.

## Components & Architecture

### 1. DataUpdateCoordinator Migration
- **Current Problem:** Each sensor independently polls the API using the `@Throttle` decorator and handles its own errors (e.g., setting state to "Credentials invalid" or "Rate Limited").
- **New Architecture:** We will introduce a subclass of `DataUpdateCoordinator` in `coordinator.py`.
  - The coordinator will hold the `RealtimeTrainsApiClient`.
  - It will be responsible for fetching data (`_async_update_data`).
  - API errors (Auth, Not Found, Rate Limit) will be caught by the coordinator and raised as `UpdateFailed` or `ConfigEntryAuthFailed` exceptions.
  - Sensors will inherit from `CoordinatorEntity`. Their state will automatically become `unavailable` when updates fail, which is the idiomatic HA approach.
  - The `RealtimeTrainRateLimitSensor` will also become a `CoordinatorEntity` and update passively when the coordinator pushes new data.

### 2. Home Assistant Devices
- **Current Problem:** Sensors are floating entities without a parent device, making them difficult to manage in the UI.
- **New Architecture:** We will group sensors under a Home Assistant Device.
  - The Device will represent the specific station query (e.g., "London Waterloo Departures" or "Departures: WAT -> WAL").
  - The Device ID will be generated uniquely based on the origin, destination, and platforms.
  - When creating sensors in `_create_sensors`, we will attach the `device_info` property to link them to this parent device.
  - The `RealtimeTrainRateLimitSensor` will be attached to a global "RTT API Client" device for the integration entry.

### 3. Removal of YAML Configuration
- **Current Problem:** Legacy `PLATFORM_SCHEMA` and `async_setup_platform` exist in `sensor.py`, creating redundant setup paths.
- **New Architecture:** 
  - Delete `PLATFORM_SCHEMA` and `async_setup_platform` from `sensor.py`.
  - Delete the `async_setup` function from `__init__.py` (it currently just returns True but explicitly notes it's for deprecated YAML).
  - Ensure the configuration strictly relies on `async_setup_entry` and the config flow.

### 4. Diagnostics Platform (`diagnostics.py`)
- **Current Problem:** Troubleshooting requires users to hunt through debug logs for JSON payloads and manually redact their API keys.
- **New Architecture:** Create a `diagnostics.py` file to implement the Home Assistant Diagnostics platform (`async_get_config_entry_diagnostics`).
  - This function will return a dictionary containing:
    1. The sanitized configuration entry data (redacting the API and refresh tokens using `async_redact_data` with `TO_REDACT` constants).
    2. The latest raw data payload held by the `DataUpdateCoordinator`.
    3. The current state of the API rate limits (`api_client.rate_limits`).
  - Users can download this JSON file directly from the Device page in the UI to share on GitHub issues.

## Testing Strategy
- Tests for `sensor.py` and `rtt_api.py` will be updated to reflect the `DataUpdateCoordinator` mock injection.
- Add a new test file `test_diagnostics.py` to ensure the tokens are successfully redacted from the diagnostics output.
- Verify that `ConfigEntryAuthFailed` is raised when a `RealtimeTrainsApiAuthError` is encountered.
