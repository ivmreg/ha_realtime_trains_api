import sys
from unittest.mock import AsyncMock, MagicMock

# Mock Home Assistant and aiohttp modules
mock_ha = MagicMock()
sys.modules["homeassistant"] = mock_ha
sys.modules["homeassistant.config_entries"] = MagicMock()
sys.modules["homeassistant.core"] = MagicMock()
sys.modules["homeassistant.helpers"] = MagicMock()
sys.modules["homeassistant.helpers.typing"] = MagicMock()

mock_aiohttp = MagicMock()
class FakeBasicAuth:
    def __init__(self, login, password, encoding="utf-8"):
        self.login = login
        self.password = password
        self.encoding = encoding

mock_aiohttp.BasicAuth = FakeBasicAuth
sys.modules["aiohttp"] = mock_aiohttp

import pytest
import asyncio
import inspect

def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as asyncio")

@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            funcargs = pyfuncitem.funcargs
            testargs = {arg: funcargs[arg] for arg in pyfuncitem._fixtureinfo.argnames if arg in funcargs}
            loop.run_until_complete(pyfuncitem.obj(**testargs))
        finally:
            loop.close()
        return True

@pytest.fixture
def hass():
    """Fixture for a mock HomeAssistant instance."""
    hass_mock = MagicMock()
    hass_mock.data = {}
    hass_mock.config_entries = MagicMock()

    # Use AsyncMock for methods that are awaited
    hass_mock.config_entries.async_forward_entry_setups = AsyncMock()
    hass_mock.config_entries.async_unload_platforms = AsyncMock()

    return hass_mock

@pytest.fixture
def config_entry():
    """Fixture for a mock ConfigEntry."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {}
    return entry
