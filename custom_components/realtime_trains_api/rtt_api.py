from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Any

from aiohttp import ClientSession

API_BASE = "https://data.rtt.io/"
_LOGGER = logging.getLogger(__name__)


class RealtimeTrainsApiError(Exception):
    """Base exception for RTT API issues."""


class RealtimeTrainsApiAuthError(RealtimeTrainsApiError):
    """Raised when authentication with the RTT API fails."""


class RealtimeTrainsApiNotFoundError(RealtimeTrainsApiError):
    """Raised when requested RTT API resource is not found."""


class RealtimeTrainsApiRateLimitError(RealtimeTrainsApiError):
    """Raised when the API rate limit has been exceeded or is preemptively skipped."""
    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RealtimeTrainsApiClient:
    """Simple async client for the Realtime Trains Pull API."""

    def __init__(self, session: ClientSession, token: str, refresh_token: str | None = None) -> None:
        self._session = session
        self._token = token
        self._refresh_token = refresh_token
        self._headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
        self.rate_limits: dict[str, dict[str, int | None]] = {
            "minute": {"limit": None, "remaining": None},
            "hour": {"limit": None, "remaining": None},
            "day": {"limit": None, "remaining": None},
            "week": {"limit": None, "remaining": None},
        }
        self._retry_after_timestamp: float | None = None

    @property
    def token(self) -> str:
        """Return current access token."""
        return self._token

    async def async_get_access_token(self) -> str:
        """Fetch a new access token using the refresh token."""
        if not self._refresh_token:
            raise RealtimeTrainsApiAuthError("No refresh token available")

        url = f"{API_BASE}api/get_access_token"
        headers = {
            "Authorization": f"Bearer {self._refresh_token}",
            "accept": "application/json",
        }
        
        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status == 200:
                    json_data = await response.json()
                    new_token = json_data.get("token")
                    if not new_token:
                        _LOGGER.error("Token refresh response missing 'token' key")
                        raise RealtimeTrainsApiAuthError("Response missing token")
                    self._token = new_token
                    self._headers["Authorization"] = f"Bearer {new_token}"
                    return new_token
                
                body = await response.text()
                _LOGGER.error("Failed to refresh token: %s. Response: %s", response.status, body[:200])
                raise RealtimeTrainsApiAuthError(f"Failed to refresh token: {response.status}")
        except Exception as err:
            if not isinstance(err, RealtimeTrainsApiAuthError):
                _LOGGER.error("Connection error during token refresh: %s", err)
            raise

    async def fetch_location_services(
        self,
        station: str,
        to_station: str | None = None,
        query_date: date | None = None,
        time: str | int | None = None,
        time_window: int | None = None,
    ) -> dict[str, Any]:
        """Fetch departures or arrivals for a location."""
        params = [f"code={station}"]
        if to_station:
            params.append(f"filterTo={to_station}")
        if query_date:
            if time:
                try:
                    # Robustly handle time as HHMM (string or int)
                    t_val = int(time)
                    h = t_val // 100
                    m = t_val % 100
                    iso_dt = f"{query_date.isoformat()}T{h:02d}:{m:02d}:00"
                    params.append(f"timeFrom={iso_dt}")
                except (ValueError, TypeError):
                    params.append(f"timeFrom={query_date.isoformat()}T00:00:00")
            else:
                params.append(f"timeFrom={query_date.isoformat()}T00:00:00")
        elif time:
            raise ValueError("time can only be provided when query_date is set")

        if time_window:
            params.append(f"timeWindow={time_window}")

        path = "gb-nr/location?" + "&".join(params)
        return await self._request(path)

    async def fetch_service_details(
        self,
        service_uid: str,
        run_date: date | datetime,
    ) -> dict[str, Any]:
        """Fetch detailed calling pattern for a specific service."""
        if isinstance(run_date, datetime):
            run_date = run_date.date()
        path = f"gb-nr/service?identity={service_uid}&departureDate={run_date.isoformat()}"
        return await self._request(path)

    async def _request(self, path: str) -> dict[str, Any]:
        import time
        url = f"{API_BASE}{path}"
        
        # Check if we should preemptively skip
        if self._retry_after_timestamp and time.time() < self._retry_after_timestamp:
            raise RealtimeTrainsApiRateLimitError(
                "Waiting for rate limit reset based on Retry-After header",
                retry_after=int(self._retry_after_timestamp - time.time())
            )
            

        try:
            async with self._session.get(url, headers=self._headers) as response:
                # Parse Rate Limit Headers
                for dim in ["Minute", "Hour", "Day", "Week"]:
                    limit_header = response.headers.get(f"X-RateLimit-Limit-{dim}")
                    remaining_header = response.headers.get(f"X-RateLimit-Remaining-{dim}")
                    if limit_header is not None:
                        try:
                            self.rate_limits[dim.lower()]["limit"] = int(limit_header)
                        except ValueError:
                            pass
                    if remaining_header is not None:
                        try:
                            self.rate_limits[dim.lower()]["remaining"] = int(remaining_header)
                        except ValueError:
                            pass

                if response.status == 200:
                    return await response.json()
                
                if response.status == 429:
                    retry_after = response.headers.get("Retry-After")
                    retry_after_int = int(retry_after) if retry_after and retry_after.isdigit() else 60
                    self._retry_after_timestamp = time.time() + retry_after_int
                    _LOGGER.warning(
                        "RTT API rate limit reached; retrying after %s seconds",
                        retry_after_int,
                    )
                    raise RealtimeTrainsApiRateLimitError(f"Too many requests", retry_after=retry_after_int)

                if response.status in (401, 403):
                    # An expired access token is expected periodically. The
                    # coordinator refreshes it and retries once, and reports an
                    # authentication failure only if recovery does not work.
                    raise RealtimeTrainsApiAuthError("Credentials invalid") from None
                
                if response.status == 404:
                    raise RealtimeTrainsApiNotFoundError(f"Endpoint returned 404 for path {path}") from None
                
                body = await response.text()
                _LOGGER.error("RTT API returned unexpected status %s: %s", response.status, body[:200])
                raise RealtimeTrainsApiError(f"Unexpected status {response.status}")
        except Exception as err:
            if not isinstance(err, (RealtimeTrainsApiError)):
                _LOGGER.error("RTT API connection error: %s", err)
            raise
