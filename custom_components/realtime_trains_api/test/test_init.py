from unittest.mock import MagicMock, AsyncMock
import pytest
from custom_components.realtime_trains_api import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.realtime_trains_api.const import DOMAIN, PLATFORMS

@pytest.mark.asyncio
async def test_async_setup(hass):
    """Test the async_setup function."""
    config = {}
    result = await async_setup(hass, config)
    assert result is True
    assert DOMAIN in hass.data

@pytest.mark.asyncio
async def test_async_setup_entry(hass, config_entry):
    """Test setting up a config entry."""
    result = await async_setup_entry(hass, config_entry)
    assert result is True
    assert config_entry.entry_id in hass.data[DOMAIN]
    assert hass.config_entries.async_forward_entry_setups.called
    assert hass.config_entries.async_forward_entry_setups.call_args[0][1] == PLATFORMS

@pytest.mark.asyncio
async def test_async_unload_entry(hass, config_entry):
    """Test unloading a config entry."""
    # Setup first
    await async_setup_entry(hass, config_entry)
    assert config_entry.entry_id in hass.data[DOMAIN]

    # Mock unload success
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    result = await async_unload_entry(hass, config_entry)
    assert result is True
    assert config_entry.entry_id not in hass.data[DOMAIN]
    assert hass.config_entries.async_unload_platforms.called
    assert hass.config_entries.async_unload_platforms.call_args[0][1] == PLATFORMS

@pytest.mark.asyncio
async def test_async_unload_entry_fails(hass, config_entry):
    """Test unloading a config entry when it fails."""
    # Setup first
    await async_setup_entry(hass, config_entry)
    assert config_entry.entry_id in hass.data[DOMAIN]

    # Mock unload failure
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

    result = await async_unload_entry(hass, config_entry)
    assert result is False
    assert config_entry.entry_id in hass.data[DOMAIN]
