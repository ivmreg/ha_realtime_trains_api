from unittest.mock import AsyncMock, MagicMock
import pytest
from custom_components.realtime_trains_api import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
    async_migrate_entry,
)
from custom_components.realtime_trains_api.normalization import token_fingerprint
from custom_components.realtime_trains_api.const import (
    DOMAIN,
    PLATFORMS,
    CONF_API_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_QUERIES,
    CONF_START,
    CONF_END,
    CONF_PEAK_INTERVAL,
    DEFAULT_PEAK_INTERVAL,
    CONF_OFF_PEAK_INTERVAL,
    DEFAULT_OFF_PEAK_INTERVAL,
    CONF_PEAK_WINDOWS,
    DEFAULT_PEAK_WINDOWS,
)
from custom_components.realtime_trains_api.coordinator import RealtimeTrainsUpdateCoordinator

@pytest.fixture
def mock_config_entry_data(config_entry):
    config_entry.data = {
        CONF_API_TOKEN: "test_api_token",
        CONF_REFRESH_TOKEN: "test_refresh_token",
        CONF_QUERIES: [
            {CONF_START: "PAD", CONF_END: "RDG"},
        ],
        CONF_PEAK_INTERVAL: DEFAULT_PEAK_INTERVAL,
        CONF_OFF_PEAK_INTERVAL: DEFAULT_OFF_PEAK_INTERVAL,
        CONF_PEAK_WINDOWS: DEFAULT_PEAK_WINDOWS,
    }
    config_entry.options = {}
    return config_entry

@pytest.mark.asyncio
async def test_async_setup(hass):
    """Test the async_setup function."""
    config = {}
    result = await async_setup(hass, config)
    assert result is True
    assert DOMAIN in hass.data

@pytest.mark.asyncio
async def test_async_setup_entry(hass, mock_config_entry_data):
    """Test setting up a config entry."""
    result = await async_setup_entry(hass, mock_config_entry_data)
    assert result is True
    assert mock_config_entry_data.entry_id in hass.data[DOMAIN]
    coordinator = hass.data[DOMAIN][mock_config_entry_data.entry_id]
    assert isinstance(coordinator, RealtimeTrainsUpdateCoordinator)
    assert coordinator.api is not None
    assert coordinator.queries == mock_config_entry_data.data[CONF_QUERIES]
    assert hass.config_entries.async_forward_entry_setups.called
    assert hass.config_entries.async_forward_entry_setups.call_args[0][1] == PLATFORMS

@pytest.mark.asyncio
async def test_async_unload_entry(hass, mock_config_entry_data):
    """Test unloading a config entry."""
    # Setup first
    await async_setup_entry(hass, mock_config_entry_data)
    assert mock_config_entry_data.entry_id in hass.data[DOMAIN]

    # Mock unload success
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    result = await async_unload_entry(hass, mock_config_entry_data)
    assert result is True
    assert mock_config_entry_data.entry_id not in hass.data[DOMAIN]
    assert hass.config_entries.async_unload_platforms.called
    assert hass.config_entries.async_unload_platforms.call_args[0][1] == PLATFORMS

@pytest.mark.asyncio
async def test_async_unload_entry_fails(hass, mock_config_entry_data):
    """Test unloading a config entry when it fails."""
    # Setup first
    await async_setup_entry(hass, mock_config_entry_data)
    assert mock_config_entry_data.entry_id in hass.data[DOMAIN]

    # Mock unload failure
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

    result = await async_unload_entry(hass, mock_config_entry_data)
    assert result is False
    assert mock_config_entry_data.entry_id in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_async_migrate_entry_v1_to_v2_scrubs_legacy_title(hass):
    """Test migrating entry from version 1 to version 2 scrubs legacy title and sets fingerprint."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.version = 1
    entry.unique_id = "legacy_raw_token_id"
    entry.title = "Realtime Trains API (abcde...)"
    entry.data = {
        CONF_REFRESH_TOKEN: "sample_refresh_token_secret",
    }
    hass.config_entries.async_update_entry = MagicMock()

    result = await async_migrate_entry(hass, entry)
    assert result is True
    hass.config_entries.async_update_entry.assert_called_once()
    assert hass.config_entries.async_update_entry.call_args.args[0] == entry
    call_kwargs = hass.config_entries.async_update_entry.call_args.kwargs
    assert call_kwargs["version"] == 2
    assert call_kwargs["unique_id"] == token_fingerprint("sample_refresh_token_secret")
    assert call_kwargs["title"] == "Realtime Trains API"
    assert "abcde" not in call_kwargs["title"]
    assert "sample_refresh" not in call_kwargs["unique_id"]


@pytest.mark.asyncio
async def test_async_migrate_entry_v1_to_v2_preserves_custom_title(hass):
    """Test migrating entry from version 1 to version 2 preserves custom user title."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id_2"
    entry.version = 1
    entry.unique_id = "legacy_raw_token_id"
    entry.title = "Realtime Trains API (Work)"
    entry.data = {
        CONF_REFRESH_TOKEN: "another_refresh_token_secret",
    }
    hass.config_entries.async_update_entry = MagicMock()

    result = await async_migrate_entry(hass, entry)
    assert result is True
    hass.config_entries.async_update_entry.assert_called_once()
    assert hass.config_entries.async_update_entry.call_args.args[0] == entry
    call_kwargs = hass.config_entries.async_update_entry.call_args.kwargs
    assert call_kwargs["version"] == 2
    assert call_kwargs["unique_id"] == token_fingerprint("another_refresh_token_secret")
    assert "title" not in call_kwargs


@pytest.mark.asyncio
async def test_async_migrate_entry_v2_noop(hass):
    """Test that version 2 entry is not modified."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id_3"
    entry.version = 2
    entry.unique_id = "already_fingerprinted_id"
    entry.title = "Realtime Trains API"
    entry.data = {
        CONF_REFRESH_TOKEN: "token",
    }
    hass.config_entries.async_update_entry = MagicMock()

    result = await async_migrate_entry(hass, entry)
    assert result is True
    assert entry.version == 2
    assert not hass.config_entries.async_update_entry.called
