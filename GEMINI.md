# Realtime Trains API - Home Assistant Integration

## Project Overview

This project is a custom component for Home Assistant that provides detailed, real-time train departures and journey statistics using the [Realtime Trains API (api.rtt.io)](https://api.rtt.io). Unlike the built-in `uk_transport` integration, it offers advanced journey details such as intermediate stops, journey durations, and estimated arrival times. 

Key features include:
- UI Configuration Flow support (sensors with unique IDs).
- Monitoring all trains from a specific station or platform.
- Destination filtering.
- A Home Assistant blueprint (`blueprint.yaml`) for tracking scheduled trains and notifying on delays, cancellations, or platform changes.

The main codebase is located in `custom_components/realtime_trains_api/`. The project also contains an OpenAPI specification for the Realtime Trains API in `main.yml`.

## Building and Running

As a Home Assistant custom component, the code is "installed" by copying the `custom_components/realtime_trains_api` directory into a Home Assistant installation's `custom_components` folder, or by using HACS (Home Assistant Community Store).

### Development Environment
The project is configured for development using Dev Containers (`.devcontainer/devcontainer.json`). Opening the project in a Dev Container will automatically set up Python and the required VSCode extensions.

### Testing
Tests are written using `pytest`. You can run the test suite using the following command:
```bash
pytest custom_components/realtime_trains_api/test
```

## Development Conventions

- **Language:** Python 3.
- **Linting & Formatting:** The project uses `ruff` (as indicated by the Dev Container configuration) and standard Python type hinting (`from __future__ import annotations`).
- **Framework:** Adheres to Home Assistant custom component architecture and async paradigms (e.g., `async_setup_entry`, `async_unload_entry`).
- **Dependencies:** The repository includes HACS metadata (`hacs.json`) and a standard HA manifest (`manifest.json`). Ensure any new dependencies are added to the manifest.
