from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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
    CONF_LOOKBACK,
    CONF_MAXTRAINS,
    CONF_PINNED_DEPARTURE,
    DEFAULT_LOOKBACK_MINUTES,
    HHMM_PATTERN,
)
from .normalization import (
    DEFAULT_TITLE,
    coerce_positive_int,
    coerce_time_offset,
    parse_time_windows,
    scrub_legacy_title,
    split_csv,
    token_fingerprint,
)
from .rtt_api import RealtimeTrainsApiClient, RealtimeTrainsApiAuthError

_LOGGER = logging.getLogger(__name__)

FIELD_ADD_ANOTHER = "add_another"
FIELD_PLATFORMS = "platforms_input"
FIELD_TIME_OFFSET = "time_offset_minutes"
MAX_TIME_OFFSET_MINUTES = 12 * 60


def _user_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(RTT_CONF_REFRESH_TOKEN): cv.string,
            vol.Optional(CONF_AUTOADJUSTSCANS, default=False): bool,
            vol.Optional(CONF_PEAK_INTERVAL, default=DEFAULT_PEAK_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Optional(CONF_OFF_PEAK_INTERVAL, default=DEFAULT_OFF_PEAK_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=30, max=21600)),
            vol.Optional(CONF_PEAK_WINDOWS, default=DEFAULT_PEAK_WINDOWS): cv.string,
        }
    )


def _query_schema(
    defaults: dict[str, Any] | None = None,
    include_add_another: bool = True,
) -> vol.Schema:
    defaults = defaults or {}
    schema: dict[Any, Any] = {
            vol.Optional(CONF_SENSORNAME, default=defaults.get(CONF_SENSORNAME, "")): cv.string,
            vol.Required(CONF_START, default=defaults.get(CONF_START, "")): cv.string,
            vol.Optional(CONF_END, default=defaults.get(CONF_END, "")): cv.string,
            vol.Optional(CONF_JOURNEYDATA, default=defaults.get(CONF_JOURNEYDATA, 0)): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
            vol.Optional(CONF_MAXTRAINS, default=defaults.get(CONF_MAXTRAINS, 0)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=50)
            ),
            vol.Optional(FIELD_TIME_OFFSET, default=defaults.get(FIELD_TIME_OFFSET, 0)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=MAX_TIME_OFFSET_MINUTES)
            ),
            vol.Optional(FIELD_PLATFORMS, default=defaults.get(FIELD_PLATFORMS, "")): cv.string,
            vol.Optional(CONF_LOOKBACK, default=defaults.get(CONF_LOOKBACK, DEFAULT_LOOKBACK_MINUTES)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=1440)
            ),
            vol.Optional(CONF_PINNED_DEPARTURE, default=defaults.get(CONF_PINNED_DEPARTURE, "")): cv.string,
    }
    if include_add_another:
        schema[vol.Optional(FIELD_ADD_ANOTHER, default=False)] = bool
    return vol.Schema(schema)


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
    max_trains = coerce_positive_int(user_input.get(CONF_MAXTRAINS, 0)) or None

    pinned_raw = user_input.get(CONF_PINNED_DEPARTURE)
    pinned = str(pinned_raw).strip() if pinned_raw else None
    if pinned and not HHMM_PATTERN.match(pinned):
        errors[CONF_PINNED_DEPARTURE] = "invalid_pinned_time"
    time_offset = coerce_positive_int(user_input.get(FIELD_TIME_OFFSET, 0))
    platforms = split_csv(user_input.get(FIELD_PLATFORMS, ""))
    lookback = coerce_positive_int(user_input.get(CONF_LOOKBACK, DEFAULT_LOOKBACK_MINUTES))

    add_another = bool(user_input.get(FIELD_ADD_ANOTHER))

    query = {
        CONF_SENSORNAME: sensor_name,
        CONF_START: origin,
        CONF_END: destination,
        CONF_JOURNEYDATA: journey_data,
        CONF_MAXTRAINS: max_trains,
        CONF_TIMEOFFSET: time_offset,
        CONF_PLATFORMS_OF_INTEREST: platforms,
        CONF_LOOKBACK: lookback,
        CONF_PINNED_DEPARTURE: pinned,
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
        CONF_MAXTRAINS: raw_query.get(CONF_MAXTRAINS) or 0,
        CONF_PINNED_DEPARTURE: raw_query.get(CONF_PINNED_DEPARTURE) or "",
        FIELD_TIME_OFFSET: minutes,
        FIELD_PLATFORMS: ", ".join(platforms),
        CONF_LOOKBACK: raw_query.get(CONF_LOOKBACK, DEFAULT_LOOKBACK_MINUTES),
        FIELD_ADD_ANOTHER: False,
    }


@config_entries.HANDLERS.register(DOMAIN)
class RealtimeTrainsConfigFlow(config_entries.ConfigFlow):
    """Handle a config flow for Realtime Trains API."""

    VERSION = 2
    domain = DOMAIN

    def __init__(self) -> None:
        self._config_data: dict[str, Any] = {}
        self._queries: list[dict[str, Any]] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauthentication when the stored refresh token stops working."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None and self._reauth_entry is not None:
            refresh_token = str(user_input.get(RTT_CONF_REFRESH_TOKEN, "")).strip()
            if not refresh_token:
                errors[RTT_CONF_REFRESH_TOKEN] = "required"
            else:
                try:
                    session = async_get_clientsession(self.hass)
                    client = RealtimeTrainsApiClient(session, "none", refresh_token)
                    access_token = await client.async_get_access_token()

                    new_unique_id = token_fingerprint(refresh_token)
                    new_title = scrub_legacy_title(self._reauth_entry.title)

                    update_kwargs: dict[str, Any] = {
                        "data": {
                            **self._reauth_entry.data,
                            RTT_CONF_API_TOKEN: access_token,
                            RTT_CONF_REFRESH_TOKEN: refresh_token,
                        },
                        "unique_id": new_unique_id,
                    }
                    if new_title != self._reauth_entry.title:
                        update_kwargs["title"] = new_title

                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry,
                        **update_kwargs,
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id
                    )
                    return self.async_abort(reason="reauth_successful")
                except RealtimeTrainsApiAuthError:
                    errors[RTT_CONF_REFRESH_TOKEN] = "invalid_auth"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected error during reauth")
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(RTT_CONF_REFRESH_TOKEN): cv.string}),
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            refresh_token = user_input.get(RTT_CONF_REFRESH_TOKEN, "").strip()
            _LOGGER.debug("Starting RTT config flow validation")
            
            if not refresh_token:
                errors[RTT_CONF_REFRESH_TOKEN] = "required"

            try:
                parse_time_windows(user_input.get(CONF_PEAK_WINDOWS, ""))
            except ValueError:
                errors[CONF_PEAK_WINDOWS] = "invalid_time_windows"

            if not errors:
                try:
                    session = async_get_clientsession(self.hass)
                    client = RealtimeTrainsApiClient(session, "none", refresh_token)
                    
                    _LOGGER.debug("Attempting to fetch initial access token to validate refresh token")
                    access_token = await client.async_get_access_token()
                    _LOGGER.debug("Initial access token fetched successfully")
                    
                    user_input[RTT_CONF_API_TOKEN] = access_token
                    user_input[RTT_CONF_REFRESH_TOKEN] = refresh_token
                    user_input[CONF_PEAK_INTERVAL] = int(user_input.get(CONF_PEAK_INTERVAL, DEFAULT_PEAK_INTERVAL))
                    user_input[CONF_OFF_PEAK_INTERVAL] = int(user_input.get(CONF_OFF_PEAK_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL))

                    unique_id = token_fingerprint(refresh_token)
                    await self.async_set_unique_id(unique_id)
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
                    CONF_LOOKBACK: user_input.get(CONF_LOOKBACK, DEFAULT_LOOKBACK_MINUTES),
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
        return DEFAULT_TITLE

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return RealtimeTrainsOptionsFlowHandler(config_entry)


class RealtimeTrainsOptionsFlowHandler(config_entries.OptionsFlow):
    """Menu-based editing of an existing Realtime Trains API configuration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        if CONF_QUERIES in config_entry.options:
            queries = list(config_entry.options.get(CONF_QUERIES) or [])
        else:
            queries = list(config_entry.data.get(CONF_QUERIES, []))
        # Working copies; nothing is persisted until the save step.
        self._queries: list[dict[str, Any]] = queries
        self._settings: dict[str, Any] = {
            CONF_AUTOADJUSTSCANS: bool(
                config_entry.options.get(
                    CONF_AUTOADJUSTSCANS,
                    config_entry.data.get(CONF_AUTOADJUSTSCANS, False),
                )
            ),
            CONF_PEAK_INTERVAL: config_entry.options.get(
                CONF_PEAK_INTERVAL,
                config_entry.data.get(CONF_PEAK_INTERVAL, DEFAULT_PEAK_INTERVAL),
            ),
            CONF_OFF_PEAK_INTERVAL: config_entry.options.get(
                CONF_OFF_PEAK_INTERVAL,
                config_entry.data.get(CONF_OFF_PEAK_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL),
            ),
            CONF_PEAK_WINDOWS: config_entry.options.get(
                CONF_PEAK_WINDOWS,
                config_entry.data.get(CONF_PEAK_WINDOWS, DEFAULT_PEAK_WINDOWS),
            ),
        }
        self._edit_index: int | None = None

    def _query_labels(self) -> dict[str, str]:
        labels: dict[str, str] = {}
        for idx, query in enumerate(self._queries):
            origin = query.get(CONF_START, "?")
            destination = query.get(CONF_END) or "all"
            label = f"{idx + 1}. {origin} → {destination}"
            pinned = query.get(CONF_PINNED_DEPARTURE)
            if pinned:
                label += f" (pinned {pinned})"
            labels[str(idx)] = label
        return labels

    def _settings_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Optional(CONF_AUTOADJUSTSCANS, default=self._settings[CONF_AUTOADJUSTSCANS]): bool,
                vol.Optional(CONF_PEAK_INTERVAL, default=self._settings[CONF_PEAK_INTERVAL]): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
                vol.Optional(CONF_OFF_PEAK_INTERVAL, default=self._settings[CONF_OFF_PEAK_INTERVAL]): vol.All(vol.Coerce(int), vol.Range(min=30, max=21600)),
                vol.Optional(CONF_PEAK_WINDOWS, default=self._settings[CONF_PEAK_WINDOWS]): cv.string,
            }
        )

    def _pick_query_schema(self) -> vol.Schema:
        return vol.Schema({vol.Required("query_index"): vol.In(self._query_labels())})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        menu_options = ["settings", "add_query"]
        if self._queries:
            menu_options += ["edit_query", "remove_query"]
        menu_options.append("save")
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_time_windows(user_input.get(CONF_PEAK_WINDOWS, ""))
            except ValueError:
                errors[CONF_PEAK_WINDOWS] = "invalid_time_windows"

            if not errors:
                self._settings = {
                    CONF_AUTOADJUSTSCANS: bool(user_input.get(CONF_AUTOADJUSTSCANS, False)),
                    CONF_PEAK_INTERVAL: int(user_input.get(CONF_PEAK_INTERVAL, DEFAULT_PEAK_INTERVAL)),
                    CONF_OFF_PEAK_INTERVAL: int(user_input.get(CONF_OFF_PEAK_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL)),
                    CONF_PEAK_WINDOWS: user_input.get(CONF_PEAK_WINDOWS, ""),
                }
                return await self.async_step_init()

        return self.async_show_form(
            step_id="settings",
            data_schema=self._settings_schema(),
            errors=errors,
        )

    async def async_step_add_query(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            query, _, query_errors = _convert_query_input(user_input)
            if not query_errors:
                self._queries.append(query)
                return await self.async_step_init()
            errors = query_errors

        return self.async_show_form(
            step_id="add_query",
            data_schema=_query_schema(user_input or {}, include_add_another=False),
            errors=errors,
        )

    async def async_step_edit_query(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._edit_index = int(user_input["query_index"])
            return await self.async_step_edit_query_form()

        if len(self._queries) == 1:
            self._edit_index = 0
            return await self.async_step_edit_query_form()

        return self.async_show_form(step_id="edit_query", data_schema=self._pick_query_schema())

    async def async_step_edit_query_form(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            query, _, query_errors = _convert_query_input(user_input)
            if not query_errors:
                self._queries[self._edit_index] = query
                return await self.async_step_init()
            errors = query_errors
            defaults = dict(user_input)
        else:
            defaults = _query_form_defaults(self._queries[self._edit_index])

        return self.async_show_form(
            step_id="edit_query_form",
            data_schema=_query_schema(defaults, include_add_another=False),
            errors=errors,
        )

    async def async_step_remove_query(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            index = int(user_input["query_index"])
            if 0 <= index < len(self._queries):
                self._queries.pop(index)
            return await self.async_step_init()

        return self.async_show_form(step_id="remove_query", data_schema=self._pick_query_schema())

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        options = dict(self._settings)
        options[CONF_QUERIES] = list(self._queries)
        return self.async_create_entry(title="", data=options)
