import re
from datetime import timedelta

DOMAIN = "realtime_trains_api"
PLATFORMS = ["sensor"]

CONF_API_TOKEN = "token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_QUERIES = "queries"
CONF_AUTOADJUSTSCANS = "auto_adjust_scans"

CONF_START = "origin"
CONF_END = "destination"
CONF_JOURNEYDATA = "journey_data_for_next_X_trains"
CONF_SENSORNAME = "sensor_name"
CONF_TIMEOFFSET = "time_offset"
CONF_PLATFORMS_OF_INTEREST = "platforms_of_interest"

CRS_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")

CONF_PEAK_INTERVAL = "peak_interval"
CONF_OFF_PEAK_INTERVAL = "off_peak_interval"
CONF_PEAK_WINDOWS = "peak_windows"

DEFAULT_PEAK_INTERVAL = 60
DEFAULT_OFF_PEAK_INTERVAL = 300
DEFAULT_PEAK_WINDOWS = "07:00-09:30, 16:00-19:00"
