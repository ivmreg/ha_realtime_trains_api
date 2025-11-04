"""Support for UK train data provided by api.rtt.io."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import cast

import aiohttp
import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.const import (
    UnitOfTime,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import Throttle
import homeassistant.util.dt as dt_util

from . import rtt

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOFFSET = timedelta(minutes=0)

ATTR_ATCOCODE = "atcocode"
ATTR_LOCALITY = "locality"
ATTR_REQUEST_TIME = "request_time"
ATTR_JOURNEY_START = "journey_start"
ATTR_JOURNEY_END = "journey_end"
ATTR_NEXT_TRAINS = "next_trains"
ATTR_AGGREGATE = "aggregate_data"

CONF_API_USERNAME = "username"
CONF_API_PASSWORD = "password"
CONF_QUERIES = "queries"
CONF_AUTOADJUSTSCANS = "auto_adjust_scans"

CONF_START = "origin"
CONF_END = "destination"
CONF_JOURNEYDATA = "journey_data_for_next_X_trains"
CONF_SENSORNAME = "sensor_name"
CONF_TIMEOFFSET = "time_offset"
CONF_STOPS_OF_INTEREST = "stops_of_interest"

TIMEZONE = rtt.TIMEZONE
STRFFORMAT = rtt.STRFFORMAT

_QUERY_SCHEME = vol.Schema(
    {
        vol.Optional(CONF_SENSORNAME): cv.string,
        vol.Required(CONF_START): cv.string,
        vol.Required(CONF_END): cv.string,
        vol.Optional(CONF_JOURNEYDATA, default=0): cv.positive_int,
        vol.Optional(CONF_TIMEOFFSET, default=DEFAULT_TIMEOFFSET):
            vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(CONF_STOPS_OF_INTEREST): [cv.string],
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_AUTOADJUSTSCANS, default=False): cv.boolean,
        vol.Required(CONF_API_USERNAME): cv.string,
        vol.Required(CONF_API_PASSWORD): cv.string,
        vol.Required(CONF_QUERIES): [_QUERY_SCHEME],
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Get the realtime_train sensor."""
    sensors = { }
    interval = config[CONF_SCAN_INTERVAL]
    autoadjustscans = config[CONF_AUTOADJUSTSCANS]
    username = config[CONF_API_USERNAME]
    password = config[CONF_API_PASSWORD]
    queries = config[CONF_QUERIES]


    client = async_get_clientsession(hass)

    for query in queries:
        sensor_name = query.get(CONF_SENSORNAME, None)
        journey_start = query.get(CONF_START)
        journey_end = query.get(CONF_END)
        journey_data_for_next_X_trains = query.get(CONF_JOURNEYDATA)
        timeoffset = query.get(CONF_TIMEOFFSET)
        stops_of_interest = query.get(CONF_STOPS_OF_INTEREST, [])
        sensor = RealtimeTrainLiveTrainTimeSensor(
                sensor_name,
                username,
                password,
                journey_start,
                journey_end,
                journey_data_for_next_X_trains,
                timeoffset,
                autoadjustscans,
                stops_of_interest,
                interval,
                client
            )
        sensors[sensor.name] = sensor

    async_add_entities(sensors.values(), True)


class RealtimeTrainLiveTrainTimeSensor(SensorEntity):
    """
    Sensor that reads the rtt API.

    api.rtt.io provides free comprehensive train data for UK trains
    across the UK via simple JSON API. Subclasses of this
    base class can be used to access specific types of information.
    """



    TRANSPORT_API_URL_BASE = "https://api.rtt.io/api/v1/json/"
    _attr_icon = "mdi:train"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, sensor_name, username, password, journey_start, journey_end,
                journey_data_for_next_X_trains, timeoffset, autoadjustscans, stops_of_interest, interval, client):
        """Construct a live train time sensor."""

        default_sensor_name = (
            f"Next train from {journey_start} to {journey_end} ({timeoffset})" if (timeoffset.total_seconds() > 0)
            else f"Next train from {journey_start} to {journey_end}")

        self._journey_start = journey_start
        self._journey_end = journey_end
        self._journey_data_for_next_X_trains = journey_data_for_next_X_trains
        self._next_trains = []
        self._aggregate_data = {}
        self._data = {}
        self._username = username
        self._password = password
        self._timeoffset = timeoffset
        self._autoadjustscans = autoadjustscans
        self._stops_of_interest = stops_of_interest
        self._interval = interval
        self._client = client

        self._name = default_sensor_name if sensor_name is None else sensor_name
        self._state = None

        self.async_update = self._async_update

    async def _async_update(self):
        """Get the latest live departure data for the specified stop."""
        await self._getdepartures_api_request()
        self._next_trains = []
        departureCount = 0
        now = cast(datetime, dt_util.now()).astimezone(TIMEZONE)

        nextDepartureEstimatedTs : (datetime | None) = None

        departures = [] if self._data == {} or self._data["services"] == None else self._data["services"]

        for departure in departures:
            parsed = rtt.parse_departure(departure, now, self._timeoffset)
            if parsed is None:
                continue

            train, scheduledTs, estimatedTs = parsed
            route_valid = await self._add_journey_data(train, scheduledTs, estimatedTs)
            if not route_valid:
                continue

            if nextDepartureEstimatedTs is None:
                nextDepartureEstimatedTs = estimatedTs
            else:
                nextDepartureEstimatedTs = min(nextDepartureEstimatedTs, estimatedTs)

            self._next_trains.append(train)
            departureCount += 1
            if departureCount > self._journey_data_for_next_X_trains:
                break
        self._aggregate_data = await self._calculate_aggregates()

        if nextDepartureEstimatedTs is None:
            self._state = None
        else:
            self._state = int(rtt.delta_secs(nextDepartureEstimatedTs, now) // 60)

        if self._autoadjustscans:
            if nextDepartureEstimatedTs is None:
                self.async_update = Throttle(timedelta(minutes=30))(self._async_update)
            else:
                self.async_update = self._async_update


    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._state

    async def _getdepartures_api_request(self):
        """Perform an API request."""
        depsUrl = self.TRANSPORT_API_URL_BASE + f"search/{self._journey_start}/to/{self._journey_end}"
        async with self._client.get(depsUrl, auth=aiohttp.BasicAuth(login=self._username, password=self._password, encoding='utf-8')) as response:
            if response.status == 200:
                self._data = await response.json()
            elif response.status == 403:
                self._state = "Credentials invalid"
            else:
                _LOGGER.warning("Invalid response from API")

    async def _add_journey_data(self, train, scheduled_departure, estimated_departure) -> bool:
        """Populate detailed journey data for a service.

        Returns True if the service reaches the configured destination after the
        boarding station, otherwise False.
        """
        trainUrl = self.TRANSPORT_API_URL_BASE + f"service/{train['service_uid']}/{scheduled_departure.strftime('%Y/%m/%d')}"
        async with self._client.get(trainUrl, auth=aiohttp.BasicAuth(login=self._username, password=self._password, encoding='utf-8')) as response:
            if response.status == 200:
                data = await response.json()
                result = rtt.parse_service_detail(
                    data,
                    self._journey_start,
                    self._journey_end,
                    scheduled_departure,
                    estimated_departure,
                    self._stops_of_interest,
                )

                if not result.success:
                    if result.reason == rtt.REASON_MISSING_ORIGIN:
                        _LOGGER.warning(
                            "Service %s does not call at configured origin %s",
                            train['service_uid'],
                            self._journey_start,
                        )
                    elif result.reason == rtt.REASON_DESTINATION_NOT_REACHED:
                        _LOGGER.debug(
                            "Skipping service %s as destination %s is not reached after origin %s",
                            train['service_uid'],
                            self._journey_end,
                            self._journey_start,
                        )
                    return False

                if result.data:
                    train.update(result.data)
                return True

            _LOGGER.warning(
                "Could not populate arrival times: Invalid response from API (HTTP code %s)",
                response.status,
            )
            return False

    async def _calculate_aggregates(self):
        """Calculate aggregate delay and duration data."""
        departure_delays = []
        arrival_delays = []
        stop_delays = []
        durations = []
        agg_delays = {}
        for train in self._next_trains:
            if 'journey_time_mins' in train:
                durations.append(train['journey_time_mins'])
            if 'delay' in train and train['delay'] > 0:
                departure_delays.append(train['delay'])
            if 'arrival_delay' in train and train['arrival_delay'] > 0:
                arrival_delays.append(train['arrival_delay'])
            if 'stop_delay' in train and train['stop_delay'] > 0:
                stop_delays.append(train['stop_delay'])

        if len(arrival_delays):
            agg_delays['arrival'] = {
                'count': len(arrival_delays),
                'min': min(arrival_delays),
                'max': max(arrival_delays),
                'average': round( sum(arrival_delays) / len(arrival_delays) )
            }
        if len(departure_delays):
            agg_delays['departure'] = {
                'count': len(departure_delays),
                'min': min(departure_delays),
                'max': max(departure_delays),
                'average': round( sum(departure_delays) / len(departure_delays) )
            }
        if len(stop_delays):
            agg_delays['stop'] = {
                'count': len(stop_delays),
                'min': min(stop_delays),
                'max': max(stop_delays),
                'average': round( sum(stop_delays) / len(stop_delays) )
            }
        return {
            'delays': agg_delays,
            'durations': {
                'count': len(durations),
                'min': min(durations),
                'max': max(durations),
                'average': round( sum(durations) / len(durations) )
            }
        }

    @property
    def extra_state_attributes(self):
        """Return other details about the sensor state."""
        attrs = {}
        if self._data is not None:
            attrs[ATTR_JOURNEY_START] = self._journey_start
            attrs[ATTR_JOURNEY_END] = self._journey_end
            if self._next_trains:
                attrs[ATTR_NEXT_TRAINS] = self._next_trains
                attrs[ATTR_AGGREGATE] = self._aggregate_data
            return attrs

