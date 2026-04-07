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
    api.async_get_access_token = AsyncMock(side_effect=RealtimeTrainsApiAuthError("Auth failed"))
    
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
    api.async_get_access_token = AsyncMock()
    
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

from freezegun import freeze_time
from datetime import datetime

@pytest.mark.asyncio
async def test_coordinator_fetches_and_structures_data():
    hass = MagicMock()
    api = MagicMock()
    
    # Mock location services response
    api.fetch_location_services = AsyncMock(return_value={
        "services": [{
            "scheduleMetadata": {
                "identity": "123", 
                "inPassengerService": True,
                "departureDate": "2026-04-07"
            },
            "temporalData": {
                "departure": {
                    "scheduleAdvertised": "2026-04-07T12:05:00Z"
                }
            }
        }]
    })
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
    assert len(data["WAL_WAT_all_0"]["next_trains"]) == 1
    api.fetch_location_services.assert_called_once()
