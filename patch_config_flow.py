import re

with open('custom_components/realtime_trains_api/config_flow.py', 'r') as f:
    content = f.read()

# 1. Remove CONF_SCAN_INTERVAL from homeassistant.const import
content = re.sub(r'from homeassistant\.const import CONF_SCAN_INTERVAL\n', '', content)

# 2. Remove DEFAULT_SCAN_INTERVAL from .const import
content = re.sub(r'\s*DEFAULT_SCAN_INTERVAL,\n', '\n', content)

# 3. Remove coerce_scan_interval_seconds from .normalization import
content = content.replace(', coerce_scan_interval_seconds', '')

# 4. Remove MIN_SCAN_INTERVAL_SECONDS and MAX_SCAN_INTERVAL_SECONDS
content = re.sub(r'MIN_SCAN_INTERVAL_SECONDS = 30\n', '', content)
content = re.sub(r'MAX_SCAN_INTERVAL_SECONDS = 6 \* 3600\n', '', content)

# 5. Remove CONF_SCAN_INTERVAL from _user_schema
content = re.sub(r'\s*vol\.Optional\(\s*CONF_SCAN_INTERVAL,\s*default=int\(DEFAULT_SCAN_INTERVAL\.total_seconds\(\)\),\s*\):\s*vol\.All\(vol\.Coerce\(int\),\s*vol\.Range\(min=MIN_SCAN_INTERVAL_SECONDS,\s*max=MAX_SCAN_INTERVAL_SECONDS\)\),\n', '', content)

# 6. Remove user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
content = re.sub(r'\s*user_input\[CONF_SCAN_INTERVAL\] = int\(user_input\[CONF_SCAN_INTERVAL\]\)\n', '\n', content)

# 7. Remove scan_interval_value definition and self._scan_interval_default
content = re.sub(r'\s*scan_interval_value = config_entry\.options\.get\(\s*CONF_SCAN_INTERVAL,\s*config_entry\.data\.get\(\s*CONF_SCAN_INTERVAL,\s*int\(DEFAULT_SCAN_INTERVAL\.total_seconds\(\)\),\s*\),\s*\)\n', '', content)

content = re.sub(r'\s*self\._scan_interval_default = coerce_scan_interval_seconds\(\s*scan_interval_value,\s*DEFAULT_SCAN_INTERVAL,\s*MIN_SCAN_INTERVAL_SECONDS,\s*\)\n', '', content)

# 8. Remove self._options[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
content = re.sub(r'\s*self\._options\[CONF_SCAN_INTERVAL\] = int\(user_input\[CONF_SCAN_INTERVAL\]\)\n', '\n', content)

# 9. Remove CONF_SCAN_INTERVAL from _init_schema
content = re.sub(r'\s*vol\.Optional\(\s*CONF_SCAN_INTERVAL,\s*default=self\._scan_interval_default,\s*\):\s*vol\.All\(vol\.Coerce\(int\),\s*vol\.Range\(min=MIN_SCAN_INTERVAL_SECONDS,\s*max=MAX_SCAN_INTERVAL_SECONDS\)\),\n', '', content)

with open('custom_components/realtime_trains_api/config_flow.py', 'w') as f:
    f.write(content)
