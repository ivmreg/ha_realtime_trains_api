# Realtime Trains API Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the Home Assistant `realtime_trains_api` integration to `DataUpdateCoordinator`, introduce HA Devices, remove legacy YAML configuration, and add a Diagnostics platform.

**Architecture:** We will introduce a centralized `DataUpdateCoordinator` that holds the `RealtimeTrainsApiClient`. All sensors will inherit from `CoordinatorEntity` and update passively. We will group sensors under HA Devices using `device_info`. Legacy YAML paths will be deleted. Finally, `async_get_config_entry_diagnostics` will be implemented to export sanitized API and configuration state.

**Tech Stack:** Python 3, Home Assistant Core APIs (`DataUpdateCoordinator`, `CoordinatorEntity`, `diagnostics`), pytest.

---

### Task 1: Create DataUpdateCoordinator

**Files:**
- Create: `custom_components/realtime_trains_api/coordinator.py`
- Create: `custom_components/realtime_trains_api/test/test_coordinator.py`

- [ ] **Step 1: Write the failing tests**

```python
# custom_components/realtime_trains_api/test/test_coordinator.py
from datetime import timedelta
import pytest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.realtime_trains_api.coordinator import RealtimeTrainsUpdateCoordinator
from custom_components.realtime_trains_api.rtt_api import (
    RealtimeTrainsApiAuthError,
    RealtimeTrainsApiRateLimitError,
    RealtimeTrainsApiError,
)

@pytest.mark.asyncio
async def test_coordinator_auth_error():
    hass = MagicMock()
    api = MagicMock()
    api.fetch_location_services = AsyncMock(side_effect=RealtimeTrainsApiAuthError("Auth failed"))
    
    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=hass,
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(minutes=1),
        api=api,
        queries=[{"origin": "WAL", "destination": "WAT"}]
    )
    
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

@pytest.mark.asyncio
async def test_coordinator_rate_limit_error():
    hass = MagicMock()
    api = MagicMock()
    api.fetch_location_services = AsyncMock(side_effect=RealtimeTrainsApiRateLimitError("Rate limit", retry_after=60))
    
    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=hass,
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(minutes=1),
        api=api,
        queries=[{"origin": "WAL", "destination": "WAT"}]
    )
    
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest custom_components/realtime_trains_api/test/test_coordinator.py -v`
Expected: FAIL with "No module named 'custom_components.realtime_trains_api.coordinator'"

- [ ] **Step 3: Write minimal implementation**

```python
# custom_components/realtime_trains_api/coordinator.py
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .rtt_api import (
    RealtimeTrainsApiAuthError,
    RealtimeTrainsApiClient,
    RealtimeTrainsApiError,
    RealtimeTrainsApiRateLimitError,
)

_LOGGER = logging.getLogger(__name__)

class RealtimeTrainsUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching Realtime Trains data."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        name: str,
        update_interval: timedelta,
        api: RealtimeTrainsApiClient,
        queries: list[dict[str, Any]],
    ) -> None:
        """Initialize."""
        super().__init__(
            hass=hass,
            logger=logger,
            name=name,
            update_interval=update_interval,
        )
        self.api = api
        self.queries = queries

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        try:
            # Note: The actual fetching logic will be moved here from the sensor in the next tasks
            # For now, we just test the error handling structure
            data = {}
            for query in self.queries:
                # Mock call to trigger errors in tests
                await self.api.fetch_location_services(query.get("origin"), query.get("destination"))
            return data
        except RealtimeTrainsApiAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except RealtimeTrainsApiRateLimitError as err:
            raise UpdateFailed(f"Rate limit hit: {err}") from err
        except RealtimeTrainsApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest custom_components/realtime_trains_api/test/test_coordinator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/realtime_trains_api/coordinator.py custom_components/realtime_trains_api/test/test_coordinator.py
git commit -m "feat: add DataUpdateCoordinator structure"
```

### Task 2: Implement Data Fetching Logic in Coordinator

**Files:**
- Modify: `custom_components/realtime_trains_api/coordinator.py`
- Modify: `custom_components/realtime_trains_api/test/test_coordinator.py`

- [ ] **Step 1: Write the failing test**

```python
# append to custom_components/realtime_trains_api/test/test_coordinator.py
from freezegun import freeze_time
from datetime import datetime

@pytest.mark.asyncio
async def test_coordinator_fetches_and_structures_data():
    hass = MagicMock()
    api = MagicMock()
    
    # Mock location services response
    api.fetch_location_services = AsyncMock(return_value={"services": [{"scheduleMetadata": {"identity": "123", "inPassengerService": True}}]})
    # Mock service details response
    api.fetch_service_details = AsyncMock(return_value={"service": {"locations": []}})
    
    # Mock token refresh
    api.async_get_access_token = AsyncMock(return_value="new_token")
    
    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=hass,
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(minutes=1),
        api=api,
        queries=[{"origin": "WAL", "destination": "WAT", "journey_data_for_next_X_trains": 1, "platforms_of_interest": [], "time_offset": timedelta()}]
    )
    
    with freeze_time("2026-04-07 12:00:00"):
        data = await coordinator._async_update_data()
        
    assert "WAL_WAT_all_0" in data
    assert len(data["WAL_WAT_all_0"]["services"]) == 1
    api.fetch_location_services.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest custom_components/realtime_trains_api/test/test_coordinator.py::test_coordinator_fetches_and_structures_data -v`
Expected: FAIL

- [ ] **Step 3: Write implementation**

Move the logic from `sensor.py` (`_load_departures` and the `_async_update` parsing loop) into the coordinator.

```python
# modify custom_components/realtime_trains_api/coordinator.py
# Add these imports at the top
import pytz
from homeassistant.util import dt as dt_util
from .sensor_helpers import retry_with_auth_refresh

TIMEZONE = pytz.timezone('Europe/London')

# Replace _async_update_data with:
    async def _async_refresh_token(self) -> bool:
        """Refresh the access token."""
        try:
            await self.api.async_get_access_token()
            return True
        except RealtimeTrainsApiAuthError as err:
            _LOGGER.error("Failed to refresh RTT access token: %s", err)
            return False

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API endpoint."""
        now = dt_util.now().astimezone(TIMEZONE)
        result_data = {}

        try:
            for query in self.queries:
                origin = query.get("origin")
                destination = query.get("destination")
                platforms = query.get("platforms_of_interest", [])
                time_offset = query.get("time_offset", timedelta())
                
                # Generate key based on same logic as unique_id
                platforms_str = "_".join(sorted(platforms)) if platforms else "all"
                dest_str = destination if destination else "all"
                offset_str = f"{int(time_offset.total_seconds())}" if time_offset.total_seconds() > 0 else "0"
                query_key = f"{origin}_{dest_str}_{platforms_str}_{offset_str}"

                _LOGGER.debug(
                    "Fetching location services for %s to %s at %s",
                    origin,
                    destination,
                    now.strftime("%H%M"),
                )
                
                data = await retry_with_auth_refresh(
                    lambda: self.api.fetch_location_services(
                        origin,
                        destination,
                        now.date(),
                        now.strftime("%H%M"),
                    ),
                    self._async_refresh_token,
                )
                
                result_data[query_key] = data or {}

            return result_data
        except RealtimeTrainsApiAuthError as err:
            raise ConfigEntryAuthFailed(err) from err
        except RealtimeTrainsApiRateLimitError as err:
            raise UpdateFailed(f"Rate limit hit: {err}") from err
        except RealtimeTrainsApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest custom_components/realtime_trains_api/test/test_coordinator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/realtime_trains_api/coordinator.py custom_components/realtime_trains_api/test/test_coordinator.py
git commit -m "feat: implement data fetching in coordinator"
```

### Task 3: Refactor Sensors to use CoordinatorEntity and Device Info

**Files:**
- Modify: `custom_components/realtime_trains_api/sensor.py`
- Modify: `custom_components/realtime_trains_api/__init__.py`

- [ ] **Step 1: Write the failing tests**

We will update the integration tests to expect CoordinatorEntity behavior in a separate task, but for now, we need to ensure the setup entry creates the coordinator.

- [ ] **Step 2: Update `__init__.py` to create the coordinator**

```python
# custom_components/realtime_trains_api/__init__.py
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, PLATFORMS, CONF_API_TOKEN, CONF_REFRESH_TOKEN, CONF_SCAN_INTERVAL, CONF_AUTOADJUSTSCANS, CONF_QUERIES, DEFAULT_SCAN_INTERVAL
from .normalization import coerce_scan_interval
from .rtt_api import RealtimeTrainsApiClient
from .coordinator import RealtimeTrainsUpdateCoordinator
import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Realtime Trains API from a config entry."""
    token = entry.data.get(CONF_API_TOKEN)
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)

    if not token and not refresh_token:
        _LOGGER.error("Realtime Trains API entry %s is missing credentials", entry.entry_id)
        return False

    interval = coerce_scan_interval(
        entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        DEFAULT_SCAN_INTERVAL,
    )
    queries = entry.options.get(CONF_QUERIES) or entry.data.get(CONF_QUERIES, [])

    client = async_get_clientsession(hass)
    api_client = RealtimeTrainsApiClient(client, token, refresh_token)

    coordinator = RealtimeTrainsUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_interval=interval,
        api=api_client,
        queries=queries,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "api_client": api_client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
```
*Note: Also delete `async_setup`.*

- [ ] **Step 3: Update `sensor.py` to use CoordinatorEntity and Devices**

```python
# custom_components/realtime_trains_api/sensor.py
# Add imports:
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
# Delete PLATFORM_SCHEMA and async_setup_platform.

# Modify async_setup_entry
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = domain_data["coordinator"]
    api_client = domain_data["api_client"]
    
    queries = entry.options.get(CONF_QUERIES) or entry.data.get(CONF_QUERIES, [])

    sensors = []
    seen_unique_ids = set()

    for idx, raw_query in enumerate(queries or []):
        try:
            query = _normalize_query(raw_query)
        except ValueError as err:
            _LOGGER.warning("Skipping RTT query configuration: %s", err)
            continue
            
        sensor = RealtimeTrainLiveTrainTimeSensor(
            coordinator,
            query.get(CONF_SENSORNAME),
            query[CONF_START],
            query[CONF_END],
            query[CONF_JOURNEYDATA],
            query[CONF_TIMEOFFSET],
            query[CONF_PLATFORMS_OF_INTEREST],
            entry.entry_id,
            idx,
        )
        
        if sensor.unique_id and sensor.unique_id in seen_unique_ids:
            continue
            
        if sensor.unique_id:
            seen_unique_ids.add(sensor.unique_id)
        sensors.append(sensor)

    sensors.append(RealtimeTrainRateLimitSensor(coordinator, api_client, entry.entry_id))

    if sensors:
        async_add_entities(sensors, True)

# Modify RealtimeTrainLiveTrainTimeSensor to inherit from CoordinatorEntity
class RealtimeTrainLiveTrainTimeSensor(CoordinatorEntity, SensorEntity):
    def __init__(
        self,
        coordinator,
        sensor_name: str | None,
        journey_start: str,
        journey_end: str | None,
        journey_data_for_next_X_trains: int,
        timeoffset: timedelta,
        platforms_of_interest: list[str],
        entry_id: str,
        query_index: int = 0,
    ) -> None:
        super().__init__(coordinator)
        # Keep existing init logic, but remove api_client, interval, autoadjustscans
        # Add Device Info:
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{journey_start}_{journey_end or 'all'}")},
            name=f"RTT {journey_start} to {journey_end or 'All'}",
            manufacturer="Realtime Trains",
            entry_type=DeviceEntryType.SERVICE,
        )
        
        # Determine query_key for looking up data in coordinator
        platforms_str = "_".join(sorted(self._platforms_of_interest)) if self._platforms_of_interest else "all"
        dest_str = journey_end if journey_end else "all"
        offset_str = f"{int(timeoffset.total_seconds())}" if timeoffset.total_seconds() > 0 else "0"
        self.query_key = f"{journey_start}_{dest_str}_{platforms_str}_{offset_str}"
        self._attr_unique_id = f"{entry_id}_{self.query_key}_{query_index}"
        
        # Remove self.async_update = self._async_update
        
    @property
    def native_value(self):
        # Update logic to read from self.coordinator.data[self.query_key]
        # Move the parsing loop from _async_update here or to a separate processing method
        return self._calculate_state()
        
    def _calculate_state(self):
        # Implement parsing logic reading from self.coordinator.data
        if not self.coordinator.data or self.query_key not in self.coordinator.data:
            return None
        # ... extract next departure ...
        return 0 # Placeholder

# Modify RealtimeTrainRateLimitSensor
class RealtimeTrainRateLimitSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, api_client, entry_id: str) -> None:
        super().__init__(coordinator)
        self._api = api_client
        self._attr_unique_id = f"{entry_id}_rate_limit"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_api_client")},
            name="RTT API Client",
            manufacturer="Realtime Trains",
            entry_type=DeviceEntryType.SERVICE,
        )
        # remove async_update override
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest custom_components/realtime_trains_api/test -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/realtime_trains_api/__init__.py custom_components/realtime_trains_api/sensor.py
git commit -m "feat: migrate sensors to CoordinatorEntity and Devices"
```

### Task 4: Complete Sensor Data Parsing

**Files:**
- Modify: `custom_components/realtime_trains_api/sensor.py`

- [ ] **Step 1: Write the failing test**

```python
# Add test verifying sensor state updates based on coordinator data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Restore the full `_async_update` parsing logic into a non-async `_handle_coordinator_update` method in `RealtimeTrainLiveTrainTimeSensor`, extracting data from `self.coordinator.data[self.query_key]`. 
*Note: We cannot make secondary API calls for `_add_journey_data` from within the sensor property anymore since properties are synchronous. We must move `fetch_service_details` into the coordinator's update loop or execute it async via a listener.*

For this step, adapt `_add_journey_data` to be called during `_async_update_data` in the coordinator, appending the enriched details directly into the `services` array of the coordinator data.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/realtime_trains_api/sensor.py custom_components/realtime_trains_api/coordinator.py
git commit -m "feat: complete sensor state parsing from coordinator"
```

### Task 5: Implement Diagnostics Platform

**Files:**
- Create: `custom_components/realtime_trains_api/diagnostics.py`
- Create: `custom_components/realtime_trains_api/test/test_diagnostics.py`

- [ ] **Step 1: Write the failing test**

```python
# custom_components/realtime_trains_api/test/test_diagnostics.py
import pytest
from unittest.mock import MagicMock
from homeassistant.components.diagnostics import async_redact_data

from custom_components.realtime_trains_api.diagnostics import async_get_config_entry_diagnostics
from custom_components.realtime_trains_api.const import DOMAIN, CONF_API_TOKEN, CONF_REFRESH_TOKEN

@pytest.mark.asyncio
async def test_diagnostics_redacts_tokens():
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_API_TOKEN: "secret_token", CONF_REFRESH_TOKEN: "secret_refresh"}
    entry.entry_id = "test_id"
    
    coordinator = MagicMock()
    coordinator.data = {"test": "data"}
    
    api = MagicMock()
    api.rate_limits = {"minute": {"limit": 30}}
    
    hass.data = {DOMAIN: {entry.entry_id: {"coordinator": coordinator, "api_client": api}}}
    
    result = await async_get_config_entry_diagnostics(hass, entry)
    
    assert result["entry"]["data"][CONF_API_TOKEN] == "**REDACTED**"
    assert result["entry"]["data"][CONF_REFRESH_TOKEN] == "**REDACTED**"
    assert result["rate_limits"]["minute"]["limit"] == 30
    assert result["coordinator_data"] == {"test": "data"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest custom_components/realtime_trains_api/test/test_diagnostics.py -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# custom_components/realtime_trains_api/diagnostics.py
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_API_TOKEN, CONF_REFRESH_TOKEN

TO_REDACT = {CONF_API_TOKEN, CONF_REFRESH_TOKEN}

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    domain_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = domain_data["coordinator"]
    api_client = domain_data["api_client"]

    diagnostics_data = {
        "entry": {
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": async_redact_data(entry.options, TO_REDACT),
        },
        "coordinator_data": coordinator.data,
        "rate_limits": api_client.rate_limits,
    }

    return diagnostics_data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. pytest custom_components/realtime_trains_api/test/test_diagnostics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/realtime_trains_api/diagnostics.py custom_components/realtime_trains_api/test/test_diagnostics.py
git commit -m "feat: implement diagnostics platform"
```