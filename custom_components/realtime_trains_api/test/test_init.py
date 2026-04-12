from unittest.mock import AsyncMock
import pytest
from custom_components.realtime_trains_api import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.realtime_trains_api.const import DOMAIN, PLATFORMS, CONF_PEAK_INTERVAL, DEFAULT_PEAK_INTERVAL, CONF_OFF_PEAK_INTERVAL, DEFAULT_OFF_PEAK_INTERVAL, CONF_PEAK_WINDOWS, DEFAULT_PEAK_WINDOWS

@pytest.fixture
def mock_config_entry_data(config_entry):
    config_entry.data = {
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
