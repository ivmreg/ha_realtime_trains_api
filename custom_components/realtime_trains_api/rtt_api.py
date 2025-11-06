from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Any

from aiohttp import BasicAuth, ClientSession

API_BASE = "https://api.rtt.io/api/v1/json/"
_LOGGER = logging.getLogger(__name__)


class RealtimeTrainsApiError(Exception):
    """Base exception for RTT API issues."""


class RealtimeTrainsApiAuthError(RealtimeTrainsApiError):
    """Raised when authentication with the RTT API fails."""


class RealtimeTrainsApiNotFoundError(RealtimeTrainsApiError):
    """Raised when requested RTT API resource is not found."""


class RealtimeTrainsApiClient:
    """Simple async client for the Realtime Trains Pull API."""

    def __init__(self, session: ClientSession, username: str, password: str) -> None:
        self._session = session
        self._auth = BasicAuth(login=username, password=password, encoding="utf-8")

    async def fetch_location_services(
        self,
        station: str,
        to_station: str | None = None,
        query_date: date | None = None,
        time: str | None = None,
    ) -> dict[str, Any]:
        """Fetch departures or arrivals for a location."""
        path_parts = ["search", station]
        if to_station:
            path_parts.extend(["to", to_station])
        if query_date:
            path_parts.extend(
                [
                    f"{query_date.year:04d}",
                    f"{query_date.month:02d}",
                    f"{query_date.day:02d}",
                ]
            )
            if time:
                path_parts.append(time)
        elif time:
            raise ValueError("time can only be provided when query_date is set")
        return await self._request("/".join(path_parts))

    async def fetch_service_details(
        self,
        service_uid: str,
        run_date: date | datetime,
    ) -> dict[str, Any]:
        """Fetch detailed calling pattern for a specific service."""
        if isinstance(run_date, datetime):
            run_date = run_date.date()
        path = f"service/{service_uid}/{run_date:%Y/%m/%d}"
        return await self._request(path)

    async def _request(self, path: str) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        async with self._session.get(url, auth=self._auth) as response:
            if response.status == 200:
                return await response.json()
            if response.status == 403:
                raise RealtimeTrainsApiAuthError("Credentials invalid") from None
            if response.status == 404:
                raise RealtimeTrainsApiNotFoundError(
                    f"Endpoint returned 404 for path {path}"
                ) from None
            body = await response.text()
            message = body[:200] if body else ""
            raise RealtimeTrainsApiError(
                f"Unexpected status {response.status} for path {path}: {message}"
            )
