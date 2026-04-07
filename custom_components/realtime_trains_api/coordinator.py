from datetime import timedelta
import logging
from typing import Any
import pytz

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .sensor_helpers import retry_with_auth_refresh
from .rtt_api import (
    RealtimeTrainsApiAuthError,
    RealtimeTrainsApiClient,
    RealtimeTrainsApiError,
    RealtimeTrainsApiRateLimitError,
)

_LOGGER = logging.getLogger(__name__)
TIMEZONE = pytz.timezone('Europe/London')

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
