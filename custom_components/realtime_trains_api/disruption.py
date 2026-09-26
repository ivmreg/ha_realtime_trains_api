"""Disruption and incident management for realtime_trains_api."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import aiohttp

from .const import (
    DEFAULT_DISRUPTION_CACHE_SECONDS,
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
)

_LOGGER = logging.getLogger(__name__)
UK_TZ = ZoneInfo("Europe/London")

OPENLDBWS_URL = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb11.asmx"
KB_AUTH_URL = "https://opendata.nationalrail.co.uk/authenticate"
KB_INCIDENTS_URL = "https://opendata.nationalrail.co.uk/api/staticfeeds/5.0/incidents"

STATION_CLOSED_PATTERNS = [
    "station is closed",
    "station is currently closed",
    "station is temporarily closed",
    "station remaining closed",
    "station has closed",
    "closed to passengers",
    "closure of the station",
    "closure of station",
    "station closed",
]

FUTURE_CLOSURE_INDICATORS = [
    "will be closed",
    "will close",
    "due to close",
    "due to be closed",
    "planned closure",
    "planned to be closed",
    "scheduled to be closed",
    "scheduled to close",
    "advance notice",
    "advance warning",
    "future closure",
    "is to be closed",
    "closing at",
    "closing from",
    "to be closed from",
    "to be closed on",
]

CURRENT_ENGINEERING_PATTERNS = [
    "engineering work is taking place",
    "engineering works are taking place",
    "engineering work taking place",
    "engineering works taking place",
    "due to engineering work",
    "due to planned engineering work",
    "due to track renewal",
    "track renewal work is taking place",
    "track maintenance is taking place",
    "buses replace trains",
    "buses are replacing trains",
    "rail replacement buses are in operation",
    "rail replacement buses are running",
    "rail replacement bus service is in operation",
    "rail replacement bus service",
    "rail replacement buses operate",
    "rail replacement buses run",
    "rail replacement service is in operation",
    "rail replacement service",
    "bus replacement service",
    "replacement bus service",
    "replacement buses are in operation",
    "replacement buses are running",
    "replacement buses operate",
    "replacement buses run",
]

FUTURE_WORK_INDICATORS = [
    "will take place",
    "will be closed",
    "will close",
    "will be replaced",
    "will be replacing",
    "will operate",
    "will be operating",
    "will be in operation",
    "will affect",
    "will be affected",
    "planned for",
    "planned from",
    "scheduled for",
    "scheduled from",
    "scheduled to",
    "due to take place",
    "advance notice",
    "advance warning",
    "future work",
    "future engineering",
    "next weekend",
    "next saturday",
    "next sunday",
    "next week",
    "from next",
]


def clean_html_to_text(raw_html: str | None) -> str:
    """Normalize HTML to plain readable text."""
    if not raw_html:
        return ""
    # Replace block/line breaks with spaces
    text = re.sub(r"<(?:br|/p|/div|/li|p|div|li)\s*/?>", " ", raw_html, flags=re.IGNORECASE)
    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Unescape HTML entities after stripping tags so encoded text like &lt;tag&gt; is preserved
    text = html.unescape(text)
    # Normalize non-breaking spaces and whitespace
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_element_text(elem: ET.Element) -> str:
    """Extract text or inner HTML from an XML element and normalize to plain text."""
    if len(elem) > 0:
        raw = ET.tostring(elem, encoding="unicode")
        raw = re.sub(r"^<[^>]+>", "", raw)
        raw = re.sub(r"</[^>]+>$", "", raw)
        return clean_html_to_text(raw)
    return clean_html_to_text(elem.text or "")


def is_safe_url(url: str | None) -> bool:
    """Validate that a URL uses http or https protocol only with a valid host."""
    if not url or not isinstance(url, str):
        return False
    clean = url.strip()
    try:
        parsed = urlparse(clean)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def local_tag(elem: ET.Element) -> str:
    """Return the local tag name without XML namespace."""
    tag = elem.tag
    return tag.split("}")[-1] if "}" in tag else tag


def find_child_by_local_tag(elem: ET.Element, name: str) -> ET.Element | None:
    """Find the first direct child matching a local tag name."""
    name_lower = name.lower()
    for child in elem:
        if local_tag(child).lower() == name_lower:
            return child
    return None


def find_first_child_by_local_tags(
    elem: ET.Element, names: tuple[str, ...]
) -> ET.Element | None:
    """Find the first direct child matching any of the local tag names."""
    for name in names:
        child = find_child_by_local_tag(elem, name)
        if child is not None:
            return child
    return None


def find_all_by_local_tag(elem: ET.Element, name: str) -> list[ET.Element]:
    """Find all descendant elements matching a local tag name."""
    name_lower = name.lower()
    results = []
    for item in elem.iter():
        if local_tag(item).lower() == name_lower:
            results.append(item)
    return results


def get_child_text(elem: ET.Element, name: str) -> str | None:
    """Get the text of a direct child element matching a local tag."""
    child = find_child_by_local_tag(elem, name)
    if child is not None and child.text:
        return child.text.strip()
    return None


def parse_xml_robust(xml_content: str | bytes) -> ET.Element:
    """Parse XML string or bytes robustly."""
    if isinstance(xml_content, str):
        xml_content = xml_content.encode("utf-8")
    return ET.fromstring(xml_content)


def parse_incident_datetime(
    val: str | None,
    default_tz: ZoneInfo = UK_TZ,
) -> datetime | None:
    """Parse an incident timestamp robustly into a timezone-aware datetime."""
    if not val or not isinstance(val, str):
        return None
    val = val.strip()
    if not val:
        return None
    # Normalize Z suffix to +00:00
    if val.endswith("Z") or val.endswith("z"):
        val = val[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt)
            return dt.replace(tzinfo=default_tz)
        except Exception:
            continue
    return None


def is_incident_active(incident: dict[str, Any], now: datetime) -> bool:
    """Check if an incident is active at the given timezone-aware time."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UK_TZ)

    validity_periods = incident.get("validity_periods")
    if validity_periods is None:
        start_str = incident.get("start_time")
        end_str = incident.get("end_time")
        if start_str or end_str:
            validity_periods = [(
                parse_incident_datetime(start_str) if isinstance(start_str, str) else start_str,
                parse_incident_datetime(end_str) if isinstance(end_str, str) else end_str,
            )]
        else:
            return True

    if not validity_periods:
        return True

    for start_dt, end_dt in validity_periods:
        if start_dt is not None:
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=UK_TZ)
            if start_dt > now:
                continue
        if end_dt is not None:
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=UK_TZ)
            if end_dt < now:
                continue
        return True

    return False


def incident_matches_crs(
    incident: dict[str, Any],
    crs: str,
    station_names: list[str] | None = None,
) -> bool:
    """Check if an incident affects a station by structured CRS code or bounded token."""
    crs_upper = crs.strip().upper()
    if not crs_upper:
        return False

    affects_stations = incident.get("affects_stations", set())
    if crs_upper in affects_stations:
        return True

    routes = incident.get("routes_affected", "")
    if routes:
        # Bounded token matching on CRS code to prevent cross-station false positives
        if re.search(rf"\b{re.escape(crs_upper)}\b", routes):
            return True

    if station_names and routes:
        for name in station_names:
            clean_name = name.strip()
            if len(clean_name) >= 4:
                if re.search(rf"\b{re.escape(clean_name)}\b", routes, re.IGNORECASE):
                    return True

    return False


def is_explicit_current_closure(text: str) -> bool:
    """Check if text contains explicit reliable evidence of CURRENT station closure."""
    if not text:
        return False
    lower = text.lower()

    # Split into clauses/sentences by punctuation or line breaks
    sentences = re.split(r"[.\n;!]", lower)
    for s in sentences:
        s_clean = s.strip()
        if not s_clean:
            continue
        # Check if this clause contains any current closure pattern
        if any(pat in s_clean for pat in STATION_CLOSED_PATTERNS):
            # If the clause contains a future indicator, it's not a current closure
            if any(fm in s_clean for fm in FUTURE_CLOSURE_INDICATORS):
                continue
            return True

    return False


def is_origin_closure(
    text: str,
    origin: str | None = None,
    destination: str | None = None,
    origin_names: list[str] | None = None,
    dest_names: list[str] | None = None,
) -> bool:
    """Determine if a closure text indicates that the ORIGIN station specifically is closed.

    If destination is provided and the closure text refers solely to the destination
    station, returns False (so origin station is not classified as closed).
    """
    if not is_explicit_current_closure(text):
        return False

    if not destination and not dest_names:
        return True

    lower = text.lower()
    sentences = re.split(r"[.\n;!]", lower)

    dest_tokens: set[str] = set()
    if destination:
        dest_tokens.add(destination.strip().lower())
    if dest_names:
        for d_name in dest_names:
            clean = d_name.strip().lower()
            if len(clean) >= 3:
                dest_tokens.add(clean)

    orig_tokens: set[str] = set()
    if origin:
        orig_tokens.add(origin.strip().lower())
    if origin_names:
        for o_name in origin_names:
            clean = o_name.strip().lower()
            if len(clean) >= 3:
                orig_tokens.add(clean)

    closure_found = False
    for s in sentences:
        s_clean = s.strip()
        if not s_clean:
            continue
        if any(pat in s_clean for pat in STATION_CLOSED_PATTERNS):
            if any(fm in s_clean for fm in FUTURE_CLOSURE_INDICATORS):
                continue
            closure_found = True

            mentions_dest = any(
                re.search(rf"\b{re.escape(tok)}\b", s_clean) for tok in dest_tokens
            )
            mentions_orig = any(
                re.search(rf"\b{re.escape(tok)}\b", s_clean) for tok in orig_tokens
            )

            # If it explicitly names the destination as closed and does NOT name origin
            if mentions_dest and not mentions_orig:
                return False

            # If it names a specific station before 'station ... closed' and does not match origin
            named_station_match = re.search(
                r"\b([a-z\s]+?)\s+station\s+(?:is\s+|currently\s+|temporarily\s+)?closed\b",
                s_clean,
            )
            if named_station_match and orig_tokens:
                named_prefix = named_station_match.group(1).strip()
                if named_prefix and not any(tok in named_prefix for tok in orig_tokens):
                    return False

    return closure_found


def is_explicit_current_engineering_work(text: str) -> bool:
    """Check if text contains explicit reliable evidence of CURRENT engineering work or replacement buses."""
    if not text:
        return False
    lower = text.lower()

    sentences = re.split(r"[.\n;!]", lower)
    for s in sentences:
        s_clean = s.strip()
        if not s_clean:
            continue
        if any(pat in s_clean for pat in CURRENT_ENGINEERING_PATTERNS):
            if any(fm in s_clean for fm in FUTURE_WORK_INDICATORS):
                continue
            return True

    return False


def is_destination_message_relevant(
    msg: str,
    origin: str,
    origin_names: list[str],
    destination: str,
    dest_names: list[str],
) -> bool:
    """Check if a message from the destination station is relevant to the query."""
    if not msg:
        return False
    msg_clean = msg.strip()
    if not msg_clean:
        return False

    # 1. Check if it mentions the origin CRS or origin station name
    if re.search(rf"\b{re.escape(origin.upper())}\b", msg_clean):
        return True
    for name in origin_names:
        if len(name.strip()) >= 4 and re.search(rf"\b{re.escape(name.strip())}\b", msg_clean, re.IGNORECASE):
            return True

    # 2. Check if destination station is currently closed (affects inbound trains)
    if is_explicit_current_closure(msg_clean):
        return True

    # 3. Check if destination station has active engineering work or replacement buses
    if is_explicit_current_engineering_work(msg_clean):
        return True

    # 4. Check if message describes route-wide disruption affecting services
    lower = msg_clean.lower()
    if any(term in lower for term in ("services suspended", "line closed", "all lines blocked", "no trains")):
        return True

    return False


def compute_service_status(
    next_trains: list[dict[str, Any]],
    disruptions: list[dict[str, Any]],
    station_messages: list[str],
    origin: str | None = None,
    destination: str | None = None,
    origin_station_names: list[str] | None = None,
    destination_station_names: list[str] | None = None,
    destination_messages: list[str] | None = None,
) -> str:
    """Compute canonical service_status enum from departures and disruptions.

    Priority order:
    1. station_closed (explicit reliable closure evidence of ORIGIN station)
    2. engineering_work (planned work on route or empty board due to planned work)
    3. disrupted (unplanned incident, cancellations, major delays, destination closed)
    4. delayed (trains delayed)
    5. no_departures (empty board without station closed)
    6. normal (operating smoothly)
    """
    # 1. Process origin-scoped Darwin station_messages first
    for text in station_messages:
        if is_explicit_current_closure(text):
            return SERVICE_STATUS_STATION_CLOSED

    # 2. Process structured disruptions with existing is_origin_closure check
    disruption_texts: list[str] = []
    for d in disruptions:
        if d.get("title"):
            disruption_texts.append(str(d["title"]))
        if d.get("summary"):
            disruption_texts.append(str(d["summary"]))
        if d.get("alternative_travel"):
            disruption_texts.append(str(d["alternative_travel"]))

    has_destination_closed = False

    for text in disruption_texts:
        if is_origin_closure(
            text,
            origin=origin,
            destination=destination,
            origin_names=origin_station_names,
            dest_names=destination_station_names,
        ):
            return SERVICE_STATUS_STATION_CLOSED
        elif is_explicit_current_closure(text):
            has_destination_closed = True

    # 3. Process destination_messages only as disrupted
    for text in (destination_messages or []):
        if is_explicit_current_closure(text):
            has_destination_closed = True

    # 4. Check for planned engineering work (from structured disruptions or Darwin text)
    all_query_texts: list[str] = (
        list(station_messages)
        + list(disruption_texts)
        + list(destination_messages or [])
    )
    has_planned_disruptions = any(d.get("is_planned") for d in disruptions)
    has_planned_messages = any(is_explicit_current_engineering_work(text) for text in all_query_texts)
    has_planned = has_planned_disruptions or has_planned_messages

    has_unplanned_disruptions = any(not d.get("is_planned") for d in disruptions)
    has_unplanned = has_unplanned_disruptions or has_destination_closed

    has_cancelled = any(
        t.get("is_cancelled") or t.get("status") == "cancelled"
        for t in next_trains
    )
    has_major_delay = any(
        (t.get("delay_minutes") or 0) >= 15
        for t in next_trains
    )
    has_delayed = any(
        t.get("status") == "delayed" or (t.get("delay_minutes") or 0) > 1
        for t in next_trains
    )

    # 3. Empty board handling:
    if len(next_trains) == 0:
        if has_planned:
            return SERVICE_STATUS_ENGINEERING_WORK
        if has_unplanned:
            return SERVICE_STATUS_DISRUPTED
        return SERVICE_STATUS_NO_DEPARTURES

    # 4. Trains exist: check for unplanned disruptions, cancellations, or major delays
    if has_unplanned or has_cancelled or has_major_delay:
        return SERVICE_STATUS_DISRUPTED

    # 5. Planned engineering work active
    if has_planned:
        return SERVICE_STATUS_ENGINEERING_WORK

    # 6. Routine delays
    if has_delayed:
        return SERVICE_STATUS_DELAYED

    # 7. Normal
    return SERVICE_STATUS_NORMAL


class DarwinLdbClient:
    """Client for National Rail Darwin OpenLDBWS SOAP API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str | None = None,
        endpoint_url: str = OPENLDBWS_URL,
    ) -> None:
        self.session = session
        self.token = token.strip() if token and token.strip() else None
        self.endpoint_url = endpoint_url

    async def fetch_station_messages(self, crs: str) -> list[str]:
        """Fetch station NRCC messages for a CRS code via OpenLDBWS SOAP."""
        if not self.token or not crs:
            return []

        soap_envelope = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:typ="http://thalesgroup.com/RTTI/2013-11-28/Token/types" '
            'xmlns:ldb="http://thalesgroup.com/RTTI/2017-10-01/ldb/">\n'
            "  <soap:Header>\n"
            "    <typ:AccessToken>\n"
            f"      <typ:TokenValue>{html.escape(self.token)}</typ:TokenValue>\n"
            "    </typ:AccessToken>\n"
            "  </soap:Header>\n"
            "  <soap:Body>\n"
            "    <ldb:GetDepartureBoardRequest>\n"
            "      <ldb:numRows>1</ldb:numRows>\n"
            f"      <ldb:crs>{html.escape(crs.upper())}</ldb:crs>\n"
            "    </ldb:GetDepartureBoardRequest>\n"
            "  </soap:Body>\n"
            "</soap:Envelope>"
        )

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://thalesgroup.com/RTTI/2012-01-13/ldb/GetDepartureBoard",
        }

        try:
            async with self.session.post(
                self.endpoint_url,
                data=soap_envelope.encode("utf-8"),
                headers=headers,
            ) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "Darwin OpenLDBWS returned status %s for station %s",
                        response.status,
                        crs,
                    )
                    return []
                content = await response.read()
                return self._parse_nrcc_messages(content)
        except Exception as err:
            _LOGGER.debug("Error fetching Darwin NRCC messages for %s: %s", crs, err)
            return []

    def _parse_nrcc_messages(self, xml_bytes: bytes) -> list[str]:
        """Parse NRCC messages from Darwin SOAP response XML."""
        try:
            root = parse_xml_robust(xml_bytes)
        except Exception as err:
            _LOGGER.debug("Failed to parse Darwin SOAP XML: %s", err)
            return []

        messages: list[str] = []
        for nrcc in find_all_by_local_tag(root, "nrccmessages"):
            for msg_elem in nrcc:
                if local_tag(msg_elem).lower() == "message":
                    cleaned = extract_element_text(msg_elem)
                    if cleaned and cleaned not in messages:
                        messages.append(cleaned)
        return messages


class KnowledgeBaseClient:
    """Client for National Rail Knowledgebase Incidents XML static feed.

    Tracks connection status across:
    - not_configured: Either username or password credential is absent or blank.
    - pending: Credentials are configured, waiting for first connection attempt.
    - connected: Successfully authenticated and parsed an HTTP 200 incidents feed response.
    - authentication_failed: Credentials rejected by authentication endpoint (HTTP 401/403).
    - feed_error: Incidents feed endpoint returned an HTTP error status (e.g. 500, 502, 503, 404).
    - request_error: Network or connection error (e.g. timeout, connection reset).
    - invalid_response: Response payload could not be parsed or token missing in auth response.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str | None = None,
        password: str | None = None,
        endpoint_url: str = KB_INCIDENTS_URL,
        auth_url: str = KB_AUTH_URL,
    ) -> None:
        self.session = session
        self.username = username.strip() if username and username.strip() else None
        self.password = password.strip() if password and password.strip() else None
        self.endpoint_url = endpoint_url
        self.auth_url = auth_url
        self._auth_token: str | None = None
        self._status: str = (
            KB_STATUS_PENDING
            if (self.username and self.password)
            else KB_STATUS_NOT_CONFIGURED
        )
        self._last_successful_check: datetime | None = None

    @property
    def status(self) -> str:
        """Return the current connection status."""
        return self._status

    @property
    def last_successful_check(self) -> datetime | None:
        """Return the timestamp of the last successful incidents feed check."""
        return self._last_successful_check

    def _set_status(self, new_status: str, detail: str | None = None) -> None:
        """Update connection status and log transitions without exposing secrets."""
        old_status = self._status
        if old_status == new_status:
            return

        self._status = new_status
        if new_status == KB_STATUS_CONNECTED:
            _LOGGER.info("Knowledgebase connection status: %s -> %s", old_status, new_status)
        elif new_status in (
            KB_STATUS_AUTHENTICATION_FAILED,
            KB_STATUS_FEED_ERROR,
            KB_STATUS_REQUEST_ERROR,
            KB_STATUS_INVALID_RESPONSE,
        ):
            if detail:
                _LOGGER.warning(
                    "Knowledgebase connection status: %s -> %s (%s)",
                    old_status,
                    new_status,
                    detail,
                )
            else:
                _LOGGER.warning(
                    "Knowledgebase connection status: %s -> %s",
                    old_status,
                    new_status,
                )
        else:
            _LOGGER.debug(
                "Knowledgebase connection status: %s -> %s",
                old_status,
                new_status,
            )

    async def _authenticate(self) -> str | None:
        """Authenticate via POST form-urlencoded to obtain an auth token."""
        if not self.username or not self.password:
            self._set_status(KB_STATUS_NOT_CONFIGURED)
            return None

        payload = {
            "username": self.username,
            "password": self.password,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            async with self.session.post(
                self.auth_url,
                data=payload,
                headers=headers,
            ) as response:
                if response.status in (401, 403):
                    self._set_status(KB_STATUS_AUTHENTICATION_FAILED, f"HTTP {response.status}")
                    return None
                elif response.status != 200:
                    if response.status < 500:
                        self._set_status(KB_STATUS_AUTHENTICATION_FAILED, f"HTTP {response.status}")
                    else:
                        self._set_status(KB_STATUS_FEED_ERROR, f"HTTP {response.status}")
                    return None

                # 1. Check response header for token
                token_hdr = (
                    response.headers.get("X-Auth-Token")
                    or response.headers.get("token")
                    or response.headers.get("auth-token")
                )
                if token_hdr and token_hdr.strip():
                    self._auth_token = token_hdr.strip()
                    return self._auth_token

                # 2. Check response body
                text = await response.text()
                if not text:
                    self._set_status(KB_STATUS_INVALID_RESPONSE, "Empty auth response")
                    return None

                content_type = response.headers.get("Content-Type", "").lower()
                token: str | None = None

                # JSON response format
                if "json" in content_type or text.strip().startswith("{"):
                    try:
                        data = json.loads(text)
                        if isinstance(data, dict):
                            token = (
                                data.get("token")
                                or data.get("tokenValue")
                                or data.get("authToken")
                                or data.get("id")
                                or data.get("access_token")
                            )
                            if not token:
                                err = (
                                    data.get("error")
                                    or data.get("error_description")
                                    or data.get("message")
                                )
                                if isinstance(err, str):
                                    err_lower = err.strip().lower()
                                    if (
                                        "invalid username" in err_lower
                                        or "invalid password" in err_lower
                                        or "invalid credentials" in err_lower
                                        or "unauthorized" in err_lower
                                    ):
                                        self._set_status(
                                            KB_STATUS_AUTHENTICATION_FAILED,
                                            "invalid credentials",
                                        )
                                        return None
                    except Exception:
                        pass

                # XML response format
                if not token and ("xml" in content_type or text.strip().startswith("<")):
                    try:
                        root = parse_xml_robust(text)
                        token_elem = find_child_by_local_tag(root, "token")
                        if token_elem is not None and token_elem.text:
                            token = token_elem.text.strip()
                        elif root.text and root.text.strip():
                            token = root.text.strip()
                    except Exception:
                        pass

                # Plain text format (only when not JSON or XML)
                is_json = "json" in content_type or text.strip().startswith("{")
                is_xml = "xml" in content_type or text.strip().startswith("<")
                if not token and not is_json and not is_xml:
                    candidate = text.strip().strip('"').strip("'")
                    if candidate and "\n" not in candidate and "<" not in candidate and "{" not in candidate:
                        token = candidate

                if token:
                    self._auth_token = str(token).strip()
                    return self._auth_token

                self._set_status(KB_STATUS_INVALID_RESPONSE, "Token missing in auth response")
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            self._set_status(KB_STATUS_REQUEST_ERROR, type(err).__name__)
            return None
        except Exception as err:
            self._set_status(KB_STATUS_REQUEST_ERROR, type(err).__name__)
            return None

    async def fetch_incidents(self) -> list[dict[str, Any]]:
        """Fetch and parse incidents from Knowledgebase XML feed."""
        if not self.username or not self.password:
            self._set_status(KB_STATUS_NOT_CONFIGURED)
            return []

        # Obtain token (cached or fresh)
        if not self._auth_token:
            token = await self._authenticate()
            if not token:
                return []

        headers = {"X-Auth-Token": self._auth_token}
        try:
            async with self.session.get(
                self.endpoint_url,
                headers=headers,
            ) as response:
                # Handle token expiry / 401 or 403
                if response.status in (401, 403):
                    self._auth_token = None
                    token = await self._authenticate()
                    if not token:
                        return []
                    retry_headers = {"X-Auth-Token": token}
                    try:
                        async with self.session.get(
                            self.endpoint_url,
                            headers=retry_headers,
                        ) as retry_resp:
                            if retry_resp.status in (401, 403):
                                self._set_status(KB_STATUS_AUTHENTICATION_FAILED, f"HTTP {retry_resp.status}")
                                return []
                            if retry_resp.status != 200:
                                self._set_status(KB_STATUS_FEED_ERROR, f"HTTP {retry_resp.status}")
                                return []
                            content = await retry_resp.read()
                            return self._process_feed_content(content)
                    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                        self._set_status(KB_STATUS_REQUEST_ERROR, type(err).__name__)
                        return []
                    except Exception as err:
                        self._set_status(KB_STATUS_REQUEST_ERROR, type(err).__name__)
                        return []

                if response.status != 200:
                    self._set_status(KB_STATUS_FEED_ERROR, f"HTTP {response.status}")
                    return []

                content = await response.read()
                return self._process_feed_content(content)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            self._set_status(KB_STATUS_REQUEST_ERROR, type(err).__name__)
            return []
        except Exception as err:
            self._set_status(KB_STATUS_REQUEST_ERROR, type(err).__name__)
            return []

    def _process_feed_content(self, content: bytes) -> list[dict[str, Any]]:
        """Parse feed XML, update status to connected on success, or invalid_response on parse error."""
        if not content or not content.strip():
            self._set_status(KB_STATUS_INVALID_RESPONSE, "Empty feed response")
            return []

        try:
            incidents = self._parse_incidents(content)
        except Exception:
            self._set_status(KB_STATUS_INVALID_RESPONSE, "XML parse error")
            return []

        self._last_successful_check = datetime.now(timezone.utc)
        self._set_status(KB_STATUS_CONNECTED)
        return incidents

    def _parse_incidents(self, xml_bytes: bytes) -> list[dict[str, Any]]:
        """Parse PtIncident and PtIncidentStructure elements into structured incident objects."""
        root = parse_xml_robust(xml_bytes)

        target_tags = {"ptincident", "ptincidentstructure"}
        root_tag = local_tag(root).lower()

        # Validate that the XML root is an expected incidents element or container
        if root_tag not in {"incidents", "ptincident", "ptincidentstructure"} and not any(
            local_tag(child).lower() in target_tags for child in root
        ):
            raise ValueError(f"Unexpected XML root tag: {root_tag}")

        incidents: list[dict[str, Any]] = []

        if root_tag in target_tags:
            incident = self._parse_single_incident(root)
            if incident:
                incidents.append(incident)
            return incidents

        for elem in root.iter():
            if elem is not root and local_tag(elem).lower() in target_tags:
                incident = self._parse_single_incident(elem)
                if incident:
                    incidents.append(incident)
        return incidents

    def _parse_single_incident(self, elem: ET.Element) -> dict[str, Any] | None:
        """Parse a single PtIncident or PtIncidentStructure element."""
        cleared_text = (
            get_child_text(elem, "clearedincident")
            or get_child_text(elem, "cleared")
            or get_child_text(elem, "iscleared")
            or "false"
        )
        if str(cleared_text).strip().lower() in ("true", "1", "yes"):
            return None

        inc_id = (
            elem.attrib.get("id")
            or get_child_text(elem, "incidentnumber")
            or get_child_text(elem, "id")
            or get_child_text(elem, "incidentid")
            or ""
        ).strip()

        title = (
            get_child_text(elem, "summary")
            or get_child_text(elem, "header")
            or get_child_text(elem, "title")
            or "Rail Disruption"
        )
        title = clean_html_to_text(title) or "Rail Disruption"

        desc_elem = find_first_child_by_local_tags(
            elem, ("description", "incidentdescription", "detail")
        )
        if desc_elem is not None:
            summary = extract_element_text(desc_elem)
        else:
            summary = title

        if not summary:
            summary = title

        planned_text = (
            get_child_text(elem, "planned")
            or get_child_text(elem, "plannedincident")
            or get_child_text(elem, "isplanned")
            or "false"
        )
        is_planned = str(planned_text).strip().lower() in ("true", "1", "yes")

        # Parse validity periods
        validity_periods: list[tuple[datetime | None, datetime | None]] = []
        validity_elements = find_all_by_local_tag(elem, "validityperiod")
        if validity_elements:
            for vp in validity_elements:
                start_str = (
                    get_child_text(vp, "starttime")
                    or get_child_text(vp, "startdatetime")
                    or get_child_text(vp, "startdate")
                    or get_child_text(vp, "from")
                )
                end_str = (
                    get_child_text(vp, "endtime")
                    or get_child_text(vp, "enddatetime")
                    or get_child_text(vp, "enddate")
                    or get_child_text(vp, "to")
                )
                start_dt = parse_incident_datetime(start_str)
                end_dt = parse_incident_datetime(end_str)
                validity_periods.append((start_dt, end_dt))
        else:
            start_str = (
                get_child_text(elem, "starttime")
                or get_child_text(elem, "startdatetime")
                or get_child_text(elem, "startdate")
            )
            end_str = (
                get_child_text(elem, "endtime")
                or get_child_text(elem, "enddatetime")
                or get_child_text(elem, "enddate")
            )
            if start_str or end_str:
                validity_periods.append((
                    parse_incident_datetime(start_str),
                    parse_incident_datetime(end_str),
                ))

        # Affected stations and routes
        affects_stations: set[str] = set()
        routes_affected = ""
        affects_elem = find_child_by_local_tag(elem, "affects")
        if affects_elem is None:
            affects_elem = elem

        routes_elem = find_first_child_by_local_tags(
            affects_elem, ("routesaffected", "routes")
        )
        if routes_elem is not None:
            routes_affected = extract_element_text(routes_elem)
        elif affects_elem is not elem:
            direct_routes = find_first_child_by_local_tags(
                elem, ("routesaffected", "routes")
            )
            if direct_routes is not None:
                routes_affected = extract_element_text(direct_routes)

        for station_elem in find_all_by_local_tag(affects_elem, "station"):
            crs = (
                get_child_text(station_elem, "crscode")
                or get_child_text(station_elem, "crs")
                or get_child_text(station_elem, "stationcode")
                or station_elem.attrib.get("crs")
                or station_elem.attrib.get("crsCode")
            )
            if crs and len(crs.strip()) == 3:
                affects_stations.add(crs.strip().upper())

        for crs_elem in find_all_by_local_tag(affects_elem, "crscode"):
            if crs_elem.text and len(crs_elem.text.strip()) == 3:
                affects_stations.add(crs_elem.text.strip().upper())

        for stop_elem in find_all_by_local_tag(affects_elem, "stoppointref"):
            if stop_elem.text and len(stop_elem.text.strip()) == 3:
                affects_stations.add(stop_elem.text.strip().upper())

        # Alternative travel guidance
        alt_parts: list[str] = []
        search_roots = [elem]
        if affects_elem is not elem:
            search_roots.append(affects_elem)

        for s_root in search_roots:
            for tag_name in (
                "alternativetransport",
                "alternativetravel",
                "ticketacceptance",
                "alternativeservices",
            ):
                for container in find_all_by_local_tag(s_root, tag_name):
                    text = None
                    for child_tag in (
                        "alternativetraveltext",
                        "alternativetransporttext",
                        "ticketacceptancetext",
                        "description",
                        "text",
                    ):
                        child = find_child_by_local_tag(container, child_tag)
                        if child is not None:
                            text = extract_element_text(child)
                            if text:
                                break
                    if not text:
                        text = extract_element_text(container)
                    if text and text not in alt_parts:
                        alt_parts.append(text)

        alt_travel = " ".join(alt_parts).strip() if alt_parts else None

        # Info links / URL
        info_url = None
        for link_container in find_all_by_local_tag(elem, "infolinks"):
            for link in find_all_by_local_tag(link_container, "infolink"):
                uri = (
                    get_child_text(link, "uri")
                    or get_child_text(link, "url")
                    or get_child_text(link, "link")
                    or link.attrib.get("href")
                    or link.attrib.get("uri")
                )
                if uri and is_safe_url(uri):
                    info_url = uri.strip()
                    break
            if info_url:
                break

        if not info_url:
            for tag in ("customurl", "weblink", "url", "uri"):
                candidate_url = get_child_text(elem, tag)
                if candidate_url and is_safe_url(candidate_url):
                    info_url = candidate_url.strip()
                    break

        start_time_str = None
        end_time_str = None
        if validity_periods:
            p0 = validity_periods[0]
            if p0[0] is not None:
                start_time_str = p0[0].isoformat()
            if p0[1] is not None:
                end_time_str = p0[1].isoformat()

        return {
            "id": inc_id,
            "title": title,
            "summary": summary,
            "is_planned": is_planned,
            "validity_periods": validity_periods,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "affects_stations": affects_stations,
            "routes_affected": routes_affected,
            "alternative_travel": alt_travel,
            "url": info_url,
        }


class DisruptionManager:
    """Manages disruption queries, caching (15-30m), and service status calculation."""

    def __init__(
        self,
        darwin_client: DarwinLdbClient | None = None,
        kb_client: KnowledgeBaseClient | None = None,
        cache_ttl_seconds: int = DEFAULT_DISRUPTION_CACHE_SECONDS,
    ) -> None:
        self.darwin_client = darwin_client
        self.kb_client = kb_client
        self.cache_ttl = cache_ttl_seconds
        # Station CRS -> (cached_time_epoch, list of messages)
        self._station_messages_cache: dict[str, tuple[float, list[str]]] = {}
        # (cached_time_epoch, list of parsed KB incident dicts)
        self._kb_incidents_cache: tuple[float, list[dict[str, Any]]] | None = None
        # (cached_time_epoch, failure_status) for short error backoff deduplication
        self._kb_failure_cache: tuple[float, str] | None = None

    @property
    def kb_connection_status(self) -> str:
        """Return the current Knowledgebase connection status."""
        if not self.kb_client:
            return KB_STATUS_NOT_CONFIGURED
        status = getattr(self.kb_client, "status", None)
        if isinstance(status, str):
            return status
        return KB_STATUS_NOT_CONFIGURED

    @property
    def kb_last_successful_check(self) -> datetime | None:
        """Return the timestamp of the last successful Knowledgebase check."""
        if not self.kb_client:
            return None
        last_check = getattr(self.kb_client, "last_successful_check", None)
        if isinstance(last_check, datetime):
            return last_check
        return None

    async def get_station_messages(self, crs: str) -> list[str]:
        """Get cached or fresh Darwin station messages."""
        if not self.darwin_client or not crs:
            return []

        now_epoch = time.time()
        cached = self._station_messages_cache.get(crs)
        if cached and (now_epoch - cached[0]) < self.cache_ttl:
            return cached[1]

        messages = await self.darwin_client.fetch_station_messages(crs)
        self._station_messages_cache[crs] = (now_epoch, messages)
        return messages

    async def get_kb_incidents(self) -> list[dict[str, Any]]:
        """Get cached or fresh Knowledgebase incidents."""
        if not self.kb_client:
            return []

        now_epoch = time.time()
        if self._kb_incidents_cache and (now_epoch - self._kb_incidents_cache[0]) < self.cache_ttl:
            return self._kb_incidents_cache[1]

        # Prevent hammer during error window across simultaneous queries
        if self._kb_failure_cache and (now_epoch - self._kb_failure_cache[0]) < min(self.cache_ttl, 30):
            return []

        incidents = await self.kb_client.fetch_incidents()
        status = getattr(self.kb_client, "status", None)
        if not isinstance(status, str) or status == KB_STATUS_CONNECTED:
            self._kb_incidents_cache = (now_epoch, incidents)
            self._kb_failure_cache = None
        else:
            # On failure, clear success cache so failed refresh does not leave stale connected status
            self._kb_incidents_cache = None
            self._kb_failure_cache = (now_epoch, str(status))
        return incidents

    async def get_disruptions_for_query(
        self,
        origin: str,
        destination: str | None,
        next_trains: list[dict[str, Any]],
        now: datetime | None = None,
    ) -> tuple[str, list[str], list[dict[str, Any]]]:
        """Fetch and filter station messages and disruptions for a specific query."""
        if now is None:
            now = datetime.now(UK_TZ)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=UK_TZ)

        station_messages = await self.get_station_messages(origin)
        dest_station_messages: list[str] = []
        if destination:
            dest_station_messages = await self.get_station_messages(destination)

        raw_incidents = await self.get_kb_incidents()

        origin_names: list[str] = []
        dest_names: list[str] = []
        for t in next_trains:
            o_name = t.get("origin_name")
            if o_name and o_name not in origin_names:
                origin_names.append(o_name)
            d_name = t.get("destination_name")
            if d_name and d_name not in dest_names:
                dest_names.append(d_name)

        # Correlate destination messages to this query
        all_messages: list[str] = list(station_messages)
        correlated_dest_messages: list[str] = []
        if destination:
            for d_msg in dest_station_messages:
                if is_destination_message_relevant(d_msg, origin, origin_names, destination, dest_names):
                    correlated_dest_messages.append(d_msg)
                    if d_msg not in all_messages:
                        all_messages.append(d_msg)

        disruptions: list[dict[str, Any]] = []
        for inc in raw_incidents:
            # Filter active incidents using timezone-aware now
            if not is_incident_active(inc, now):
                continue

            matches_origin = incident_matches_crs(inc, origin, origin_names)
            matches_dest = (
                incident_matches_crs(inc, destination, dest_names)
                if destination
                else False
            )

            if matches_origin or matches_dest:
                disruptions.append(
                    {
                        "id": inc["id"],
                        "title": inc["title"],
                        "is_planned": inc["is_planned"],
                        "summary": inc["summary"],
                        "alternative_travel": inc.get("alternative_travel"),
                        "url": inc.get("url"),
                    }
                )

        service_status = compute_service_status(
            next_trains=next_trains,
            disruptions=disruptions,
            station_messages=station_messages,
            origin=origin,
            destination=destination,
            origin_station_names=origin_names,
            destination_station_names=dest_names,
            destination_messages=correlated_dest_messages,
        )
        return service_status, all_messages, disruptions
