import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
import asyncio
import inspect


class FakeBasicAuth:
    def __init__(self, login, password, encoding="utf-8"):
        self.login = login
        self.password = password
        self.encoding = encoding


@pytest.fixture(autouse=True, scope="function")
def mock_external_modules(monkeypatch):
    """Mock Home Assistant and aiohttp modules for the test session."""
    # Mock Home Assistant-related modules
    mock_ha = MagicMock()
    monkeypatch.setitem(sys.modules, "homeassistant", mock_ha)
    monkeypatch.setitem(sys.modules, "homeassistant.config_entries", MagicMock())
    monkeypatch.setitem(sys.modules, "homeassistant.core", MagicMock())
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", MagicMock())
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.typing", MagicMock())

    # Mock aiohttp with a BasicAuth implementation
    mock_aiohttp = MagicMock()
    mock_aiohttp.BasicAuth = FakeBasicAuth
    monkeypatch.setitem(sys.modules, "aiohttp", mock_aiohttp)


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as asyncio")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        try:
            previous_loop = asyncio.get_event_loop()
        except RuntimeError:
            previous_loop = None

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            funcargs = pyfuncitem.funcargs
            testargs = {
                arg: funcargs[arg]
                for arg in pyfuncitem._fixtureinfo.argnames
                if arg in funcargs
            }
            loop.run_until_complete(pyfuncitem.obj(**testargs))
        finally:
            # Ensure the loop is cleanly shut down before closing
            if hasattr(loop, "shutdown_asyncgens"):
                loop.run_until_complete(loop.shutdown_asyncgens())
            if hasattr(loop, "shutdown_default_executor"):
                loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
            # Restore the previous event loop (or clear it if there was none)
            asyncio.set_event_loop(previous_loop)
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
