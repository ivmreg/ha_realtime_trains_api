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


class RealtimeTrainsApiClient:
    """Simple async client for the Realtime Trains Pull API."""

    def __init__(self, session: ClientSession, token: str, refresh_token: str | None = None) -> None:
        self._session = session
        self._token = token
        self._refresh_token = refresh_token
        self._headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}

    @property
    def token(self) -> str:
        """Return current access token."""
        return self._token

    async def async_get_access_token(self) -> str:
        """Fetch a new access token using the refresh token."""
        if not self._refresh_token:
            _LOGGER.debug("Access token refresh requested but no refresh token available")
            raise RealtimeTrainsApiAuthError("No refresh token available")

        url = f"{API_BASE}api/get_access_token"
        masked_refresh = f"{self._refresh_token[:5]}...{self._refresh_token[-5:]}" if len(self._refresh_token) > 10 else "***"
        _LOGGER.debug("Requesting new access token from %s using refresh token %s", url, masked_refresh)
        
        headers = {
            "Authorization": f"Bearer {self._refresh_token}",
            "accept": "application/json",
        }
        
        try:
            async with self._session.get(url, headers=headers) as response:
                _LOGGER.debug("Token refresh response status: %s", response.status)
                if response.status == 200:
                    json_data = await response.json()
                    _LOGGER.debug("Token refresh response body: %s", json_data)
                    new_token = json_data.get("token")
                    if not new_token:
                        _LOGGER.error("Token refresh response missing 'token' key")
                        raise RealtimeTrainsApiAuthError("Response missing token")
                    self._token = new_token
                    self._headers["Authorization"] = f"Bearer {new_token}"
                    _LOGGER.debug("Successfully refreshed RTT access token")
                    return new_token
                
                body = await response.text()
                _LOGGER.error("Failed to refresh token: %s. Response: %s", response.status, body)
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
        url = f"{API_BASE}{path}"
        # Create headers copy for logging (mask Authorization)
        log_headers = {k: (v if k.lower() != "authorization" else "Bearer ***") for k, v in self._headers.items()}
        _LOGGER.debug("RTT API Request: GET %s, Headers: %s", url, log_headers)
        
        try:
            async with self._session.get(url, headers=self._headers) as response:
                _LOGGER.debug("RTT API Response Status: %s for %s", response.status, url)
                
                if response.status == 200:
                    json_data = await response.json()
                    _LOGGER.debug("RTT API Response Body (JSON): %s", json_data)
                    return json_data
                
                if response.status in (401, 403):
                    _LOGGER.warning("RTT API Authentication error (401/403) for %s", url)
                    raise RealtimeTrainsApiAuthError("Credentials invalid") from None
                
                if response.status == 404:
                    _LOGGER.debug("RTT API Resource not found (404) for %s", url)
                    raise RealtimeTrainsApiNotFoundError(f"Endpoint returned 404 for path {path}") from None
                
                body = await response.text()
                _LOGGER.error("RTT API Unexpected response %s for %s: %s", response.status, url, body)
                raise RealtimeTrainsApiError(f"Unexpected status {response.status}")
        except Exception as err:
            if not isinstance(err, (RealtimeTrainsApiError)):
                _LOGGER.error("RTT API Connection error for %s: %s", url, err)
            raise
