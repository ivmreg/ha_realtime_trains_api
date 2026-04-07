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
