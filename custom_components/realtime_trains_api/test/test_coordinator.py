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

from datetime import timedelta, datetime, time
from unittest.mock import patch, MagicMock
import pytest
import pytz
from custom_components.realtime_trains_api.coordinator import TIMEZONE

@pytest.mark.asyncio
async def test_coordinator_dynamic_interval():
    hass = MagicMock()
    logger = MagicMock()
    from unittest.mock import AsyncMock
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={"services": []})
    
    # We import here to get the patched class
    from custom_components.realtime_trains_api.coordinator import RealtimeTrainsUpdateCoordinator
    
    coordinator = RealtimeTrainsUpdateCoordinator(
        hass, logger, "test", timedelta(seconds=60), api, [{}]
    )
    coordinator.peak_interval = 60
    coordinator.off_peak_interval = 300
    coordinator.peak_windows = [(time(7, 0), time(9, 30))]
    
    # Test during peak
    mock_now_peak = datetime(2026, 4, 11, 8, 0, tzinfo=TIMEZONE)
    with patch("custom_components.realtime_trains_api.coordinator.dt_util.now", return_value=mock_now_peak):
        await coordinator._async_update_data()
        assert getattr(coordinator, "update_interval", None) == timedelta(seconds=60)
        assert coordinator.current_polling_interval == 60
        
    # Test during off-peak
    mock_now_off_peak = datetime(2026, 4, 11, 10, 0, tzinfo=TIMEZONE)
    with patch("custom_components.realtime_trains_api.coordinator.dt_util.now", return_value=mock_now_off_peak):
        await coordinator._async_update_data()
        assert getattr(coordinator, "update_interval", None) == timedelta(seconds=300)
        assert coordinator.current_polling_interval == 300
