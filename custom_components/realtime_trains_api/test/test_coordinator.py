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
