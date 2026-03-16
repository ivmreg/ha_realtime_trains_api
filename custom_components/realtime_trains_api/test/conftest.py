import sys
from unittest.mock import MagicMock

# Mock homeassistant
ha = MagicMock()
sys.modules["homeassistant"] = ha
sys.modules["homeassistant.components"] = ha.components
sys.modules["homeassistant.components.sensor"] = ha.components.sensor
sys.modules["homeassistant.config_entries"] = ha.config_entries
sys.modules["homeassistant.const"] = ha.const
sys.modules["homeassistant.core"] = ha.core
sys.modules["homeassistant.helpers"] = ha.helpers
sys.modules["homeassistant.helpers.aiohttp_client"] = ha.helpers.aiohttp_client
sys.modules["homeassistant.helpers.config_validation"] = ha.helpers.config_validation
sys.modules["homeassistant.helpers.entity_platform"] = ha.helpers.entity_platform
sys.modules["homeassistant.helpers.typing"] = ha.helpers.typing
sys.modules["homeassistant.util"] = ha.util
sys.modules["homeassistant.util.dt"] = ha.util.dt

# Mock voluptuous
sys.modules["voluptuous"] = MagicMock()

# Mock pytz
sys.modules["pytz"] = MagicMock()

# Mock aiohttp
aiohttp = MagicMock()
sys.modules["aiohttp"] = aiohttp
