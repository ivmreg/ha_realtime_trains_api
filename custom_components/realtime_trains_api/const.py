import re
from datetime import timedelta

DOMAIN = "realtime_trains_api"
PLATFORMS = ["sensor"]

DEFAULT_SCAN_INTERVAL = timedelta(minutes=1)

CONF_API_TOKEN = "token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_QUERIES = "queries"
CONF_AUTOADJUSTSCANS = "auto_adjust_scans"

CONF_START = "origin"
CONF_END = "destination"
CONF_JOURNEYDATA = "journey_data_for_next_X_trains"
CONF_SENSORNAME = "sensor_name"
CONF_TIMEOFFSET = "time_offset"
CONF_STOPS_OF_INTEREST = "stops_of_interest"
CONF_PLATFORMS_OF_INTEREST = "platforms_of_interest"

CRS_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")
