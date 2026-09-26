from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.realtime_trains_api.config_flow import (
    FIELD_ADD_ANOTHER,
    FIELD_PLATFORMS,
    FIELD_TIME_OFFSET,
    RealtimeTrainsConfigFlow,
    RealtimeTrainsOptionsFlowHandler,
)
from custom_components.realtime_trains_api.normalization import token_fingerprint
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


def _make_config_entry(queries=None, options=None, title="Realtime Trains API", unique_id="old-unique-id", version=2):
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.title = title
    entry.unique_id = unique_id
    entry.version = version
    entry.data = {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "queries": queries or [],
    }
    entry.options = options if options is not None else {}
    return entry


@pytest.mark.asyncio
async def test_options_menu_lists_actions():
    handler = RealtimeTrainsOptionsFlowHandler(
        _make_config_entry(queries=[{"origin": "DFD", "destination": "CST"}])
    )
    result = await handler.async_step_init()
    assert result["type"] == "menu"
    assert result["menu_options"] == [
        "settings", "add_query", "edit_query", "remove_query", "save",
    ]


@pytest.mark.asyncio
async def test_options_menu_hides_edit_remove_without_queries():
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry(queries=[]))
    result = await handler.async_step_init()
    assert result["menu_options"] == ["settings", "add_query", "save"]


@pytest.mark.asyncio
async def test_options_settings_then_save_keeps_queries():
    existing = [{"origin": "DFD", "destination": "CST"}]
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry(queries=existing))

    result = await handler.async_step_settings(
        {
            "auto_adjust_scans": True,
            "peak_interval": 90,
            "off_peak_interval": 600,
            "peak_windows": "07:00-09:00",
        }
    )
    assert result["type"] == "menu"

    result = await handler.async_step_save()
    assert result["type"] == "create_entry"
    assert result["data"]["queries"] == existing
    assert result["data"]["auto_adjust_scans"] is True
    assert result["data"]["peak_interval"] == 90
    assert result["data"]["off_peak_interval"] == 600


@pytest.mark.asyncio
async def test_options_settings_invalid_time_windows():
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry())
    result = await handler.async_step_settings(
        {
            "auto_adjust_scans": False,
            "peak_interval": 60,
            "off_peak_interval": 300,
            "peak_windows": "25:99-",
        }
    )
    assert result["type"] == "form"
    assert result["step_id"] == "settings"
    assert result["errors"] == {"peak_windows": "invalid_time_windows"}


@pytest.mark.asyncio
async def test_options_add_query():
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry(queries=[]))

    result = await handler.async_step_add_query(dict(QUERY_INPUT))
    assert result["type"] == "menu"

    saved = await handler.async_step_save()
    assert len(saved["data"]["queries"]) == 1
    assert saved["data"]["queries"][0]["origin"] == "DFD"


@pytest.mark.asyncio
async def test_options_add_query_rejects_invalid_pinned_time():
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry(queries=[]))
    result = await handler.async_step_add_query(
        {**QUERY_INPUT, "pinned_departure_time": "26:00"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"pinned_departure_time": "invalid_pinned_time"}


@pytest.mark.asyncio
async def test_options_edit_single_query_goes_straight_to_form():
    existing = [{"origin": "DFD", "destination": "CST", "max_trains": 8}]
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry(queries=existing))

    result = await handler.async_step_edit_query()
    assert result["step_id"] == "edit_query_form"

    result = await handler.async_step_edit_query_form(
        {**QUERY_INPUT, "origin": "DFD", "destination": "LBG", "max_trains": 5}
    )
    assert result["type"] == "menu"

    saved = await handler.async_step_save()
    assert saved["data"]["queries"][0]["destination"] == "LBG"
    assert saved["data"]["queries"][0]["max_trains"] == 5


@pytest.mark.asyncio
async def test_options_edit_selects_among_multiple():
    existing = [
        {"origin": "DFD", "destination": "CST"},
        {"origin": "WAT", "destination": "WAL"},
    ]
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry(queries=existing))

    result = await handler.async_step_edit_query()
    assert result["type"] == "form"
    assert result["step_id"] == "edit_query"

    result = await handler.async_step_edit_query({"query_index": "1"})
    assert result["step_id"] == "edit_query_form"

    await handler.async_step_edit_query_form(
        {**QUERY_INPUT, "origin": "WAT", "destination": "SUR"}
    )
    saved = await handler.async_step_save()
    assert [q["destination"] for q in saved["data"]["queries"]] == ["CST", "SUR"]


@pytest.mark.asyncio
async def test_options_remove_query():
    existing = [
        {"origin": "DFD", "destination": "CST"},
        {"origin": "WAT", "destination": "WAL"},
    ]
    handler = RealtimeTrainsOptionsFlowHandler(_make_config_entry(queries=existing))

    result = await handler.async_step_remove_query()
    assert result["step_id"] == "remove_query"

    result = await handler.async_step_remove_query({"query_index": "0"})
    assert result["type"] == "menu"
    # Menu no longer offers edit/remove once the list is down to one? It does -
    # one query remains, so both stay available.
    assert "edit_query" in result["menu_options"]

    saved = await handler.async_step_save()
    assert [q["origin"] for q in saved["data"]["queries"]] == ["WAT"]


@pytest.mark.asyncio
async def test_reauth_updates_entry_and_reloads():
    flow = _make_flow()
    entry = _make_config_entry(title="Realtime Trains API (refre...)")
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
    call_kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
    updated_data = call_kwargs["data"]
    assert updated_data["token"] == "new-access-token"
    assert updated_data["refresh_token"] == "new-refresh-token"
    assert call_kwargs["unique_id"] == token_fingerprint("new-refresh-token")
    assert call_kwargs["title"] == "Realtime Trains API"
    flow.hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_reauth_preserves_custom_title():
    flow = _make_flow()
    entry = _make_config_entry(title="Realtime Trains API (Work)")
    flow.hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    flow.hass.config_entries.async_update_entry = MagicMock()
    flow.hass.config_entries.async_reload = AsyncMock()
    flow.context = {"entry_id": entry.entry_id}

    with _patch_api_client(token="new-access-token"):
        await flow.async_step_reauth(dict(entry.data))
        result = await flow.async_step_reauth_confirm(
            {"refresh_token": "new-refresh-token"}
        )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    call_kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
    assert call_kwargs["unique_id"] == token_fingerprint("new-refresh-token")
    assert "title" not in call_kwargs


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


@pytest.mark.asyncio
async def test_user_step_invalid_time_windows():
    flow = _make_flow()
    with _patch_api_client():
        result = await flow.async_step_user(
            {
                **USER_INPUT,
                "peak_windows": "99:99-",
            }
        )

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"peak_windows": "invalid_time_windows"}
    assert "base" not in result["errors"]
    assert getattr(flow, "unique_id", None) is None


@pytest.mark.asyncio
async def test_credential_hygiene(caplog):
    flow = _make_flow()
    secret_refresh = "super_secret_refresh_token_abcdef123456"
    secret_access = "super_secret_access_token_789012"

    with _patch_api_client(token=secret_access), caplog.at_level("DEBUG"):
        user_result = await flow.async_step_user(
            {
                **USER_INPUT,
                "refresh_token": secret_refresh,
            }
        )
        assert user_result["type"] == "form"
        assert user_result["step_id"] == "query"

        result = await flow.async_step_query(dict(QUERY_INPUT))

    assert result["type"] == "create_entry"
    # Title must not expose token fragments
    assert result["title"] == "Realtime Trains API"
    assert "super_secret" not in result["title"]
    assert secret_refresh[:5] not in result["title"]
    assert secret_access[:5] not in result["title"]

    # Unique ID must be a SHA-256 fingerprint, not raw token prefix
    expected_unique_id = token_fingerprint(secret_refresh)
    assert flow.unique_id == expected_unique_id
    assert "super_secret" not in flow.unique_id
    assert secret_refresh[:10] not in flow.unique_id

    # Version check
    assert RealtimeTrainsConfigFlow.VERSION == 2

    # No token fragments in logs
    assert "super_secret" not in caplog.text
    assert secret_refresh[:10] not in caplog.text
    assert secret_access[:10] not in caplog.text


@pytest.mark.asyncio
async def test_flow_stores_disruption_credentials():
    from custom_components.realtime_trains_api.const import (
        CONF_OPENLDBWS_TOKEN,
        CONF_KB_USERNAME,
        CONF_KB_PASSWORD,
    )

    flow = _make_flow()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()

    with _patch_api_client():
        user_result = await flow.async_step_user(
            {
                **USER_INPUT,
                CONF_OPENLDBWS_TOKEN: "test-darwin-token",
                CONF_KB_USERNAME: "test-kb-user",
                CONF_KB_PASSWORD: "test-kb-pass",
            }
        )
        assert user_result["type"] == "form"
        assert user_result["step_id"] == "query"

        result = await flow.async_step_query(dict(QUERY_INPUT))

    assert result["type"] == "create_entry"
    assert result["data"][CONF_OPENLDBWS_TOKEN] == "test-darwin-token"
    assert result["data"][CONF_KB_USERNAME] == "test-kb-user"
    assert result["data"][CONF_KB_PASSWORD] == "test-kb-pass"
