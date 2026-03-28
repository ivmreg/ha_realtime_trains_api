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

    def __init__(self, session: ClientSession, token: str) -> None:
        self._session = session
        self._headers = {"Authorization": f"Bearer {token}"}

    async def fetch_location_services(
        self,
        station: str,
        to_station: str | None = None,
        query_date: date | None = None,
        time: str | None = None,
    ) -> dict[str, Any]:
        """Fetch departures or arrivals for a location."""
        # Note: the new API expects ISO 8601 datetimes. Since this integration
        # largely monitors "next" trains, query_date and time aren't heavily
        # mapped in sensor logic right now (it uses 'now'), but if provided
        # we can pass them.
        params = [f"code={station}"]
        if to_station:
            params.append(f"filterTo={to_station}")
        if query_date:
            if time and len(time) == 4:
                # time is usually HHMM
                iso_dt = f"{query_date.isoformat()}T{time[:2]}:{time[2:]}:00"
                params.append(f"timeFrom={iso_dt}")
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
        async with self._session.get(url, headers=self._headers) as response:
            if response.status == 200:
                return await response.json()
            if response.status in (401, 403):
                raise RealtimeTrainsApiAuthError("Credentials invalid") from None
            if response.status == 404:
                _LOGGER.debug("Endpoint returned 404 for path %s", path)
                raise RealtimeTrainsApiNotFoundError(
                    f"Endpoint returned 404 for path {path}"
                ) from None
            body = await response.text()
            body_len = len(body)
            body_preview = body[:200]
            _LOGGER.debug(
                "Unexpected status %s for path %s (body len=%d, preview=%r)",
                response.status,
                path,
                body_len,
                body_preview,
            )
            raise RealtimeTrainsApiError(
                f"Unexpected status {response.status}"
            )
