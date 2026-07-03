from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.realtime_trains_api.config_flow import (
    FIELD_ADD_ANOTHER,
    FIELD_PLATFORMS,
    FIELD_TIME_OFFSET,
    RealtimeTrainsConfigFlow,
    RealtimeTrainsOptionsFlowHandler,
)
from custom_components.realtime_trains_api.rtt_api import RealtimeTrainsApiAuthError

USER_INPUT = {
    "refresh_token": "refresh-token-1234567890",
    "auto_adjust_scans": False,
    "peak_interval": 60,
    "off_peak_interval": 300,
    "peak_windows": "07:00-09:30, 16:00-19:00",
}

QUERY_INPUT = {
    "sensor_name": "",
    "origin": "dfd",
    "destination": "cst",
    "journey_data_for_next_X_trains": 3,
    "max_trains": 0,
    FIELD_TIME_OFFSET: 0,
    FIELD_PLATFORMS: "",
    "lookback_minutes": 60,
    FIELD_ADD_ANOTHER: False,
}


def _make_flow():
    flow = RealtimeTrainsConfigFlow()
    flow.hass = MagicMock()
    return flow


def _patch_api_client(token="access-token", auth_error=False):
    client = MagicMock()
    if auth_error:
        client.async_get_access_token = AsyncMock(
            side_effect=RealtimeTrainsApiAuthError("bad token")
        )
    else:
        client.async_get_access_token = AsyncMock(return_value=token)
    return patch(
        "custom_components.realtime_trains_api.config_flow.RealtimeTrainsApiClient",
        return_value=client,
    )


@pytest.mark.asyncio
async def test_user_step_shows_form_initially():
    flow = _make_flow()
    result = await flow.async_step_user(None)
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_full_happy_path_creates_entry():
    flow = _make_flow()

    with _patch_api_client():
        result = await flow.async_step_user(dict(USER_INPUT))

    assert result["type"] == "form"
    assert result["step_id"] == "query"

    result = await flow.async_step_query(dict(QUERY_INPUT))

    assert result["type"] == "create_entry"
    data = result["data"]
    assert data["token"] == "access-token"
    assert data["refresh_token"] == USER_INPUT["refresh_token"]
    assert len(data["queries"]) == 1
    query = data["queries"][0]
    # CRS codes are upper-cased on the way in
    assert query["origin"] == "DFD"
    assert query["destination"] == "CST"
    assert query["journey_data_for_next_X_trains"] == 3
    assert query["max_trains"] is None  # 0 means "automatic"


@pytest.mark.asyncio
async def test_add_another_collects_multiple_queries():
    flow = _make_flow()
    with _patch_api_client():
        await flow.async_step_user(dict(USER_INPUT))

    result = await flow.async_step_query({**QUERY_INPUT, FIELD_ADD_ANOTHER: True})
    assert result["type"] == "form"
    assert result["step_id"] == "query"
    assert result["description_placeholders"] == {"added": "1"}

    result = await flow.async_step_query(
        {**QUERY_INPUT, "origin": "WAT", "destination": "WAL"}
    )
    assert result["type"] == "create_entry"
    assert [q["origin"] for q in result["data"]["queries"]] == ["DFD", "WAT"]


@pytest.mark.asyncio
async def test_invalid_crs_codes_rejected():
    flow = _make_flow()
    with _patch_api_client():
        await flow.async_step_user(dict(USER_INPUT))

    result = await flow.async_step_query({**QUERY_INPUT, "origin": "TOOLONG"})
    assert result["type"] == "form"
    assert result["errors"] == {"origin": "invalid_crs"}

    result = await flow.async_step_query({**QUERY_INPUT, "destination": "X1"})
    assert result["type"] == "form"
    assert result["errors"] == {"destination": "invalid_crs"}

    result = await flow.async_step_query({**QUERY_INPUT, "origin": ""})
    assert result["type"] == "form"
    assert result["errors"] == {"origin": "required"}


@pytest.mark.asyncio
async def test_user_step_auth_failure():
    flow = _make_flow()
    with _patch_api_client(auth_error=True):
        result = await flow.async_step_user(dict(USER_INPUT))

    assert result["type"] == "form"
    assert result["errors"] == {"refresh_token": "invalid_auth"}


@pytest.mark.asyncio
async def test_user_step_invalid_peak_windows():
    flow = _make_flow()
    with _patch_api_client():
        result = await flow.async_step_user(
            {**USER_INPUT, "peak_windows": "not-a-window"}
        )

    assert result["type"] == "form"
    assert result["errors"]["peak_windows"] == "invalid_time_windows"


@pytest.mark.asyncio
async def test_user_step_missing_refresh_token():
    flow = _make_flow()
    result = await flow.async_step_user({**USER_INPUT, "refresh_token": "  "})
    assert result["type"] == "form"
    assert result["errors"] == {"refresh_token": "required"}


def _make_config_entry(queries=None, options=None):
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "queries": queries or [],
    }
    entry.options = options if options is not None else {}
    return entry


@pytest.mark.asyncio
async def test_options_flow_keeps_existing_queries_when_not_editing():
    existing = [{"origin": "DFD", "destination": "CST"}]
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry(queries=existing))

    result = await handler.async_step_init(
        {
            "auto_adjust_scans": True,
            "peak_interval": 90,
            "off_peak_interval": 600,
            "peak_windows": "07:00-09:00",
            "edit_queries": False,
        }
    )

    assert result["type"] == "create_entry"
    options = result["data"]
    assert options["queries"] == existing
    assert options["auto_adjust_scans"] is True
    assert options["peak_interval"] == 90
    assert options["off_peak_interval"] == 600


@pytest.mark.asyncio
async def test_options_flow_edit_prefills_existing_query():
    existing = [
        {
            "origin": "DFD",
            "destination": "CST",
            "journey_data_for_next_X_trains": 2,
            "max_trains": 8,
            "platforms_of_interest": ["1", "2"],
        }
    ]
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry(queries=existing))

    result = await handler.async_step_init(
        {
            "auto_adjust_scans": False,
            "peak_interval": 60,
            "off_peak_interval": 300,
            "peak_windows": "",
            "edit_queries": True,
        }
    )
    assert result["type"] == "form"
    assert result["step_id"] == "query"

    # Submit an edited version of the prefilled query
    result = await handler.async_step_query(
        {**QUERY_INPUT, "origin": "DFD", "destination": "LBG", "max_trains": 5}
    )
    assert result["type"] == "create_entry"
    queries = result["data"]["queries"]
    assert len(queries) == 1
    assert queries[0]["destination"] == "LBG"
    assert queries[0]["max_trains"] == 5


@pytest.mark.asyncio
async def test_options_flow_invalid_time_windows():
    handler = RealtimeTrainsOptionsFlowHandler(
        _make_config_entry(queries=[{"origin": "DFD", "destination": "CST"}])
    )

    result = await handler.async_step_init(
        {
            "auto_adjust_scans": False,
            "peak_interval": 60,
            "off_peak_interval": 300,
            "peak_windows": "25:99-",
            "edit_queries": False,
        }
    )
    assert result["type"] == "form"
    assert result["errors"] == {"peak_windows": "invalid_time_windows"}


@pytest.mark.asyncio
async def test_options_flow_forces_query_editing_when_none_exist():
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry(queries=[]))

    result = await handler.async_step_init(
        {
            "auto_adjust_scans": False,
            "peak_interval": 60,
            "off_peak_interval": 300,
            "peak_windows": "",
            "edit_queries": False,
        }
    )
    # With no existing queries the flow must route to the query step anyway
    assert result["type"] == "form"
    assert result["step_id"] == "query"


@pytest.mark.asyncio
async def test_reauth_updates_entry_and_reloads():
    flow = _make_flow()
    entry = _make_config_entry()
    flow.hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    flow.hass.config_entries.async_update_entry = MagicMock()
    flow.hass.config_entries.async_reload = AsyncMock()
    flow.context = {"entry_id": entry.entry_id}

    with _patch_api_client(token="new-access-token"):
        result = await flow.async_step_reauth(dict(entry.data))
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"

        result = await flow.async_step_reauth_confirm(
            {"refresh_token": "new-refresh-token"}
        )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    updated_data = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert updated_data["token"] == "new-access-token"
    assert updated_data["refresh_token"] == "new-refresh-token"
    flow.hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_reauth_rejects_bad_token():
    flow = _make_flow()
    entry = _make_config_entry()
    flow.hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    flow.context = {"entry_id": entry.entry_id}

    with _patch_api_client(auth_error=True):
        await flow.async_step_reauth(dict(entry.data))
        result = await flow.async_step_reauth_confirm({"refresh_token": "bad"})

    assert result["type"] == "form"
    assert result["errors"] == {"refresh_token": "invalid_auth"}
