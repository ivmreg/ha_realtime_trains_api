import asyncio
import inspect
import sys
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


class FakeBasicAuth:
    def __init__(self, login, password, encoding="utf-8"):
        self.login = login
        self.password = password
        self.encoding = encoding


def _install_module(name: str, package: bool = False) -> types.ModuleType:
    module = sys.modules.get(name)
    if not isinstance(module, types.ModuleType):
        module = types.ModuleType(name)
    if package:
        module.__path__ = []
    sys.modules[name] = module
    return module


class _HandlerRegistry:
    def register(self, domain):
        def decorator(cls):
            return cls

        return decorator


class _Throttle:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def __call__(self, func):
        return func


def _callback(func):
    return func


def _now():
    return datetime.now(timezone.utc)


homeassistant = _install_module("homeassistant", package=True)
exceptions = _install_module("homeassistant.exceptions")
config_entries = _install_module("homeassistant.config_entries")
core = _install_module("homeassistant.core")
helpers = _install_module("homeassistant.helpers", package=True)
typing_mod = _install_module("homeassistant.helpers.typing")
config_validation = _install_module("homeassistant.helpers.config_validation")
aiohttp_client = _install_module("homeassistant.helpers.aiohttp_client")
entity_platform = _install_module("homeassistant.helpers.entity_platform")
update_coordinator = _install_module("homeassistant.helpers.update_coordinator")
device_registry = _install_module("homeassistant.helpers.device_registry")
components = _install_module("homeassistant.components", package=True)
sensor = _install_module("homeassistant.components.sensor")
diagnostics = _install_module("homeassistant.components.diagnostics")
const = _install_module("homeassistant.const")
data_entry_flow = _install_module("homeassistant.data_entry_flow")
util = _install_module("homeassistant.util", package=True)
util_dt = _install_module("homeassistant.util.dt")
aiohttp = _install_module("aiohttp")

homeassistant.exceptions = exceptions
homeassistant.config_entries = config_entries
homeassistant.core = core
homeassistant.helpers = helpers
homeassistant.components = components
homeassistant.const = const
homeassistant.data_entry_flow = data_entry_flow
homeassistant.util = util

exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
update_coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})
update_coordinator.DataUpdateCoordinator = type("DataUpdateCoordinator", (object,), {"__class_getitem__": classmethod(lambda cls, item: cls), "__init__": lambda self, *args, **kwargs: None})
update_coordinator.CoordinatorEntity = type("CoordinatorEntity", (object,), {"__init__": lambda self, coordinator: None})
device_registry.DeviceInfo = type("DeviceInfo", (dict,), {})
device_registry.DeviceEntryType = type("DeviceEntryType", (object,), {"SERVICE": "service"})
diagnostics.async_redact_data = MagicMock(name="async_redact_data", side_effect=lambda data, to_redact: {k: "**REDACTED**" if k in to_redact else v for k, v in data.items()} if data else data)

config_entries.ConfigFlow = type("ConfigFlow", (object,), {})
config_entries.OptionsFlow = type("OptionsFlow", (object,), {})
config_entries.ConfigEntry = type("ConfigEntry", (object,), {})
config_entries.HANDLERS = _HandlerRegistry()

core.HomeAssistant = type("HomeAssistant", (object,), {})
core.callback = _callback

helpers.config_validation = config_validation
helpers.typing = typing_mod
helpers.aiohttp_client = aiohttp_client
helpers.entity_platform = entity_platform

typing_mod.ConfigType = dict
typing_mod.DiscoveryInfoType = dict

config_validation.string = MagicMock(name="string")
config_validation.positive_int = MagicMock(name="positive_int")
config_validation.positive_timedelta = MagicMock(name="positive_timedelta")
config_validation.boolean = MagicMock(name="boolean")
config_validation.time_period = MagicMock(name="time_period")

aiohttp_client.async_get_clientsession = MagicMock(name="async_get_clientsession")
entity_platform.AddEntitiesCallback = MagicMock(name="AddEntitiesCallback")

sensor.PLATFORM_SCHEMA = MagicMock(name="PLATFORM_SCHEMA")
sensor.SensorEntity = type("SensorEntity", (object,), {})

const.UnitOfTime = types.SimpleNamespace(MINUTES="min")
const.CONF_SCAN_INTERVAL = "scan_interval"

data_entry_flow.FlowResult = dict

util.Throttle = _Throttle
util_dt.now = _now

aiohttp.ClientSession = type("ClientSession", (object,), {})
aiohttp.BasicAuth = FakeBasicAuth


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as asyncio")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        try:
            previous_loop = asyncio.get_running_loop()
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
            if hasattr(loop, "shutdown_asyncgens"):
                loop.run_until_complete(loop.shutdown_asyncgens())
            if hasattr(loop, "shutdown_default_executor"):
                loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
            asyncio.set_event_loop(previous_loop)
        return True


@pytest.fixture
def hass():
    """Fixture for a mock HomeAssistant instance."""
    hass_mock = MagicMock()
    hass_mock.data = {}
    hass_mock.config_entries = MagicMock()

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
