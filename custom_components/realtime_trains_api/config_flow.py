from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_API_PASSWORD,
    CONF_API_USERNAME,
    CONF_AUTOADJUSTSCANS,
    CONF_END,
    CONF_JOURNEYDATA,
    CONF_PLATFORMS_OF_INTEREST,
    CONF_QUERIES,
    CONF_SENSORNAME,
    CONF_START,
    CONF_STOPS_OF_INTEREST,
    CONF_TIMEOFFSET,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

FIELD_ADD_ANOTHER = "add_another"
FIELD_PLATFORMS = "platforms_input"
FIELD_STOPS = "stops_input"
FIELD_TIME_OFFSET = "time_offset_minutes"
MIN_SCAN_INTERVAL_SECONDS = 30
MAX_SCAN_INTERVAL_SECONDS = 6 * 3600
MAX_TIME_OFFSET_MINUTES = 12 * 60


def _user_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_API_USERNAME): vol.All(str, lambda value: value.strip()),
            vol.Required(CONF_API_PASSWORD): str,
            vol.Optional(CONF_AUTOADJUSTSCANS, default=False): bool,
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=int(DEFAULT_SCAN_INTERVAL.total_seconds()),
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL_SECONDS, max=MAX_SCAN_INTERVAL_SECONDS)),
        }
    )


def _query_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(CONF_SENSORNAME, default=defaults.get(CONF_SENSORNAME, "")): vol.All(
                str, lambda value: value.strip()
            ),
            vol.Required(CONF_START, default=defaults.get(CONF_START, "")): vol.All(
                str, lambda value: value.strip()
            ),
            vol.Required(CONF_END, default=defaults.get(CONF_END, "")): vol.All(
                str, lambda value: value.strip()
            ),
            vol.Optional(CONF_JOURNEYDATA, default=defaults.get(CONF_JOURNEYDATA, 0)): vol.All(
                vol.Coerce(int), vol.Range(min=0)
            ),
            vol.Optional(FIELD_TIME_OFFSET, default=defaults.get(FIELD_TIME_OFFSET, 0)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=MAX_TIME_OFFSET_MINUTES)
            ),
            vol.Optional(FIELD_STOPS, default=defaults.get(FIELD_STOPS, "")): str,
            vol.Optional(FIELD_PLATFORMS, default=defaults.get(FIELD_PLATFORMS, "")): str,
            vol.Optional(FIELD_ADD_ANOTHER, default=False): bool,
        }
    )


def _split_csv(value: str) -> list[str]:
    if not value:
        return []
    cleaned = value.replace("\n", ",")
    return [item.strip() for item in cleaned.split(",") if item.strip()]


class RealtimeTrainsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[misc]
    """Handle a config flow for Realtime Trains API."""

    VERSION = 1

    def __init__(self) -> None:
        self._config_data: dict[str, Any] = {}
        self._queries: list[dict[str, Any]] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_API_USERNAME]
            if not username:
                errors[CONF_API_USERNAME] = "required"
            else:
                username = username.strip()
                if not username:
                    errors[CONF_API_USERNAME] = "required"

            if not errors:
                user_input[CONF_API_USERNAME] = username
                user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
                await self.async_set_unique_id(username.lower())
                self._abort_if_unique_id_configured()
                self._config_data = dict(user_input)
                return await self.async_step_query()

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(),
            errors=errors,
        )

    async def async_step_query(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        defaults = dict(user_input) if user_input else {}

        if user_input is not None:
            query, add_another, query_errors = self._convert_query_input(user_input)
            if query_errors:
                errors.update(query_errors)
                defaults = {
                    CONF_SENSORNAME: query.get(CONF_SENSORNAME) or "",
                    CONF_START: query.get(CONF_START, ""),
                    CONF_END: query.get(CONF_END, ""),
                    CONF_JOURNEYDATA: query.get(CONF_JOURNEYDATA, 0),
                    FIELD_TIME_OFFSET: query.get(CONF_TIMEOFFSET, 0),
                    FIELD_STOPS: user_input.get(FIELD_STOPS, ""),
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

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if self._reauth_entry is None:
            entry_id = self.context.get("entry_id")
            if not entry_id:
                return self.async_abort(reason="reauth_failed")
            self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
            if self._reauth_entry is None:
                return self.async_abort(reason="reauth_failed")

        errors: dict[str, str] = {}

        if user_input is not None:
            new_password = user_input.get(CONF_API_PASSWORD, "").strip()
            if not new_password:
                errors[CONF_API_PASSWORD] = "required"
            else:
                data = dict(self._reauth_entry.data)
                data[CONF_API_PASSWORD] = new_password
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=data)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth",
            data_schema=vol.Schema({vol.Required(CONF_API_PASSWORD): str}),
            description_placeholders={
                "username": self._reauth_entry.data.get(CONF_API_USERNAME, "")
                if self._reauth_entry
                else "",
            },
            errors=errors,
        )

    def _entry_title(self) -> str:
        username = self._config_data.get(CONF_API_USERNAME)
        if username:
            return f"Realtime Trains API ({username})"
        return "Realtime Trains API"

    def _convert_query_input(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, Any], bool, dict[str, str]]:
        errors: dict[str, str] = {}

        origin = str(user_input.get(CONF_START, "")).strip().upper()
        destination = str(user_input.get(CONF_END, "")).strip().upper()

        if not origin:
            errors[CONF_START] = "required"
        if not destination:
            errors[CONF_END] = "required"

        sensor_name_raw = user_input.get(CONF_SENSORNAME)
        sensor_name = sensor_name_raw.strip() if isinstance(sensor_name_raw, str) else None
        if sensor_name == "":
            sensor_name = None

        journey_data = int(user_input.get(CONF_JOURNEYDATA, 0))
        if journey_data < 0:
            journey_data = 0

        time_offset = int(user_input.get(FIELD_TIME_OFFSET, 0))
        if time_offset < 0:
            time_offset = 0

        stops = _split_csv(user_input.get(FIELD_STOPS, ""))
        platforms = _split_csv(user_input.get(FIELD_PLATFORMS, ""))

        add_another = bool(user_input.get(FIELD_ADD_ANOTHER))

        query = {
            CONF_SENSORNAME: sensor_name,
            CONF_START: origin,
            CONF_END: destination,
            CONF_JOURNEYDATA: journey_data,
            CONF_TIMEOFFSET: time_offset,
            CONF_STOPS_OF_INTEREST: stops,
            CONF_PLATFORMS_OF_INTEREST: platforms,
        }

        return query, add_another, errors