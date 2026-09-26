"""Unit tests for disruption module in realtime_trains_api."""
from datetime import datetime, timezone, timedelta
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
import aiohttp
import pytest

from custom_components.realtime_trains_api.disruption import (
    clean_html_to_text,
    extract_element_text,
    is_safe_url,
    parse_incident_datetime,
    is_incident_active,
    incident_matches_crs,
    is_explicit_current_closure,
    is_explicit_current_engineering_work,
    compute_service_status,
    DarwinLdbClient,
    KnowledgeBaseClient,
    DisruptionManager,
    KB_AUTH_URL,
    KB_INCIDENTS_URL,
    UK_TZ,
)
from custom_components.realtime_trains_api.const import (
    CONF_START,
    CONF_END,
    SERVICE_STATUS_NORMAL,
    SERVICE_STATUS_DELAYED,
    SERVICE_STATUS_DISRUPTED,
    SERVICE_STATUS_ENGINEERING_WORK,
    SERVICE_STATUS_STATION_CLOSED,
    SERVICE_STATUS_NO_DEPARTURES,
    KB_STATUS_NOT_CONFIGURED,
    KB_STATUS_PENDING,
    KB_STATUS_CONNECTED,
    KB_STATUS_AUTHENTICATION_FAILED,
    KB_STATUS_FEED_ERROR,
    KB_STATUS_REQUEST_ERROR,
    KB_STATUS_INVALID_RESPONSE,
    ATTR_KB_CONNECTION_STATUS,
    ATTR_KB_LAST_SUCCESSFUL_CHECK,
)


def test_clean_html_to_text():
    assert clean_html_to_text(None) == ""
    assert clean_html_to_text("") == ""
    assert clean_html_to_text("<p>Hello <b>World</b></p>") == "Hello World"
    assert clean_html_to_text("Line 1<br/>Line 2") == "Line 1 Line 2"
    assert clean_html_to_text("<div>Para 1</div><div>Para 2</div>") == "Para 1 Para 2"
    assert clean_html_to_text("Points &amp; signal failure &lt;warning&gt;") == "Points & signal failure <warning>"
    assert clean_html_to_text("<p>More info at <a href='http://nre.co.uk'>NRE</a>.</p>") == "More info at NRE."
    assert clean_html_to_text("Non-breaking&nbsp;space&nbsp;test") == "Non-breaking space test"


def test_extract_element_text():
    elem_simple = ET.fromstring("<Description>Track repairs ongoing</Description>")
    assert extract_element_text(elem_simple) == "Track repairs ongoing"

    elem_html = ET.fromstring("<Description><p>First line</p><p>Second line</p></Description>")
    assert extract_element_text(elem_html) == "First line Second line"

    elem_escaped = ET.fromstring("<Description>&lt;p&gt;Signalling issue&lt;/p&gt;</Description>")
    assert extract_element_text(elem_escaped) == "Signalling issue"

    elem_empty = ET.fromstring("<Description></Description>")
    assert extract_element_text(elem_empty) == ""


def test_is_safe_url():
    assert is_safe_url("https://www.nationalrail.co.uk/disruptions/1") is True
    assert is_safe_url("http://example.com/info") is True
    assert is_safe_url("javascript:alert(1)") is False
    assert is_safe_url("ftp://example.com") is False
    assert is_safe_url("data:text/html,<html>") is False
    assert is_safe_url("http://") is False
    assert is_safe_url("https://") is False
    assert is_safe_url(None) is False
    assert is_safe_url("") is False


def test_is_explicit_current_closure():
    assert is_explicit_current_closure("London Victoria station is closed") is True
    assert is_explicit_current_closure("Station closed due to flooding") is True
    assert is_explicit_current_closure("Station is temporarily closed for repairs") is True
    assert is_explicit_current_closure("Due to the closure of the station, no trains will call") is True
    assert is_explicit_current_closure("Closed to passengers until further notice") is True

    # Future or non-immediate closure notices must NOT count as current closure
    assert is_explicit_current_closure("Lewisham station will be closed on Saturday 15 October") is False
    assert is_explicit_current_closure("Station will be closed next weekend for engineering work") is False
    assert is_explicit_current_closure("Advance notice: Victoria station closed on 28 October") is False
    assert is_explicit_current_closure("Station is scheduled to be closed from 23:00") is False
    assert is_explicit_current_closure("Planned closure of station taking place next Sunday") is False
    assert is_explicit_current_closure("Trains are running normally with no disruptions") is False
    assert is_explicit_current_closure("") is False


def test_compute_service_status_empty_board():
    # Empty board with no disruptions yields no_departures, never station_closed
    status = compute_service_status([], [], [])
    assert status == SERVICE_STATUS_NO_DEPARTURES

    # Empty board with planned engineering work yields engineering_work
    planned_disruption = [
        {"id": "1", "title": "Track renewal", "is_planned": True, "summary": "Buses replace trains"}
    ]
    assert compute_service_status([], planned_disruption, []) == SERVICE_STATUS_ENGINEERING_WORK

    # Empty board with explicit CURRENT station closed message yields station_closed
    assert compute_service_status([], [], ["Station closed due to flooding"]) == SERVICE_STATUS_STATION_CLOSED

    # Empty board with text about FUTURE closure does NOT claim station_closed
    assert compute_service_status([], [], ["Advance notice: Station will be closed next Sunday"]) == SERVICE_STATUS_NO_DEPARTURES
    assert compute_service_status([], [], ["Lewisham station will be closed this weekend"]) == SERVICE_STATUS_NO_DEPARTURES

    # Empty board with unplanned disruption yields disrupted
    unplanned = [
        {"id": "2", "title": "Signal failure", "is_planned": False, "summary": "Lines blocked"}
    ]
    assert compute_service_status([], unplanned, []) == SERVICE_STATUS_DISRUPTED


def test_compute_service_status_trains_running():
    train_normal = {"status": "on_time", "delay_minutes": 0, "is_cancelled": False}
    assert compute_service_status([train_normal], [], []) == SERVICE_STATUS_NORMAL

    # Train delayed
    train_delayed = {"status": "delayed", "delay_minutes": 6, "is_cancelled": False}
    assert compute_service_status([train_delayed], [], []) == SERVICE_STATUS_DELAYED

    # Train cancelled
    train_cancelled = {"status": "cancelled", "delay_minutes": None, "is_cancelled": True}
    assert compute_service_status([train_cancelled], [], []) == SERVICE_STATUS_DISRUPTED

    # Train with major delay >= 15 min
    train_major_delay = {"status": "delayed", "delay_minutes": 20, "is_cancelled": False}
    assert compute_service_status([train_major_delay], [], []) == SERVICE_STATUS_DISRUPTED

    # Trains on time but planned engineering work noted
    planned = [{"id": "1", "title": "Late night work", "is_planned": True, "summary": "After 23:00"}]
    assert compute_service_status([train_normal], planned, []) == SERVICE_STATUS_ENGINEERING_WORK

    # Station closed notice overrides running trains
    assert compute_service_status([train_normal], [], ["London Victoria station is closed"]) == SERVICE_STATUS_STATION_CLOSED

    # Future closure notice does NOT mark running trains as station closed
    assert compute_service_status([train_normal], [], ["Advance notice: Victoria station will be closed this weekend"]) == SERVICE_STATUS_NORMAL


def test_validity_period_filtering():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UK_TZ)

    # 1. Currently active incident
    active_inc = {
        "validity_periods": [
            (datetime(2026, 9, 26, 10, 0, tzinfo=UK_TZ), datetime(2026, 9, 26, 14, 0, tzinfo=UK_TZ))
        ]
    }
    assert is_incident_active(active_inc, now) is True

    # 2. Future incident
    future_inc = {
        "validity_periods": [
            (datetime(2026, 9, 26, 14, 0, tzinfo=UK_TZ), datetime(2026, 9, 26, 18, 0, tzinfo=UK_TZ))
        ]
    }
    assert is_incident_active(future_inc, now) is False

    # 3. Expired incident
    expired_inc = {
        "validity_periods": [
            (datetime(2026, 9, 26, 8, 0, tzinfo=UK_TZ), datetime(2026, 9, 26, 10, 0, tzinfo=UK_TZ))
        ]
    }
    assert is_incident_active(expired_inc, now) is False

    # 4. Open-ended start (missing start time)
    open_start_inc = {
        "validity_periods": [
            (None, datetime(2026, 9, 26, 14, 0, tzinfo=UK_TZ))
        ]
    }
    assert is_incident_active(open_start_inc, now) is True

    # 5. Open-ended end (ongoing with no end time)
    open_end_inc = {
        "validity_periods": [
            (datetime(2026, 9, 26, 10, 0, tzinfo=UK_TZ), None)
        ]
    }
    assert is_incident_active(open_end_inc, now) is True

    # 6. Missing bounds (no validity period specified at all)
    no_bounds_inc = {"validity_periods": []}
    assert is_incident_active(no_bounds_inc, now) is True

    # 7. Multiple validity periods: one expired, one currently active
    multi_period_inc = {
        "validity_periods": [
            (datetime(2026, 9, 25, 8, 0, tzinfo=UK_TZ), datetime(2026, 9, 25, 12, 0, tzinfo=UK_TZ)),
            (datetime(2026, 9, 26, 11, 0, tzinfo=UK_TZ), datetime(2026, 9, 26, 15, 0, tzinfo=UK_TZ)),
        ]
    }
    assert is_incident_active(multi_period_inc, now) is True

    # 8. Timezone-naive handling converts gracefully without raising TypeError
    naive_inc = {
        "validity_periods": [
            (datetime(2026, 9, 26, 10, 0), datetime(2026, 9, 26, 14, 0))
        ]
    }
    assert is_incident_active(naive_inc, now) is True


def test_crs_matching_and_relevance():
    # Structured CRS code in affects_stations
    inc_structured = {
        "affects_stations": {"VIC", "LBG"},
        "routes_affected": "Southeastern mainline services",
    }
    assert incident_matches_crs(inc_structured, "VIC") is True
    assert incident_matches_crs(inc_structured, "LBG") is True
    assert incident_matches_crs(inc_structured, "CLJ") is False

    # Bounded uppercase token in routes_affected
    inc_token = {
        "affects_stations": set(),
        "routes_affected": "Services to/from VIC and GTW delayed",
    }
    assert incident_matches_crs(inc_token, "VIC") is True
    assert incident_matches_crs(inc_token, "GTW") is True

    # AVOID SUBSTRING FALSE POSITIVES
    inc_false_positives = {
        "affects_stations": set(),
        "routes_affected": "Maintenance on Southeastern services between Leeds and York; ticket barrier faults reported",
    }
    # "VIC" should NOT match "services"
    assert incident_matches_crs(inc_false_positives, "VIC") is False
    # "LEE" should NOT match "Leeds"
    assert incident_matches_crs(inc_false_positives, "LEE") is False
    # "BAR" should NOT match "barrier"
    assert incident_matches_crs(inc_false_positives, "BAR") is False
    # "CAN" should NOT match the word "can"
    assert incident_matches_crs(inc_false_positives, "CAN") is False


@pytest.mark.asyncio
async def test_darwin_client_no_token():
    session = MagicMock()
    client = DarwinLdbClient(session, token=None)
    messages = await client.fetch_station_messages("VIC")
    assert messages == []
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_darwin_client_parsing():
    sample_soap_response = b"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetDepartureBoardResponse xmlns="http://thalesgroup.com/RTTI/2017-10-01/ldb/">
      <GetStationBoardResult xmlns:lt="http://thalesgroup.com/RTTI/2012-01-13/ldb/types">
        <lt:nrccMessages>
          <lt:message>A fault with the signalling system between Lewisham and London Bridge.&lt;br/&gt;More details at &lt;a href="http://nre.co.uk"&gt;National Rail&lt;/a&gt;.</lt:message>
        </lt:nrccMessages>
      </GetStationBoardResult>
    </GetDepartureBoardResponse>
  </soap:Body>
</soap:Envelope>"""

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.read.return_value = sample_soap_response

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_ctx

    client = DarwinLdbClient(session, token="dummy-darwin-token")
    messages = await client.fetch_station_messages("LEW")

    assert len(messages) == 1
    assert "A fault with the signalling system" in messages[0]
    assert "More details at National Rail." in messages[0]


@pytest.mark.asyncio
async def test_knowledgebase_client_no_credentials():
    session = MagicMock()
    client = KnowledgeBaseClient(session, username=None, password=None)
    assert client.status == KB_STATUS_NOT_CONFIGURED
    assert await client.fetch_incidents() == []
    assert client.status == KB_STATUS_NOT_CONFIGURED
    session.post.assert_not_called()
    session.get.assert_not_called()

    client_empty = KnowledgeBaseClient(session, username="", password="")
    assert client_empty.status == KB_STATUS_NOT_CONFIGURED
    assert await client_empty.fetch_incidents() == []
    assert client_empty.status == KB_STATUS_NOT_CONFIGURED
    session.post.assert_not_called()
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_knowledgebase_client_auth_and_caching():
    sample_kb_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
  <PtIncident id="INC001">
    <IncidentNumber>INC001</IncidentNumber>
    <Summary>Disruption between Lewisham and London Bridge</Summary>
    <Description>&lt;p&gt;Signalling problem causing delays.&lt;/p&gt;</Description>
    <Planned>false</Planned>
    <ClearedIncident>false</ClearedIncident>
    <Affects>
      <RoutesAffected>Southeastern services via Lewisham</RoutesAffected>
      <Stations>
        <Station>
          <CrsCode>LEW</CrsCode>
          <StationName>Lewisham</StationName>
        </Station>
      </Stations>
    </Affects>
    <AlternativeTransport>
      <AlternativeTravelText>Tickets accepted on London Buses.</AlternativeTravelText>
    </AlternativeTransport>
    <InfoLinks>
      <InfoLink>
        <Uri>https://www.nationalrail.co.uk/disruptions/inc001</Uri>
      </InfoLink>
    </InfoLinks>
  </PtIncident>
</Incidents>"""

    # Auth response
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"Content-Type": "application/json"}
    mock_auth_resp.text.return_value = '{"token": "test-auth-token-123"}'

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    # Incidents feed response
    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 200
    mock_feed_resp.read.return_value = sample_kb_xml

    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx
    session.get.return_value = mock_feed_ctx

    client = KnowledgeBaseClient(session, username="myuser", password="mypassword")

    # 1. First call: authenticates via POST form-urlencoded and gets feed
    assert client.status == KB_STATUS_PENDING
    incidents = await client.fetch_incidents()
    assert client.status == KB_STATUS_CONNECTED
    assert client.last_successful_check is not None
    assert len(incidents) == 1
    assert incidents[0]["id"] == "INC001"
    assert incidents[0]["title"] == "Disruption between Lewisham and London Bridge"

    # Verify POST /authenticate arguments
    session.post.assert_called_once_with(
        KB_AUTH_URL,
        data={"username": "myuser", "password": "mypassword"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    # Verify GET /api/staticfeeds/5.0/incidents header
    session.get.assert_called_once_with(
        KB_INCIDENTS_URL,
        headers={"X-Auth-Token": "test-auth-token-123"},
    )

    # 2. Second call: uses cached token, does NOT post to /authenticate again
    await client.fetch_incidents()
    assert session.post.call_count == 1
    assert session.get.call_count == 2


@pytest.mark.asyncio
async def test_knowledgebase_client_401_refresh():
    sample_kb_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
  <PtIncident id="INC001">
    <Summary>Test Incident</Summary>
    <Planned>false</Planned>
  </PtIncident>
</Incidents>"""

    # Auth mock
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"Content-Type": "application/json"}
    mock_auth_resp.text.return_value = '{"token": "refreshed-token-999"}'

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    # First GET returns 401, second GET returns 200
    mock_401_resp = AsyncMock()
    mock_401_resp.status = 401

    mock_401_ctx = MagicMock()
    mock_401_ctx.__aenter__ = AsyncMock(return_value=mock_401_resp)
    mock_401_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_200_resp = AsyncMock()
    mock_200_resp.status = 200
    mock_200_resp.read.return_value = sample_kb_xml

    mock_200_ctx = MagicMock()
    mock_200_ctx.__aenter__ = AsyncMock(return_value=mock_200_resp)
    mock_200_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx
    session.get.side_effect = [mock_401_ctx, mock_200_ctx]

    client = KnowledgeBaseClient(session, username="myuser", password="mypassword")
    # Pre-populate with an expired cached token
    client._auth_token = "old-expired-token"

    incidents = await client.fetch_incidents()
    assert len(incidents) == 1
    # Verify client refreshed token and retried
    session.post.assert_called_once()
    assert session.get.call_count == 2
    assert client._auth_token == "refreshed-token-999"


@pytest.mark.asyncio
async def test_knowledgebase_client_auth_failure_graceful():
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 401
    mock_auth_resp.text.return_value = "Invalid credentials"

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx

    client = KnowledgeBaseClient(session, username="baduser", password="badpassword")
    assert client.status == KB_STATUS_PENDING
    incidents = await client.fetch_incidents()
    # Must return empty list gracefully without throwing
    assert incidents == []
    assert client.status == KB_STATUS_AUTHENTICATION_FAILED


@pytest.mark.asyncio
async def test_knowledgebase_client_feed_failure_graceful():
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": "good-token"}
    mock_auth_resp.text.return_value = ""

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 500

    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx
    session.get.return_value = mock_feed_ctx

    client = KnowledgeBaseClient(session, username="myuser", password="mypassword")
    assert client.status == KB_STATUS_PENDING
    incidents = await client.fetch_incidents()
    assert incidents == []
    assert client.status == KB_STATUS_FEED_ERROR


def test_knowledgebase_parse_pt_incident_structure():
    sample_structure_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
  <PtIncidentStructure id="NRE_INC_500">
    <IncidentNumber>500</IncidentNumber>
    <Header>Major disruption at London Waterloo</Header>
    <IncidentDescription>&lt;p&gt;Signal failure outside Waterloo.&lt;/p&gt;</IncidentDescription>
    <PlannedIncident>false</PlannedIncident>
    <ClearedIncident>false</ClearedIncident>
    <ValidityPeriods>
      <ValidityPeriod>
        <StartTime>2026-09-26T06:00:00+01:00</StartTime>
        <EndTime>2026-09-26T22:00:00+01:00</EndTime>
      </ValidityPeriod>
    </ValidityPeriods>
    <Affects>
      <RoutesAffected>South Western Railway services to WAT</RoutesAffected>
      <Stations>
        <Station>
          <CrsCode>WAT</CrsCode>
          <StationName>London Waterloo</StationName>
        </Station>
        <Station>
          <CrsCode>CLJ</CrsCode>
          <StationName>Clapham Junction</StationName>
        </Station>
      </Stations>
    </Affects>
    <AlternativeTransport>
      <AlternativeTravelText>South Western Railway passengers may use London Underground.</AlternativeTravelText>
    </AlternativeTransport>
    <TicketAcceptance>
      <TicketAcceptanceText>Tickets accepted on Elizabeth Line.</TicketAcceptanceText>
    </TicketAcceptance>
    <CustomURL>https://www.nationalrail.co.uk/incidents/500</CustomURL>
  </PtIncidentStructure>
  <PtIncidentStructure id="NRE_INC_501">
    <IncidentNumber>501</IncidentNumber>
    <Header>Resolved Issue</Header>
    <ClearedIncident>true</ClearedIncident>
  </PtIncidentStructure>
</Incidents>"""

    client = KnowledgeBaseClient(MagicMock())
    incidents = client._parse_incidents(sample_structure_xml)

    # 501 is cleared, so only 500 should be parsed
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["id"] == "NRE_INC_500"
    assert inc["title"] == "Major disruption at London Waterloo"
    assert inc["summary"] == "Signal failure outside Waterloo."
    assert inc["is_planned"] is False
    assert inc["affects_stations"] == {"WAT", "CLJ"}
    assert "South Western Railway passengers may use London Underground." in inc["alternative_travel"]
    assert "Tickets accepted on Elizabeth Line." in inc["alternative_travel"]
    assert inc["url"] == "https://www.nationalrail.co.uk/incidents/500"
    assert len(inc["validity_periods"]) == 1


@pytest.mark.asyncio
async def test_disruption_manager_caching_and_filtering():
    mock_darwin = AsyncMock()
    mock_darwin.fetch_station_messages.return_value = ["Test announcement"]

    now = datetime(2026, 9, 26, 12, 0, tzinfo=UK_TZ)

    mock_kb = AsyncMock()
    mock_kb.fetch_incidents.return_value = [
        # Active incident at VIC
        {
            "id": "INC100",
            "title": "Engineering Work at Victoria",
            "is_planned": True,
            "summary": "Engineering work is taking place",
            "validity_periods": [
                (datetime(2026, 9, 26, 8, 0, tzinfo=UK_TZ), datetime(2026, 9, 26, 20, 0, tzinfo=UK_TZ))
            ],
            "affects_stations": {"VIC"},
            "routes_affected": "",
            "alternative_travel": "Replacement bus from Victoria",
            "url": "https://www.nationalrail.co.uk",
        },
        # Expired incident at VIC (should be filtered out by validity period)
        {
            "id": "INC101",
            "title": "Past Closure at Victoria",
            "is_planned": True,
            "summary": "Station closed yesterday",
            "validity_periods": [
                (datetime(2026, 9, 25, 8, 0, tzinfo=UK_TZ), datetime(2026, 9, 25, 20, 0, tzinfo=UK_TZ))
            ],
            "affects_stations": {"VIC"},
            "routes_affected": "",
            "alternative_travel": None,
            "url": None,
        },
        # Future incident at VIC (should be filtered out by validity period)
        {
            "id": "INC102",
            "title": "Future Closure at Victoria",
            "is_planned": True,
            "summary": "Station will be closed next month",
            "validity_periods": [
                (datetime(2026, 10, 26, 8, 0, tzinfo=UK_TZ), datetime(2026, 10, 26, 20, 0, tzinfo=UK_TZ))
            ],
            "affects_stations": {"VIC"},
            "routes_affected": "",
            "alternative_travel": None,
            "url": None,
        },
        # Unrelated station MAN (should be filtered out by station correlation)
        {
            "id": "INC200",
            "title": "Disruption at Manchester",
            "is_planned": False,
            "summary": "Unrelated disruption",
            "validity_periods": [],
            "affects_stations": {"MAN"},
            "routes_affected": "Services to Manchester",
            "alternative_travel": None,
            "url": None,
        },
    ]

    manager = DisruptionManager(mock_darwin, mock_kb, cache_ttl_seconds=60)

    # First call: fetches from clients and filters
    status, messages, disruptions = await manager.get_disruptions_for_query(
        origin="VIC",
        destination="CLJ",
        next_trains=[],
        now=now,
    )

    assert messages == ["Test announcement"]
    # Only INC100 is active and affects VIC
    assert len(disruptions) == 1
    assert disruptions[0]["id"] == "INC100"
    assert disruptions[0]["alternative_travel"] == "Replacement bus from Victoria"
    # Empty board + planned work = engineering_work
    assert status == SERVICE_STATUS_ENGINEERING_WORK

    # Second call within TTL: uses cache, clients not called again
    await manager.get_disruptions_for_query(
        origin="VIC",
        destination="CLJ",
        next_trains=[],
        now=now,
    )
    # Both origin (VIC) and destination (CLJ) messages are fetched and cached
    assert mock_darwin.fetch_station_messages.call_count == 2
    assert mock_kb.fetch_incidents.call_count == 1


@pytest.mark.asyncio
async def test_disruption_manager_destination_notices_and_false_positives():
    mock_darwin = AsyncMock()
    mock_darwin.fetch_station_messages.return_value = []

    now = datetime(2026, 9, 26, 12, 0, tzinfo=UK_TZ)

    mock_kb = AsyncMock()
    mock_kb.fetch_incidents.return_value = [
        # Disruption specifically affecting destination CLJ
        {
            "id": "INC_CLJ",
            "title": "Platform repair at Clapham Junction",
            "is_planned": False,
            "summary": "Platform 3 closed at Clapham Junction",
            "validity_periods": [],
            "affects_stations": {"CLJ"},
            "routes_affected": "",
            "alternative_travel": None,
            "url": None,
        },
        # Disruption whose routes contain "services" - must NOT falsely match origin "VIC"
        {
            "id": "INC_UNRELATED",
            "title": "Track repairs",
            "is_planned": False,
            "summary": "Services delayed",
            "validity_periods": [],
            "affects_stations": {"CDF"},
            "routes_affected": "Great Western Railway services via Cardiff",
            "alternative_travel": None,
            "url": None,
        },
    ]

    manager = DisruptionManager(mock_darwin, mock_kb, cache_ttl_seconds=60)

    # 1. Query with destination CLJ: INC_CLJ must be included as destination notice
    status, _, disruptions = await manager.get_disruptions_for_query(
        origin="VIC",
        destination="CLJ",
        next_trains=[{"status": "on_time", "delay_minutes": 0, "is_cancelled": False}],
        now=now,
    )
    assert len(disruptions) == 1
    assert disruptions[0]["id"] == "INC_CLJ"
    # Unplanned disruption active -> disrupted
    assert status == SERVICE_STATUS_DISRUPTED

    # 2. Query WITHOUT destination (station-wide departure board for VIC)
    manager._station_messages_cache.clear()
    manager._kb_incidents_cache = None
    status_station_wide, _, disruptions_station_wide = await manager.get_disruptions_for_query(
        origin="VIC",
        destination=None,
        next_trains=[{"status": "on_time", "delay_minutes": 0, "is_cancelled": False}],
        now=now,
    )
    # CLJ disruption must not be included for station-wide VIC query, and INC_UNRELATED must not match "VIC"
    assert disruptions_station_wide == []
    assert status_station_wide == SERVICE_STATUS_NORMAL


@pytest.mark.asyncio
async def test_knowledgebase_client_auth_formats():
    # 1. Header token
    session = MagicMock()
    mock_auth_resp_hdr = AsyncMock()
    mock_auth_resp_hdr.status = 200
    mock_auth_resp_hdr.headers = {"X-Auth-Token": "token-from-header"}
    mock_auth_resp_hdr.text.return_value = ""

    mock_auth_ctx_hdr = MagicMock()
    mock_auth_ctx_hdr.__aenter__ = AsyncMock(return_value=mock_auth_resp_hdr)
    mock_auth_ctx_hdr.__aexit__ = AsyncMock(return_value=None)

    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 200
    mock_feed_resp.read.return_value = b"<Incidents></Incidents>"

    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    session.post.return_value = mock_auth_ctx_hdr
    session.get.return_value = mock_feed_ctx

    client_hdr = KnowledgeBaseClient(session, username="user", password="secret_password_1")
    await client_hdr.fetch_incidents()
    assert client_hdr._auth_token == "token-from-header"

    # 2. XML body token
    mock_auth_resp_xml = AsyncMock()
    mock_auth_resp_xml.status = 200
    mock_auth_resp_xml.headers = {"Content-Type": "text/xml"}
    mock_auth_resp_xml.text.return_value = "<token>token-from-xml</token>"

    mock_auth_ctx_xml = MagicMock()
    mock_auth_ctx_xml.__aenter__ = AsyncMock(return_value=mock_auth_resp_xml)
    mock_auth_ctx_xml.__aexit__ = AsyncMock(return_value=None)

    session.post.return_value = mock_auth_ctx_xml
    client_xml = KnowledgeBaseClient(session, username="user", password="secret_password_2")
    await client_xml.fetch_incidents()
    assert client_xml._auth_token == "token-from-xml"

    # 3. Plain text token
    mock_auth_resp_txt = AsyncMock()
    mock_auth_resp_txt.status = 200
    mock_auth_resp_txt.headers = {"Content-Type": "text/plain"}
    mock_auth_resp_txt.text.return_value = "token-from-plain-text"

    mock_auth_ctx_txt = MagicMock()
    mock_auth_ctx_txt.__aenter__ = AsyncMock(return_value=mock_auth_resp_txt)
    mock_auth_ctx_txt.__aexit__ = AsyncMock(return_value=None)

    session.post.return_value = mock_auth_ctx_txt
    client_txt = KnowledgeBaseClient(session, username="user", password="secret_password_3")
    await client_txt.fetch_incidents()
    assert client_txt._auth_token == "token-from-plain-text"


@pytest.mark.asyncio
async def test_knowledgebase_no_credential_logging(caplog):
    caplog.set_level(logging.DEBUG)
    session = MagicMock()

    # Simulate auth failure
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 401
    mock_auth_resp.text.return_value = "Unauthorized"

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    session.post.return_value = mock_auth_ctx

    super_secret_password = "SuperSecretPassword123!"
    super_secret_user = "MySuperSecretUser"
    client = KnowledgeBaseClient(session, username=super_secret_user, password=super_secret_password)
    await client.fetch_incidents()

    # Verify neither the username nor password appears anywhere in captured log text
    assert super_secret_password not in caplog.text
    assert super_secret_user not in caplog.text


def test_knowledgebase_parse_pt_incident_direct_validity_and_stoppointref():
    xml_data = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
  <PtIncident id="INC_DIRECT">
    <Summary>Direct tags test</Summary>
    <StartTime>2026-09-26T07:00:00Z</StartTime>
    <EndTime>2026-09-26T21:00:00Z</EndTime>
    <Affects>
      <StopPointRef>VIC</StopPointRef>
      <StopPointRef>LBG</StopPointRef>
    </Affects>
    <CustomURL>https://www.nationalrail.co.uk/incidents/direct</CustomURL>
  </PtIncident>
</Incidents>"""

    client = KnowledgeBaseClient(MagicMock())
    incidents = client._parse_incidents(xml_data)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc["id"] == "INC_DIRECT"
    assert "VIC" in inc["affects_stations"]
    assert "LBG" in inc["affects_stations"]
    assert len(inc["validity_periods"]) == 1
    assert inc["url"] == "https://www.nationalrail.co.uk/incidents/direct"


def test_parse_incident_datetime_formats():
    # ISO with Z
    dt_z = parse_incident_datetime("2026-09-26T12:00:00Z")
    assert dt_z is not None
    assert dt_z.tzinfo is not None

    # ISO with offset
    dt_off = parse_incident_datetime("2026-09-26T12:00:00+01:00")
    assert dt_off is not None

    # Date with space
    dt_space = parse_incident_datetime("2026-09-26 12:00:00")
    assert dt_space is not None

    # Date only
    dt_date = parse_incident_datetime("2026-09-26")
    assert dt_date is not None

    # Invalid / empty
    assert parse_incident_datetime("") is None
    assert parse_incident_datetime(None) is None
    assert parse_incident_datetime("not-a-date") is None


def test_is_incident_active_string_fallbacks():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UK_TZ)

    # Dictionary with start_time and end_time strings rather than validity_periods
    inc_active = {
        "start_time": "2026-09-26T08:00:00Z",
        "end_time": "2026-09-26T18:00:00Z",
    }
    assert is_incident_active(inc_active, now) is True

    inc_expired = {
        "start_time": "2026-09-25T08:00:00Z",
        "end_time": "2026-09-25T18:00:00Z",
    }
    assert is_incident_active(inc_expired, now) is False


def test_incident_matches_crs_station_names():
    inc = {
        "affects_stations": set(),
        "routes_affected": "All Southeastern services between London Victoria and Dover Priory",
    }
    # Matches distinctive station name
    assert incident_matches_crs(inc, "VIC", station_names=["London Victoria"]) is True
    # Unrelated station name does not match
    assert incident_matches_crs(inc, "MAN", station_names=["Manchester Piccadilly"]) is False


def test_is_explicit_current_engineering_work():
    # Active current engineering work / replacement bus patterns
    assert is_explicit_current_engineering_work("Buses replace trains between Lewisham and London Bridge due to engineering work") is True
    assert is_explicit_current_engineering_work("A rail replacement bus service is in operation between Victoria and Bromley South.") is True
    assert is_explicit_current_engineering_work("Engineering work is taking place between London Bridge and Dartford") is True
    assert is_explicit_current_engineering_work("Track renewal work is taking place; replacement buses operate") is True

    # Future engineering work notices must NOT count as current active engineering work
    assert is_explicit_current_engineering_work("Advance notice: Engineering work will take place next weekend") is False
    assert is_explicit_current_engineering_work("Buses will replace trains next Sunday due to engineering work") is False
    assert is_explicit_current_engineering_work("Engineering work is planned for Saturday 15 October") is False
    assert is_explicit_current_engineering_work("Replacement buses will operate from next Monday") is False

    # Vague or unrelated messages must NOT trigger engineering work
    assert is_explicit_current_engineering_work("Please mind the gap between the train and the platform edge.") is False
    assert is_explicit_current_engineering_work("Due to a broken down train, services are delayed.") is False
    assert is_explicit_current_engineering_work("Maintenance works scheduled to begin at 23:30 tonight") is False
    assert is_explicit_current_engineering_work("") is False
    assert is_explicit_current_engineering_work(None) is False


def test_compute_service_status_darwin_engineering_work_no_kb():
    # 1. Active engineering works in Darwin NRCC message without any KB credentials/disruptions
    darwin_msg_active = ["Buses replace trains between Lewisham and London Bridge due to engineering work."]
    status_active = compute_service_status(
        next_trains=[],
        disruptions=[],
        station_messages=darwin_msg_active,
    )
    assert status_active == SERVICE_STATUS_ENGINEERING_WORK

    # 2. Future engineering work notice in Darwin message does NOT trigger engineering_work on empty board
    darwin_msg_future = ["Advance notice: Engineering work will take place between Victoria and Clapham Junction this weekend."]
    status_future = compute_service_status(
        next_trains=[],
        disruptions=[],
        station_messages=darwin_msg_future,
    )
    assert status_future == SERVICE_STATUS_NO_DEPARTURES

    # 3. Vague message in Darwin message yields no_departures on empty board
    darwin_msg_vague = ["Please take care on platforms during wet weather."]
    status_vague = compute_service_status(
        next_trains=[],
        disruptions=[],
        station_messages=darwin_msg_vague,
    )
    assert status_vague == SERVICE_STATUS_NO_DEPARTURES

    # 4. Running trains with active Darwin engineering notice
    train_normal = {"status": "on_time", "delay_minutes": 0, "is_cancelled": False}
    status_trains_running = compute_service_status(
        next_trains=[train_normal],
        disruptions=[],
        station_messages=darwin_msg_active,
    )
    assert status_trains_running == SERVICE_STATUS_ENGINEERING_WORK


@pytest.mark.asyncio
async def test_destination_closure_does_not_close_origin():
    # When destination is closed, origin is NOT marked station_closed; service is disrupted
    mock_darwin = AsyncMock()
    # Origin VIC has no messages; destination CLJ is closed
    mock_darwin.fetch_station_messages.side_effect = lambda crs: (
        ["Clapham Junction station is closed due to flooding."] if crs == "CLJ" else []
    )

    manager = DisruptionManager(darwin_client=mock_darwin, kb_client=None)

    status, messages, disruptions = await manager.get_disruptions_for_query(
        origin="VIC",
        destination="CLJ",
        next_trains=[],
    )

    # Destination is closed, so travel on this query is disrupted, NOT station_closed
    assert status == SERVICE_STATUS_DISRUPTED
    assert "Clapham Junction station is closed due to flooding." in messages
    assert disruptions == []


@pytest.mark.asyncio
async def test_origin_closure_marks_origin_closed():
    # When origin is explicitly closed, status IS station_closed
    mock_darwin = AsyncMock()
    mock_darwin.fetch_station_messages.side_effect = lambda crs: (
        ["London Victoria station is closed due to a power outage."] if crs == "VIC" else []
    )

    manager = DisruptionManager(darwin_client=mock_darwin, kb_client=None)

    status, messages, disruptions = await manager.get_disruptions_for_query(
        origin="VIC",
        destination="CLJ",
        next_trains=[],
    )

    assert status == SERVICE_STATUS_STATION_CLOSED
    assert "London Victoria station is closed due to a power outage." in messages


@pytest.mark.asyncio
async def test_destination_engineering_work_and_facility_filtering():
    # Correlated destination engineering notice
    mock_darwin = AsyncMock()
    mock_darwin.fetch_station_messages.side_effect = lambda crs: (
        ["Buses replace trains between Victoria and Clapham Junction due to engineering work."]
        if crs == "CLJ"
        else []
    )

    manager = DisruptionManager(darwin_client=mock_darwin, kb_client=None)

    status, messages, _ = await manager.get_disruptions_for_query(
        origin="VIC",
        destination="CLJ",
        next_trains=[],
    )

    assert status == SERVICE_STATUS_ENGINEERING_WORK
    assert len(messages) == 1

    # Unrelated destination facility message should not correlate
    mock_darwin_unrelated = AsyncMock()
    mock_darwin_unrelated.fetch_station_messages.side_effect = lambda crs: (
        ["Platform 1 lift out of order at Clapham Junction."] if crs == "CLJ" else []
    )

    manager_unrelated = DisruptionManager(darwin_client=mock_darwin_unrelated, kb_client=None)

    train_normal = {"status": "on_time", "delay_minutes": 0, "is_cancelled": False}
    status_unrelated, messages_unrelated, _ = await manager_unrelated.get_disruptions_for_query(
        origin="VIC",
        destination="CLJ",
        next_trains=[train_normal],
    )

    assert status_unrelated == SERVICE_STATUS_NORMAL
    assert messages_unrelated == []


def test_compute_service_status_origin_station_closure():
    assert (
        compute_service_status(
            [],
            [],
            ["Blackheath station is closed due to engineering work"],
            origin="BKH",
            destination="LEW",
        )
        == SERVICE_STATUS_STATION_CLOSED
    )


def test_compute_service_status_destination_closure_disrupted():
    assert (
        compute_service_status(
            [],
            [],
            [],
            origin="BKH",
            destination="LEW",
            destination_messages=["Lewisham station is closed due to flooding"],
        )
        == SERVICE_STATUS_DISRUPTED
    )


# =========================================================================
# Focused tests for Knowledgebase (KB) API Connection Observability
# =========================================================================

@pytest.mark.asyncio
async def test_kb_connection_status_connected():
    """Verify status is 'connected' and last_successful_check is set after successful fetch."""
    sample_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
  <PtIncident id="INC101">
    <Summary>Disruption between Victoria and Clapham Junction</Summary>
    <Affects><Station><CrsCode>VIC</CrsCode></Station></Affects>
  </PtIncident>
</Incidents>"""

    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": "valid-token-123"}
    mock_auth_resp.text.return_value = ""

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 200
    mock_feed_resp.read.return_value = sample_xml

    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx
    session.get.return_value = mock_feed_ctx

    client = KnowledgeBaseClient(session, username="test_user", password="test_password")
    assert client.status == KB_STATUS_PENDING
    assert client.last_successful_check is None

    manager = DisruptionManager(kb_client=client)
    assert manager.kb_connection_status == KB_STATUS_PENDING
    assert manager.kb_last_successful_check is None

    incidents = await manager.get_kb_incidents()
    assert len(incidents) == 1
    assert incidents[0]["id"] == "INC101"

    # Status transitions to connected
    assert client.status == KB_STATUS_CONNECTED
    assert client.last_successful_check is not None
    assert isinstance(client.last_successful_check, datetime)
    assert client.last_successful_check.tzinfo is not None

    # Manager exposes identical status and check timestamp
    assert manager.kb_connection_status == KB_STATUS_CONNECTED
    assert manager.kb_last_successful_check == client.last_successful_check


@pytest.mark.asyncio
async def test_kb_connection_status_no_active_incidents():
    """Verify status is 'connected' when feed returns 200 with 0 incidents."""
    sample_empty_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
</Incidents>"""

    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": "valid-token-empty"}
    mock_auth_resp.text.return_value = ""

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 200
    mock_feed_resp.read.return_value = sample_empty_xml

    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx
    session.get.return_value = mock_feed_ctx

    client = KnowledgeBaseClient(session, username="test_user", password="test_password")
    incidents = await client.fetch_incidents()

    assert incidents == []
    # Status is connected because auth succeeded AND 200 XML incidents response parsed cleanly
    assert client.status == KB_STATUS_CONNECTED
    assert client.last_successful_check is not None


@pytest.mark.asyncio
async def test_kb_connection_status_bad_portal_password():
    """Verify bad portal password (401/403) sets status to 'authentication_failed'."""
    # Test HTTP 401
    mock_401_resp = AsyncMock()
    mock_401_resp.status = 401
    mock_401_resp.text.return_value = "Unauthorized"

    mock_401_ctx = MagicMock()
    mock_401_ctx.__aenter__ = AsyncMock(return_value=mock_401_resp)
    mock_401_ctx.__aexit__ = AsyncMock(return_value=None)

    session_401 = MagicMock()
    session_401.post.return_value = mock_401_ctx

    client_401 = KnowledgeBaseClient(session_401, username="bad_user", password="bad_password")
    manager_401 = DisruptionManager(kb_client=client_401)

    incidents_401 = await manager_401.get_kb_incidents()
    assert incidents_401 == []
    assert client_401.status == KB_STATUS_AUTHENTICATION_FAILED
    assert manager_401.kb_connection_status == KB_STATUS_AUTHENTICATION_FAILED
    assert manager_401.kb_last_successful_check is None

    # Test HTTP 403
    mock_403_resp = AsyncMock()
    mock_403_resp.status = 403
    mock_403_resp.text.return_value = "Forbidden"

    mock_403_ctx = MagicMock()
    mock_403_ctx.__aenter__ = AsyncMock(return_value=mock_403_resp)
    mock_403_ctx.__aexit__ = AsyncMock(return_value=None)

    session_403 = MagicMock()
    session_403.post.return_value = mock_403_ctx

    client_403 = KnowledgeBaseClient(session_403, username="user", password="forbidden_password")
    incidents_403 = await client_403.fetch_incidents()
    assert incidents_403 == []
    assert client_403.status == KB_STATUS_AUTHENTICATION_FAILED


@pytest.mark.asyncio
async def test_kb_connection_status_auth_200_json_error(caplog):
    """Verify HTTP 200 response with documented invalid credentials JSON body sets authentication_failed."""
    caplog.set_level(logging.DEBUG)

    sensitive_user = "secret_kb_username_xyz"
    sensitive_pass = "secret_kb_password_123"

    # National Rail Data Portal documents: {"error":"Invalid username/password"} with HTTP 200
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"Content-Type": "application/json"}
    mock_auth_resp.text.return_value = '{"error":"Invalid username/password"}'

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx

    client = KnowledgeBaseClient(session, username=sensitive_user, password=sensitive_pass)
    manager = DisruptionManager(kb_client=client)

    incidents = await manager.get_kb_incidents()
    assert incidents == []
    assert client.status == KB_STATUS_AUTHENTICATION_FAILED
    assert manager.kb_connection_status == KB_STATUS_AUTHENTICATION_FAILED
    assert manager.kb_last_successful_check is None

    # Verify logging: records transition to authentication_failed without leaking credentials or raw response text
    log_text = caplog.text
    assert "pending -> authentication_failed (invalid credentials)" in log_text
    assert sensitive_user not in log_text
    assert sensitive_pass not in log_text
    assert "Invalid username/password" not in log_text


@pytest.mark.asyncio
async def test_kb_connection_status_network_failure():
    """Verify network connection errors set status to 'request_error'."""
    session = MagicMock()
    session.post.side_effect = aiohttp.ClientConnectorError(
        connection_key=MagicMock(), os_error=OSError("Connection refused")
    )

    client = KnowledgeBaseClient(session, username="test_user", password="test_password")
    manager = DisruptionManager(kb_client=client)

    incidents = await manager.get_kb_incidents()
    assert incidents == []
    assert client.status == KB_STATUS_REQUEST_ERROR
    assert manager.kb_connection_status == KB_STATUS_REQUEST_ERROR
    assert manager.kb_last_successful_check is None


@pytest.mark.asyncio
async def test_kb_connection_status_feed_failure():
    """Verify feed server errors (HTTP 500, 502, 503) set status to 'feed_error'."""
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": "valid-token"}
    mock_auth_resp.text.return_value = ""

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 503

    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx
    session.get.return_value = mock_feed_ctx

    client = KnowledgeBaseClient(session, username="test_user", password="test_password")
    manager = DisruptionManager(kb_client=client)

    incidents = await manager.get_kb_incidents()
    assert incidents == []
    assert client.status == KB_STATUS_FEED_ERROR
    assert manager.kb_connection_status == KB_STATUS_FEED_ERROR


@pytest.mark.asyncio
async def test_kb_connection_status_invalid_response():
    """Verify unparseable responses set status to 'invalid_response'."""
    # 1. Feed returns 200 with invalid XML syntax
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": "valid-token"}
    mock_auth_resp.text.return_value = ""

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_feed_bad_xml = AsyncMock()
    mock_feed_bad_xml.status = 200
    mock_feed_bad_xml.read.return_value = b"<Incidents><unclosed tag"

    mock_feed_bad_ctx = MagicMock()
    mock_feed_bad_ctx.__aenter__ = AsyncMock(return_value=mock_feed_bad_xml)
    mock_feed_bad_ctx.__aexit__ = AsyncMock(return_value=None)

    session1 = MagicMock()
    session1.post.return_value = mock_auth_ctx
    session1.get.return_value = mock_feed_bad_ctx

    client1 = KnowledgeBaseClient(session1, username="user", password="password")
    assert await client1.fetch_incidents() == []
    assert client1.status == KB_STATUS_INVALID_RESPONSE

    # 2. Feed returns 200 with HTML error page
    mock_feed_html = AsyncMock()
    mock_feed_html.status = 200
    mock_feed_html.read.return_value = b"<html><body>502 Bad Gateway</body></html>"

    mock_feed_html_ctx = MagicMock()
    mock_feed_html_ctx.__aenter__ = AsyncMock(return_value=mock_feed_html)
    mock_feed_html_ctx.__aexit__ = AsyncMock(return_value=None)

    session2 = MagicMock()
    session2.post.return_value = mock_auth_ctx
    session2.get.return_value = mock_feed_html_ctx

    client2 = KnowledgeBaseClient(session2, username="user", password="password")
    assert await client2.fetch_incidents() == []
    assert client2.status == KB_STATUS_INVALID_RESPONSE

    # 3. Auth returns 200 with no token in body or header
    mock_auth_no_token = AsyncMock()
    mock_auth_no_token.status = 200
    mock_auth_no_token.headers = {"Content-Type": "application/json"}
    mock_auth_no_token.text.return_value = '{"status": "ok"}'

    mock_auth_no_token_ctx = MagicMock()
    mock_auth_no_token_ctx.__aenter__ = AsyncMock(return_value=mock_auth_no_token)
    mock_auth_no_token_ctx.__aexit__ = AsyncMock(return_value=None)

    session3 = MagicMock()
    session3.post.return_value = mock_auth_no_token_ctx

    client3 = KnowledgeBaseClient(session3, username="user", password="password")
    assert await client3.fetch_incidents() == []
    assert client3.status == KB_STATUS_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_kb_connection_status_missing_credentials():
    """Verify missing or partial credentials result in 'not_configured' status."""
    session = MagicMock()

    # Neither username nor password
    c1 = KnowledgeBaseClient(session, username=None, password=None)
    assert c1.status == KB_STATUS_NOT_CONFIGURED
    assert await c1.fetch_incidents() == []
    assert c1.status == KB_STATUS_NOT_CONFIGURED

    # Only username provided
    c2 = KnowledgeBaseClient(session, username="only_user", password=None)
    assert c2.status == KB_STATUS_NOT_CONFIGURED
    assert await c2.fetch_incidents() == []
    assert c2.status == KB_STATUS_NOT_CONFIGURED

    # Only password provided
    c3 = KnowledgeBaseClient(session, username=None, password="only_password")
    assert c3.status == KB_STATUS_NOT_CONFIGURED
    assert await c3.fetch_incidents() == []
    assert c3.status == KB_STATUS_NOT_CONFIGURED

    # DisruptionManager with no kb_client
    dm_none = DisruptionManager(kb_client=None)
    assert dm_none.kb_connection_status == KB_STATUS_NOT_CONFIGURED
    assert dm_none.kb_last_successful_check is None
    assert await dm_none.get_kb_incidents() == []

    # DisruptionManager with unconfigured kb_client
    dm_unconfigured = DisruptionManager(kb_client=c1)
    assert dm_unconfigured.kb_connection_status == KB_STATUS_NOT_CONFIGURED
    assert dm_unconfigured.kb_last_successful_check is None

    session.post.assert_not_called()
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_kb_connection_status_failed_refresh_clears_connected():
    """Ensure a failed refresh immediately changes status from connected and does not serve stale data."""
    sample_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
  <PtIncident id="INC999">
    <Summary>Incident 999</Summary>
    <Affects><Station><CrsCode>VIC</CrsCode></Station></Affects>
  </PtIncident>
</Incidents>"""

    # Auth returns good token
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": "token-1"}
    mock_auth_resp.text.return_value = ""

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    # First feed response: 200 OK
    mock_feed_200 = AsyncMock()
    mock_feed_200.status = 200
    mock_feed_200.read.return_value = sample_xml

    mock_feed_200_ctx = MagicMock()
    mock_feed_200_ctx.__aenter__ = AsyncMock(return_value=mock_feed_200)
    mock_feed_200_ctx.__aexit__ = AsyncMock(return_value=None)

    # Second feed response: 500 Internal Server Error
    mock_feed_500 = AsyncMock()
    mock_feed_500.status = 500

    mock_feed_500_ctx = MagicMock()
    mock_feed_500_ctx.__aenter__ = AsyncMock(return_value=mock_feed_500)
    mock_feed_500_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx
    session.get.side_effect = [mock_feed_200_ctx, mock_feed_500_ctx]

    client = KnowledgeBaseClient(session, username="myuser", password="mypassword")
    manager = DisruptionManager(kb_client=client, cache_ttl_seconds=60)

    # 1. First fetch: succeeds
    incidents1 = await manager.get_kb_incidents()
    assert len(incidents1) == 1
    assert incidents1[0]["id"] == "INC999"
    assert manager.kb_connection_status == KB_STATUS_CONNECTED
    first_successful_check = manager.kb_last_successful_check
    assert first_successful_check is not None

    # Fast forward time beyond cache TTL (60s)
    cached_epoch = manager._kb_incidents_cache[0]
    with patch("time.time", return_value=cached_epoch + 100):
        # 2. Second fetch: refresh fails with HTTP 500
        incidents2 = await manager.get_kb_incidents()
        assert incidents2 == []
        # MUST NOT leave stale connected status!
        assert manager.kb_connection_status == KB_STATUS_FEED_ERROR
        assert client.status == KB_STATUS_FEED_ERROR
        # Cache must be invalidated
        assert manager._kb_incidents_cache is None
        # Last successful check timestamp is preserved from previous success
        assert manager.kb_last_successful_check == first_successful_check


@pytest.mark.asyncio
async def test_kb_safe_status_transition_logging_and_redaction(caplog):
    """Verify that credentials, tokens, response bodies, and raw exceptions are NEVER logged,

    and verify status transitions are logged safely without spamming on repeated checks.
    """
    caplog.set_level(logging.DEBUG)

    sensitive_user = "user_secret_identifier_98765"
    sensitive_pass = "pass_SuperSecret_Password_XYZ!#$%"
    sensitive_token = "token_RawSecretBearerToken_456789"
    sensitive_err_msg = "Internal proxy failed connecting to https://secret-backend.internal/auth"

    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": sensitive_token}
    mock_auth_resp.text.return_value = '{"token": "' + sensitive_token + '"}'

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 200
    mock_feed_resp.read.return_value = b"<Incidents></Incidents>"

    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx
    session.get.return_value = mock_feed_ctx

    client = KnowledgeBaseClient(session, username=sensitive_user, password=sensitive_pass)

    # 1. Initial attempt: pending -> connected
    await client.fetch_incidents()
    assert client.status == KB_STATUS_CONNECTED

    log_text_first = caplog.text
    # Log must record transition
    assert "pending -> connected" in log_text_first
    # Credentials and tokens MUST NOT appear
    assert sensitive_user not in log_text_first
    assert sensitive_pass not in log_text_first
    assert sensitive_token not in log_text_first

    # 2. Repeated poll while still connected: status unchanged, NO log spam
    caplog.clear()
    await client.fetch_incidents()
    assert client.status == KB_STATUS_CONNECTED
    # Because status did not transition, no new status log should be emitted
    assert "Knowledgebase connection status" not in caplog.text

    # 3. Simulate failure with sensitive exception text
    caplog.clear()
    session.get.side_effect = aiohttp.ClientConnectorError(
        connection_key=MagicMock(),
        os_error=OSError(sensitive_err_msg),
    )
    # Clear cached token to force failure or GET failure
    await client.fetch_incidents()
    assert client.status == KB_STATUS_REQUEST_ERROR

    log_text_err = caplog.text
    # Log records safe transition without raw exception message
    assert "connected -> request_error" in log_text_err
    assert "ClientConnectorError" in log_text_err
    # Raw exception text MUST NOT be logged
    assert sensitive_err_msg not in log_text_err
    assert sensitive_user not in log_text_err
    assert sensitive_pass not in log_text_err
    assert sensitive_token not in log_text_err

    # 4. Another failed poll while still request_error: NO log spam
    caplog.clear()
    await client.fetch_incidents()
    assert client.status == KB_STATUS_REQUEST_ERROR
    assert "Knowledgebase connection status" not in caplog.text


@pytest.mark.asyncio
async def test_station_sensor_kb_connection_status_attributes():
    """Verify that RealtimeTrainLiveTrainTimeSensor exposes kb_connection_status and kb_last_successful_check."""
    from custom_components.realtime_trains_api.sensor import RealtimeTrainLiveTrainTimeSensor
    from custom_components.realtime_trains_api.coordinator import RealtimeTrainsUpdateCoordinator
    from custom_components.realtime_trains_api.rtt_api import RealtimeTrainsApiClient

    mock_hass = MagicMock()
    mock_session = MagicMock()
    api_client = RealtimeTrainsApiClient(mock_session, token="tok", refresh_token="ref")
    api_client.fetch_location_services = AsyncMock(return_value={"services": []})

    # Setup Knowledgebase client that connects successfully
    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": "test-token"}
    mock_auth_resp.text.return_value = ""

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 200
    mock_feed_resp.read.return_value = b"<Incidents></Incidents>"

    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    kb_session = MagicMock()
    kb_session.post.return_value = mock_auth_ctx
    kb_session.get.return_value = mock_feed_ctx

    kb_client = KnowledgeBaseClient(kb_session, username="kb_user", password="kb_pass")
    disruption_manager = DisruptionManager(kb_client=kb_client)

    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=mock_hass,
        logger=MagicMock(),
        name="test_coord",
        update_interval=timedelta(seconds=60),
        api=api_client,
        queries=[{"origin": "VIC", "destination": "CLJ"}],
        disruption_manager=disruption_manager,
    )

    sensor = RealtimeTrainLiveTrainTimeSensor(
        coordinator=coordinator,
        sensor_name="VIC to CLJ",
        query_key="VIC_CLJ_all_0",
        journey_start="VIC",
        journey_end="CLJ",
        timeoffset=timedelta(),
        platforms_of_interest=[],
        entry_id="entry_123",
        query_index=0,
    )

    # 1. Before coordinator refresh: reflects pending status
    attrs_before = sensor.extra_state_attributes
    assert attrs_before[ATTR_KB_CONNECTION_STATUS] == KB_STATUS_PENDING
    assert attrs_before[ATTR_KB_LAST_SUCCESSFUL_CHECK] is None

    # 2. After coordinator update: reflects connected status and timestamp
    await coordinator._async_update_data()
    attrs_after = sensor.extra_state_attributes
    assert attrs_after[ATTR_KB_CONNECTION_STATUS] == KB_STATUS_CONNECTED
    assert attrs_after[ATTR_KB_LAST_SUCCESSFUL_CHECK] is not None
    # Validate ISO 8601 string format
    assert "T" in attrs_after[ATTR_KB_LAST_SUCCESSFUL_CHECK]

    # 3. When KB credentials missing: reflects not_configured
    dm_no_cred = DisruptionManager(kb_client=None)
    coord_no_cred = RealtimeTrainsUpdateCoordinator(
        hass=mock_hass,
        logger=MagicMock(),
        name="test_no_cred",
        update_interval=timedelta(seconds=60),
        api=api_client,
        queries=[{"origin": "VIC", "destination": "CLJ"}],
        disruption_manager=dm_no_cred,
    )
    sensor_no_cred = RealtimeTrainLiveTrainTimeSensor(
        coordinator=coord_no_cred,
        sensor_name="VIC to CLJ",
        query_key="VIC_CLJ_all_0",
        journey_start="VIC",
        journey_end="CLJ",
        timeoffset=timedelta(),
        platforms_of_interest=[],
        entry_id="entry_no_cred",
        query_index=0,
    )
    assert sensor_no_cred.extra_state_attributes[ATTR_KB_CONNECTION_STATUS] == KB_STATUS_NOT_CONFIGURED
    await coord_no_cred._async_update_data()
    assert sensor_no_cred.extra_state_attributes[ATTR_KB_CONNECTION_STATUS] == KB_STATUS_NOT_CONFIGURED


@pytest.mark.asyncio
async def test_blackheath_planned_closure_empty_board_regression():
    """Focused regression test for Blackheath station closure with empty departures.

    Reproduces the live acceptance scenario on 2026-09-26 21:34 Europe/London where:
    - Origin is BKH (Blackheath) with no departures (board is empty).
    - KB feed contains the official Incidents V5 notice without StationEffects:
      Title: 'No Southeastern services via Lewisham on Saturday 26 and Sunday 27 September'
      RoutesAffected: 'All routes via Lewisham'
      Description: 'The following stations will be closed all weekend and will only be served by accessible buses: Lewisham, Blackheath, Kidbrooke, Eltham, Falconwood, Welling, Bexleyheath, Barnehurst.'
    - Official National Rail URL: https://www.nationalrail.co.uk/engineering-works/lewisham-26-sep-20260926/
    - Asserts service_status is station_closed and disruption details are surfaced.
    - Asserts that an unrelated station (MAN) does NOT flag the incident.
    """
    now = datetime(2026, 9, 26, 21, 34, tzinfo=UK_TZ)

    kb_xml_payload = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
  <PtIncident id="NRE_LEW_20260926">
    <IncidentNumber>20260926</IncidentNumber>
    <Header>No Southeastern services via Lewisham on Saturday 26 and Sunday 27 September</Header>
    <IncidentDescription>The following stations will be closed all weekend and will only be served by accessible buses: Lewisham, Blackheath, Kidbrooke, Eltham, Falconwood, Welling, Bexleyheath, Barnehurst.</IncidentDescription>
    <PlannedIncident>true</PlannedIncident>
    <ClearedIncident>false</ClearedIncident>
    <ValidityPeriods>
      <ValidityPeriod>
        <StartTime>2026-09-26T00:00:00+01:00</StartTime>
        <EndTime>2026-09-27T23:59:59+01:00</EndTime>
      </ValidityPeriod>
    </ValidityPeriods>
    <Affects>
      <RoutesAffected>All routes via Lewisham</RoutesAffected>
    </Affects>
    <AlternativeTransport>
      <AlternativeTravelText>Replacement buses operate between Lewisham and Charlton via Blackheath.</AlternativeTravelText>
    </AlternativeTransport>
    <CustomURL>https://www.nationalrail.co.uk/engineering-works/lewisham-26-sep-20260926/</CustomURL>
  </PtIncident>
</Incidents>"""

    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": "valid_token"}
    mock_auth_resp.text.return_value = '{"token": "valid_token"}'

    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 200
    mock_feed_resp.read.return_value = kb_xml_payload

    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx
    session.get.return_value = mock_feed_ctx

    kb_client = KnowledgeBaseClient(session, username="user", password="pwd")
    manager = DisruptionManager(kb_client=kb_client)

    # 1. Query for Blackheath (BKH) station-wide with NO departures
    status, station_messages, disruptions = await manager.get_disruptions_for_query(
        origin="BKH",
        destination=None,
        next_trains=[],
        now=now,
    )

    # Service status must correctly identify station_closed
    assert status == SERVICE_STATUS_STATION_CLOSED
    assert len(disruptions) == 1
    d = disruptions[0]
    assert d["id"] == "NRE_LEW_20260926"
    assert "No Southeastern services via Lewisham" in d["title"]
    assert d["is_planned"] is True
    assert "Blackheath" in d["summary"]
    assert "Replacement buses" in d["alternative_travel"]
    assert d["url"] == "https://www.nationalrail.co.uk/engineering-works/lewisham-26-sep-20260926/"

    # 2. Avoid broad matching: an unrelated station (MAN) must NOT match this disruption
    status_unrelated, _, disruptions_unrelated = await manager.get_disruptions_for_query(
        origin="MAN",
        destination=None,
        next_trains=[],
        now=now,
    )
    assert status_unrelated == SERVICE_STATUS_NO_DEPARTURES
    assert disruptions_unrelated == []


@pytest.mark.asyncio
async def test_blackheath_engineering_work_unproven_closure():
    """Verify that when engineering work is active but closure cannot be proven, status is engineering_work."""
    now = datetime(2026, 9, 26, 21, 34, tzinfo=UK_TZ)

    kb_xml_unproven = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
  <PtIncident id="NRE_LEW_ENG">
    <IncidentNumber>20260927</IncidentNumber>
    <Header>Engineering work in the Lewisham area</Header>
    <IncidentDescription>Planned track maintenance taking place between Lewisham and Dartford via Blackheath. Replacement buses are running.</IncidentDescription>
    <PlannedIncident>true</PlannedIncident>
    <ClearedIncident>false</ClearedIncident>
    <ValidityPeriods>
      <ValidityPeriod>
        <StartTime>2026-09-26T00:00:00+01:00</StartTime>
        <EndTime>2026-09-27T23:59:59+01:00</EndTime>
      </ValidityPeriod>
    </ValidityPeriods>
    <Affects>
      <RoutesAffected>Southeastern services via Blackheath</RoutesAffected>
    </Affects>
    <AlternativeTransport>
      <AlternativeTravelText>Replacement buses are running.</AlternativeTravelText>
    </AlternativeTransport>
  </PtIncident>
</Incidents>"""

    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": "valid_token"}
    mock_auth_resp.text.return_value = '{"token": "valid_token"}'
    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 200
    mock_feed_resp.read.return_value = kb_xml_unproven
    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post.return_value = mock_auth_ctx
    session.get.return_value = mock_feed_ctx

    kb_client = KnowledgeBaseClient(session, username="user", password="pwd")
    manager = DisruptionManager(kb_client=kb_client)

    status, _, disruptions = await manager.get_disruptions_for_query(
        origin="BKH",
        destination=None,
        next_trains=[],
        now=now,
    )

    # Empty board with planned disruption where closure is not proven -> engineering_work
    assert status == SERVICE_STATUS_ENGINEERING_WORK
    assert len(disruptions) == 1
    assert disruptions[0]["id"] == "NRE_LEW_ENG"
    assert disruptions[0]["is_planned"] is True


def test_date_only_validity_period_active_at_night():
    """Verify that date-only validity periods (YYYY-MM-DD) remain active throughout the final day."""
    now = datetime(2026, 9, 26, 21, 34, tzinfo=UK_TZ)

    xml_date_only = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
  <PtIncident id="NRE_DATE_ONLY">
    <Summary>Weekend Track Maintenance</Summary>
    <PlannedIncident>true</PlannedIncident>
    <ValidityPeriods>
      <ValidityPeriod>
        <StartDate>2026-09-26</StartDate>
        <EndDate>2026-09-26</EndDate>
      </ValidityPeriod>
    </ValidityPeriods>
  </PtIncident>
</Incidents>"""

    client = KnowledgeBaseClient(MagicMock())
    incidents = client._parse_incidents(xml_date_only)
    assert len(incidents) == 1
    inc = incidents[0]
    # On 2026-09-26 at 21:34, an incident valid for 2026-09-26 must still be active
    assert is_incident_active(inc, now) is True


@pytest.mark.asyncio
async def test_coordinator_blackheath_station_closed_contract_integration():
    """Verify full coordinator flow for BKH station closed with empty departures."""
    from custom_components.realtime_trains_api.sensor import RealtimeTrainLiveTrainTimeSensor
    from custom_components.realtime_trains_api.coordinator import RealtimeTrainsUpdateCoordinator
    from custom_components.realtime_trains_api.rtt_api import RealtimeTrainsApiClient

    now = datetime(2026, 9, 26, 21, 34, tzinfo=UK_TZ)

    mock_hass = MagicMock()
    mock_rtt_session = MagicMock()
    api_client = RealtimeTrainsApiClient(mock_rtt_session, token="rtt_tok")
    # RTT returns location data for Blackheath but 0 services
    api_client.fetch_location_services = AsyncMock(return_value={
        "location": {"name": "Blackheath", "crs": "BKH"},
        "services": [],
    })

    kb_xml_payload = b"""<?xml version="1.0" encoding="utf-8"?>
<Incidents xmlns="http://nationalrail.co.uk/xml/incident">
  <PtIncident id="NRE_LEW_BKH">
    <IncidentNumber>999</IncidentNumber>
    <Header>No Southeastern services via Lewisham on Saturday 26 and Sunday 27 September</Header>
    <IncidentDescription>The following stations will be closed all weekend and will only be served by accessible buses: Lewisham, Blackheath, Kidbrooke, Eltham, Falconwood, Welling, Bexleyheath, Barnehurst.</IncidentDescription>
    <PlannedIncident>true</PlannedIncident>
    <ClearedIncident>false</ClearedIncident>
    <ValidityPeriods>
      <ValidityPeriod>
        <StartTime>2026-09-26T00:00:00+01:00</StartTime>
        <EndTime>2026-09-27T23:59:59+01:00</EndTime>
      </ValidityPeriod>
    </ValidityPeriods>
    <Affects>
      <RoutesAffected>All routes via Lewisham</RoutesAffected>
    </Affects>
    <AlternativeTransport>
      <AlternativeTravelText>Replacement buses will run between Lewisham and Charlton via Blackheath.</AlternativeTravelText>
    </AlternativeTransport>
    <CustomURL>https://www.nationalrail.co.uk/engineering-works/lewisham-26-sep-20260926/</CustomURL>
  </PtIncident>
</Incidents>"""

    mock_auth_resp = AsyncMock()
    mock_auth_resp.status = 200
    mock_auth_resp.headers = {"X-Auth-Token": "valid_token"}
    mock_auth_resp.text.return_value = '{"token": "valid_token"}'
    mock_auth_ctx = MagicMock()
    mock_auth_ctx.__aenter__ = AsyncMock(return_value=mock_auth_resp)
    mock_auth_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_feed_resp = AsyncMock()
    mock_feed_resp.status = 200
    mock_feed_resp.read.return_value = kb_xml_payload
    mock_feed_ctx = MagicMock()
    mock_feed_ctx.__aenter__ = AsyncMock(return_value=mock_feed_resp)
    mock_feed_ctx.__aexit__ = AsyncMock(return_value=None)

    kb_session = MagicMock()
    kb_session.post.return_value = mock_auth_ctx
    kb_session.get.return_value = mock_feed_ctx

    kb_client = KnowledgeBaseClient(kb_session, username="u", password="p")
    manager = DisruptionManager(kb_client=kb_client)

    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=mock_hass,
        logger=MagicMock(),
        name="test_bkh_coord",
        update_interval=timedelta(seconds=60),
        api=api_client,
        queries=[{CONF_START: "BKH", CONF_END: None}],
        disruption_manager=manager,
    )

    sensor = RealtimeTrainLiveTrainTimeSensor(
        coordinator=coordinator,
        sensor_name="Blackheath Station",
        query_key="BKH_all_all_0",
        journey_start="BKH",
        journey_end=None,
        timeoffset=timedelta(),
        platforms_of_interest=[],
        entry_id="bkh_entry",
        query_index=0,
    )

    with patch("custom_components.realtime_trains_api.coordinator.dt_util.now", return_value=now):
        coordinator.data = await coordinator._async_update_data()

    # Sensor state must be None (no departures), service_status must be station_closed
    assert sensor.native_value is None
    attrs = sensor.extra_state_attributes
    assert attrs["service_status"] == SERVICE_STATUS_STATION_CLOSED
    assert attrs["next_trains"] == []
    assert len(attrs["disruptions"]) == 1
    disruption = attrs["disruptions"][0]
    assert disruption["id"] == "NRE_LEW_BKH"
    assert disruption["is_planned"] is True
    assert disruption["url"] == "https://www.nationalrail.co.uk/engineering-works/lewisham-26-sep-20260926/"
    assert "Replacement buses" in disruption["alternative_travel"]
