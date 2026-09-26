import re
from datetime import timedelta

DOMAIN = "realtime_trains_api"
PLATFORMS = ["sensor", "binary_sensor"]

CONF_API_TOKEN = "token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_QUERIES = "queries"
CONF_AUTOADJUSTSCANS = "auto_adjust_scans"

CONF_START = "origin"
CONF_END = "destination"
CONF_JOURNEYDATA = "journey_data_for_next_X_trains"
CONF_MAXTRAINS = "max_trains"
CONF_SENSORNAME = "sensor_name"
CONF_TIMEOFFSET = "time_offset"
CONF_PLATFORMS_OF_INTEREST = "platforms_of_interest"
CONF_LOOKBACK = "lookback_minutes"
CONF_PINNED_DEPARTURE = "pinned_departure_time"

CRS_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")
HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# A pinned train counts as disrupted from this many minutes of delay
PINNED_DELAY_THRESHOLD_MINUTES = 5

CONF_PEAK_INTERVAL = "peak_interval"
CONF_OFF_PEAK_INTERVAL = "off_peak_interval"
CONF_PEAK_WINDOWS = "peak_windows"

DEFAULT_PEAK_INTERVAL = 60
DEFAULT_OFF_PEAK_INTERVAL = 300
DEFAULT_PEAK_WINDOWS = "07:00-09:30, 16:00-19:00"
DEFAULT_LOOKBACK_MINUTES = 60
DEFAULT_MAX_TRAINS = 10

# Polling backoff applied when auto_adjust_scans is enabled and a station
# currently has no departures at all.
NO_TRAINS_BACKOFF_SECONDS = 1800

# Canonical contract version
CONTRACT_VERSION = 2
ATTR_CONTRACT_VERSION = "contract_version"

# Disruption credentials
CONF_OPENLDBWS_TOKEN = "openldbws_token"
CONF_KB_USERNAME = "kb_username"
CONF_KB_PASSWORD = "kb_password"

# Service status enum
SERVICE_STATUS_NORMAL = "normal"
SERVICE_STATUS_DELAYED = "delayed"
SERVICE_STATUS_DISRUPTED = "disrupted"
SERVICE_STATUS_ENGINEERING_WORK = "engineering_work"
SERVICE_STATUS_STATION_CLOSED = "station_closed"
SERVICE_STATUS_NO_DEPARTURES = "no_departures"

SERVICE_STATUS_ALL = {
    SERVICE_STATUS_NORMAL,
    SERVICE_STATUS_DELAYED,
    SERVICE_STATUS_DISRUPTED,
    SERVICE_STATUS_ENGINEERING_WORK,
    SERVICE_STATUS_STATION_CLOSED,
    SERVICE_STATUS_NO_DEPARTURES,
}

# Sensor attributes
ATTR_SERVICE_STATUS = "service_status"
ATTR_STATION_MESSAGES = "station_messages"
ATTR_DISRUPTIONS = "disruptions"

# Disruption cache TTL (15 minutes)
DEFAULT_DISRUPTION_CACHE_SECONDS = 900
