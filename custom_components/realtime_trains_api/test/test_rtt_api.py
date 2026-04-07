from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from custom_components.realtime_trains_api.rtt_api import (
    API_BASE,
    RealtimeTrainsApiAuthError,
    RealtimeTrainsApiClient,
    RealtimeTrainsApiError,
    RealtimeTrainsApiNotFoundError,
)


class MockResponse:
    """Minimal async context manager to mimic aiohttp responses."""

    def __init__(self, status: int, *, json_data: dict | None = None, text_data: str = "", headers: dict | None = None) -> None:
        self.status = status
        self._json_data = json_data
        self._text_data = text_data
        self.headers = headers or {}

    async def __aenter__(self) -> "MockResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def json(self) -> dict:
        if self._json_data is None:
            raise ValueError("No JSON data configured")
        return self._json_data

    async def text(self) -> str:
        return self._text_data


class MockSession:
    """Fake client session capturing calls to get()."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._responses: dict[str, MockResponse] = {}

    def queue_response(self, url: str, response: MockResponse) -> None:
        self._responses[url] = response

    def get(self, url: str, **kwargs) -> MockResponse:
        self.calls.append((url, kwargs))
        try:
            return self._responses[url]
        except KeyError as err:
            raise AssertionError(f"No mock response queued for {url}") from err


@pytest.fixture(name="sample_departures")
def fixture_sample_departures() -> dict:
    sample_path = Path(__file__).with_name("sample.json")
    return json.loads(sample_path.read_text())


@pytest.mark.asyncio
async def test_fetch_location_services_returns_sample(sample_departures: dict) -> None:
    session = MockSession()
    url = f"{API_BASE}gb-nr/location?code=BKH&filterTo=CST"
    session.queue_response(url, MockResponse(200, json_data=sample_departures))
    client = RealtimeTrainsApiClient(session, "token123")

    data = await client.fetch_location_services("BKH", "CST")

    assert data == sample_departures
    assert session.calls[0][0] == url
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer token123"


@pytest.mark.asyncio
async def test_fetch_location_services_builds_dated_path(sample_departures: dict) -> None:
    session = MockSession()
    dated_url = f"{API_BASE}gb-nr/location?code=BKH&filterTo=CST&timeFrom=2025-11-04T17:30:00"
    session.queue_response(dated_url, MockResponse(200, json_data=sample_departures))
    client = RealtimeTrainsApiClient(session, "token123")

    data = await client.fetch_location_services("BKH", "CST", date(2025, 11, 4), "1730")

    assert data["services"]
    assert session.calls[-1][0] == dated_url


@pytest.mark.asyncio
async def test_fetch_location_services_requires_date_with_time() -> None:
    client = RealtimeTrainsApiClient(MockSession(), "token123")

    with pytest.raises(ValueError):
        await client.fetch_location_services("BKH", "CST", time="1730")


@pytest.mark.asyncio
async def test_fetch_location_services_auth_error() -> None:
    session = MockSession()
    url = f"{API_BASE}gb-nr/location?code=BKH&filterTo=CST"
    session.queue_response(url, MockResponse(403))
    client = RealtimeTrainsApiClient(session, "token123")

    with pytest.raises(RealtimeTrainsApiAuthError):
        await client.fetch_location_services("BKH", "CST")


@pytest.mark.asyncio
async def test_fetch_location_services_not_found() -> None:
    session = MockSession()
    url = f"{API_BASE}gb-nr/location?code=BKH&filterTo=XXX"
    session.queue_response(url, MockResponse(404))
    client = RealtimeTrainsApiClient(session, "token123")

    with pytest.raises(RealtimeTrainsApiNotFoundError):
        await client.fetch_location_services("BKH", "XXX")


@pytest.mark.asyncio
async def test_fetch_service_details_uses_date(sample_departures: dict) -> None:
    session = MockSession()
    service_url = f"{API_BASE}gb-nr/service?identity=P18676&departureDate=2025-11-04"
    session.queue_response(service_url, MockResponse(200, json_data=sample_departures))
    client = RealtimeTrainsApiClient(session, "token123")

    data = await client.fetch_service_details("P18676", datetime(2025, 11, 4, 17, 0))

    assert data["location"]["crs"] == "BKH"
    assert session.calls[-1][0] == service_url


@pytest.mark.asyncio
async def test_fetch_service_details_unexpected_status() -> None:
    session = MockSession()
    service_url = f"{API_BASE}gb-nr/service?identity=P18676&departureDate=2025-11-04"
    session.queue_response(service_url, MockResponse(500, text_data="server error"))
    client = RealtimeTrainsApiClient(session, "token123")

    with pytest.raises(RealtimeTrainsApiError) as err:
        await client.fetch_service_details("P18676", date(2025, 11, 4))

    assert "500" in str(err.value)


@pytest.mark.asyncio
async def test_request_rate_limit_parsing(sample_departures: dict) -> None:
    session = MockSession()
    url = f"{API_BASE}gb-nr/location?code=BKH&filterTo=CST"
    headers = {
        "X-RateLimit-Limit-Minute": "30",
        "X-RateLimit-Remaining-Minute": "29",
        "X-RateLimit-Limit-Hour": "750",
        "X-RateLimit-Remaining-Hour": "740"
    }
    session.queue_response(url, MockResponse(200, json_data=sample_departures, headers=headers))
    client = RealtimeTrainsApiClient(session, "token123")

    await client.fetch_location_services("BKH", "CST")

    assert client.rate_limits["minute"]["limit"] == 30
    assert client.rate_limits["minute"]["remaining"] == 29
    assert client.rate_limits["hour"]["limit"] == 750
    assert client.rate_limits["hour"]["remaining"] == 740
