from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_API_TOKEN as RTT_CONF_API_TOKEN,
    CONF_REFRESH_TOKEN as RTT_CONF_REFRESH_TOKEN,
    CONF_AUTOADJUSTSCANS,
    CONF_END,
    CONF_JOURNEYDATA,
    CONF_PLATFORMS_OF_INTEREST,
    CONF_QUERIES,
    CONF_SENSORNAME,
    CONF_PEAK_INTERVAL,
    CONF_OFF_PEAK_INTERVAL,
    CONF_PEAK_WINDOWS,
    DEFAULT_PEAK_INTERVAL,
    DEFAULT_OFF_PEAK_INTERVAL,
    DEFAULT_PEAK_WINDOWS,
    CONF_START,
    CONF_TIMEOFFSET,
    CRS_CODE_PATTERN,
    DOMAIN,
)
from .normalization import coerce_positive_int, coerce_time_offset, split_csv, parse_time_windows
from .rtt_api import RealtimeTrainsApiClient, RealtimeTrainsApiAuthError

_LOGGER = logging.getLogger(__name__)

FIELD_ADD_ANOTHER = "add_another"
FIELD_EDIT_QUERIES = "edit_queries"
FIELD_PLATFORMS = "platforms_input"
FIELD_TIME_OFFSET = "time_offset_minutes"
MAX_TIME_OFFSET_MINUTES = 12 * 60


def _user_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(RTT_CONF_REFRESH_TOKEN): cv.string,
            vol.Optional(CONF_AUTOADJUSTSCANS, default=False): bool,            vol.Optional(CONF_PEAK_INTERVAL, default=DEFAULT_PEAK_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Optional(CONF_OFF_PEAK_INTERVAL, default=DEFAULT_OFF_PEAK_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=30, max=21600)),
            vol.Optional(CONF_PEAK_WINDOWS, default=DEFAULT_PEAK_WINDOWS): cv.string,
        }
    )


def _query_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(CONF_SENSORNAME, default=defaults.get(CONF_SENSORNAME, "")): cv.string,
            vol.Required(CONF_START, default=defaults.get(CONF_START, "")): cv.string,
            vol.Optional(CONF_END, default=defaults.get(CONF_END, "")): cv.string,
            vol.Optional(CONF_JOURNEYDATA, default=defaults.get(CONF_JOURNEYDATA, 0)): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
            vol.Optional(FIELD_TIME_OFFSET, default=defaults.get(FIELD_TIME_OFFSET, 0)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=MAX_TIME_OFFSET_MINUTES)
            ),
            vol.Optional(FIELD_PLATFORMS, default=defaults.get(FIELD_PLATFORMS, "")): cv.string,
            vol.Optional(FIELD_ADD_ANOTHER, default=False): bool,
        }
    )


def _convert_query_input(user_input: dict[str, Any]) -> tuple[dict[str, Any], bool, dict[str, str]]:
    errors: dict[str, str] = {}

    origin = str(user_input.get(CONF_START, "")).strip().upper()
    destination_raw = user_input.get(CONF_END, "")
    destination_str = str(destination_raw).strip().upper() if destination_raw is not None else ""
    destination = destination_str or None

    if not origin:
        errors[CONF_START] = "required"
    elif not CRS_CODE_PATTERN.match(origin):
        errors[CONF_START] = "invalid_crs"

    if destination and not CRS_CODE_PATTERN.match(destination):
        errors[CONF_END] = "invalid_crs"

    sensor_name_raw = user_input.get(CONF_SENSORNAME)
    sensor_name = sensor_name_raw.strip() if isinstance(sensor_name_raw, str) else None
    if sensor_name == "":
        sensor_name = None

    journey_data = coerce_positive_int(user_input.get(CONF_JOURNEYDATA, 0))
    time_offset = coerce_positive_int(user_input.get(FIELD_TIME_OFFSET, 0))
    platforms = split_csv(user_input.get(FIELD_PLATFORMS, ""))

    add_another = bool(user_input.get(FIELD_ADD_ANOTHER))

    query = {
        CONF_SENSORNAME: sensor_name,
        CONF_START: origin,
        CONF_END: destination,
        CONF_JOURNEYDATA: journey_data,
        CONF_TIMEOFFSET: time_offset,
        CONF_PLATFORMS_OF_INTEREST: platforms,
    }

    return query, add_another, errors


def _query_form_defaults(raw_query: dict[str, Any]) -> dict[str, Any]:
    time_offset = coerce_time_offset(raw_query.get(CONF_TIMEOFFSET, timedelta()), timedelta())
    minutes = int(time_offset.total_seconds() // 60)

    platforms = raw_query.get(CONF_PLATFORMS_OF_INTEREST, []) or []

    return {
        CONF_SENSORNAME: raw_query.get(CONF_SENSORNAME, "") or "",
        CONF_START: raw_query.get(CONF_START, ""),
        CONF_END: (raw_query.get(CONF_END) or ""),
        CONF_JOURNEYDATA: raw_query.get(CONF_JOURNEYDATA, 0),
        FIELD_TIME_OFFSET: minutes,
        FIELD_PLATFORMS: ", ".join(platforms),
        FIELD_ADD_ANOTHER: False,
    }


@config_entries.HANDLERS.register(DOMAIN)
class RealtimeTrainsConfigFlow(config_entries.ConfigFlow):
    """Handle a config flow for Realtime Trains API."""

    VERSION = 1
    domain = DOMAIN

    def __init__(self) -> None:
        self._config_data: dict[str, Any] = {}
        self._queries: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            refresh_token = user_input.get(RTT_CONF_REFRESH_TOKEN, "").strip()
            _LOGGER.debug("Starting RTT config flow validation with refresh token (truncated): %s...", refresh_token[:10] if refresh_token else "none")
            
            if not refresh_token:
                errors[RTT_CONF_REFRESH_TOKEN] = "required"
            else:
                try:
                    # Validate by getting the first access token
                    from homeassistant.helpers.aiohttp_client import async_get_clientsession
                    session = async_get_clientsession(self.hass)
                    # Create a temporary client to fetch the first token
                    # Use 'none' as initial token since we are about to refresh
                    client = RealtimeTrainsApiClient(session, "none", refresh_token)
                    
                    _LOGGER.debug("Attempting to fetch initial access token to validate refresh token")
                    access_token = await client.async_get_access_token()
                    _LOGGER.debug("Initial access token fetched successfully")
                    
                    user_input[RTT_CONF_API_TOKEN] = access_token
                    user_input[RTT_CONF_REFRESH_TOKEN] = refresh_token
                    user_input[CONF_PEAK_INTERVAL] = int(user_input.get(CONF_PEAK_INTERVAL, DEFAULT_PEAK_INTERVAL))
                    user_input[CONF_OFF_PEAK_INTERVAL] = int(user_input.get(CONF_OFF_PEAK_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL))
                    try:
                        parse_time_windows(user_input.get(CONF_PEAK_WINDOWS, ""))
                    except ValueError:
                        errors[CONF_PEAK_WINDOWS] = "invalid_time_windows"
                        raise ValueError("Invalid time windows")
                        
                    await self.async_set_unique_id(refresh_token.lower()[:30])
                    self._abort_if_unique_id_configured()
                    self._config_data = dict(user_input)
                    _LOGGER.debug("Config flow user step completed successfully")
                    return await self.async_step_query()
                except RealtimeTrainsApiAuthError as err:
                    _LOGGER.error("Authentication failed during config flow: %s", err)
                    errors[RTT_CONF_REFRESH_TOKEN] = "invalid_auth"
                except Exception as err:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected error during config flow validation: %s", err)
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(),
            errors=errors,
        )

    async def async_step_query(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        defaults = dict(user_input) if user_input else {}

        if user_input is not None:
            query, add_another, query_errors = _convert_query_input(user_input)
            if query_errors:
                errors.update(query_errors)
                defaults = {
                    CONF_SENSORNAME: query.get(CONF_SENSORNAME) or "",
                    CONF_START: query.get(CONF_START, ""),
                    CONF_END: query.get(CONF_END) or "",
                    CONF_JOURNEYDATA: query.get(CONF_JOURNEYDATA, 0),
                    FIELD_TIME_OFFSET: query.get(CONF_TIMEOFFSET, 0),
                    FIELD_PLATFORMS: user_input.get(FIELD_PLATFORMS, ""),
                    FIELD_ADD_ANOTHER: add_another,
                }
            else:
                self._queries.append(query)
                if add_another:
                    return self.async_show_form(
                        step_id="query",
                        data_schema=_query_schema(),
                        description_placeholders={"added": str(len(self._queries))},
                        errors={},
                    )

                data = dict(self._config_data)
                data[CONF_QUERIES] = self._queries
                title = self._entry_title()
                return self.async_create_entry(title=title, data=data)

        description_placeholders = {"added": str(len(self._queries))}
        return self.async_show_form(
            step_id="query",
            data_schema=_query_schema(defaults),
            description_placeholders=description_placeholders,
            errors=errors,
        )

    def _entry_title(self) -> str:
        token = self._config_data.get(RTT_CONF_API_TOKEN)
        if token:
            display_token = token[:5] + "..." if len(token) > 5 else token
            return f"Realtime Trains API ({display_token})"
        return "Realtime Trains API"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return RealtimeTrainsOptionsFlowHandler(config_entry)


class RealtimeTrainsOptionsFlowHandler(config_entries.OptionsFlow):
    """Allow users to adjust existing Realtime Trains API configuration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._queries: list[dict[str, Any]] = []
        self._options: dict[str, Any] = {}

        if CONF_QUERIES in config_entry.options:
            existing_queries = list(config_entry.options.get(CONF_QUERIES) or [])
        else:
            existing_queries = list(config_entry.data.get(CONF_QUERIES, []))
        self._existing_queries_raw = list(existing_queries)
        self._query_defaults = [_query_form_defaults(query) for query in existing_queries]

        auto_adjust_default = config_entry.options.get(
            CONF_AUTOADJUSTSCANS,
            config_entry.data.get(CONF_AUTOADJUSTSCANS, False),
        )
        self._auto_adjust_default = bool(auto_adjust_default)
        self._peak_interval_default = config_entry.options.get(CONF_PEAK_INTERVAL, config_entry.data.get(CONF_PEAK_INTERVAL, DEFAULT_PEAK_INTERVAL))
        self._off_peak_interval_default = config_entry.options.get(CONF_OFF_PEAK_INTERVAL, config_entry.data.get(CONF_OFF_PEAK_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL))
        self._peak_windows_default = config_entry.options.get(CONF_PEAK_WINDOWS, config_entry.data.get(CONF_PEAK_WINDOWS, DEFAULT_PEAK_WINDOWS))

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_time_windows(user_input.get(CONF_PEAK_WINDOWS, ""))
            except ValueError:
                errors[CONF_PEAK_WINDOWS] = "invalid_time_windows"
                force_edit = not self._existing_queries_raw
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._init_schema(force_edit),
                    errors=errors
                )
                
            self._options[CONF_AUTOADJUSTSCANS] = bool(user_input.get(CONF_AUTOADJUSTSCANS, False))
            self._options[CONF_PEAK_INTERVAL] = int(user_input.get(CONF_PEAK_INTERVAL, DEFAULT_PEAK_INTERVAL))
            self._options[CONF_OFF_PEAK_INTERVAL] = int(user_input.get(CONF_OFF_PEAK_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL))
            self._options[CONF_PEAK_WINDOWS] = user_input.get(CONF_PEAK_WINDOWS, "")

            edit_queries = bool(user_input.get(FIELD_EDIT_QUERIES, False))

            if not self._existing_queries_raw:
                edit_queries = True

            if not edit_queries:
                options = dict(self._options)
                options[CONF_QUERIES] = list(self._existing_queries_raw)
                return self.async_create_entry(title="", data=options)

            return await self.async_step_query()

        force_edit = not self._existing_queries_raw
        return self.async_show_form(
            step_id="init",
            data_schema=self._init_schema(force_edit),
        )

    async def async_step_query(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            query, add_another, query_errors = _convert_query_input(user_input)
            if query_errors:
                errors.update(query_errors)
                defaults = {
                    CONF_SENSORNAME: user_input.get(CONF_SENSORNAME, ""),
                    CONF_START: user_input.get(CONF_START, ""),
                    CONF_END: user_input.get(CONF_END, ""),
                    CONF_JOURNEYDATA: user_input.get(CONF_JOURNEYDATA, 0),
                    FIELD_TIME_OFFSET: user_input.get(FIELD_TIME_OFFSET, 0),
                    FIELD_PLATFORMS: user_input.get(FIELD_PLATFORMS, ""),
                    FIELD_ADD_ANOTHER: add_another,
                }
                return self._show_query_form(defaults, errors)

            self._queries.append(query)

            if add_another:
                defaults = self._prefill_for_index(len(self._queries))
                return self._show_query_form(defaults, {})

            options = dict(self._options)
            options[CONF_QUERIES] = list(self._queries)
            return self.async_create_entry(title="", data=options)

        defaults = self._prefill_for_index(0)
        return self._show_query_form(defaults, errors)

    def _show_query_form(self, defaults: dict[str, Any], errors: dict[str, str]) -> FlowResult:
        defaults = dict(defaults)
        remaining_defaults = len(self._query_defaults) - len(self._queries) - 1
        defaults.setdefault(FIELD_ADD_ANOTHER, remaining_defaults >= 0)

        description_placeholders = {"added": str(len(self._queries))}
        return self.async_show_form(
            step_id="query",
            data_schema=_query_schema(defaults),
            description_placeholders=description_placeholders,
            errors=errors,
        )

    def _prefill_for_index(self, index: int) -> dict[str, Any]:
        if index < len(self._query_defaults):
            defaults = dict(self._query_defaults[index])
            defaults[FIELD_ADD_ANOTHER] = index < len(self._query_defaults) - 1
            return defaults
        return {}

    def _init_schema(self, force_edit: bool) -> vol.Schema:
        schema_dict: dict[Any, Any] = {
            vol.Optional(CONF_AUTOADJUSTSCANS, default=self._auto_adjust_default): bool,            vol.Optional(CONF_PEAK_INTERVAL, default=self._peak_interval_default): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Optional(CONF_OFF_PEAK_INTERVAL, default=self._off_peak_interval_default): vol.All(vol.Coerce(int), vol.Range(min=30, max=21600)),
            vol.Optional(CONF_PEAK_WINDOWS, default=self._peak_windows_default): cv.string,
        }

        if force_edit:
            schema_dict[vol.Optional(FIELD_EDIT_QUERIES, default=True)] = bool
        else:
            schema_dict[vol.Optional(FIELD_EDIT_QUERIES, default=False)] = bool

        return vol.Schema(schema_dict)
