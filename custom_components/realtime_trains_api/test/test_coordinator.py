from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun import freeze_time
import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.realtime_trains_api.coordinator import (
    RealtimeTrainsUpdateCoordinator,
    TIMEZONE,
)
from custom_components.realtime_trains_api.rtt_api import (
    RealtimeTrainsApiAuthError,
    RealtimeTrainsApiRateLimitError,
    RealtimeTrainsApiError,
)

@pytest.mark.asyncio
async def test_coordinator_auth_error():
    hass = MagicMock()
    api = MagicMock()
    api.fetch_location_services = AsyncMock(side_effect=RealtimeTrainsApiAuthError("Auth failed"))
    api.async_get_access_token = AsyncMock(side_effect=RealtimeTrainsApiAuthError("Auth failed"))
    
    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=hass,
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(minutes=1),
        api=api,
        queries=[{"origin": "WAL", "destination": "WAT"}]
    )
    
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

@pytest.mark.asyncio
async def test_coordinator_rate_limit_error():
    hass = MagicMock()
    api = MagicMock()
    api.fetch_location_services = AsyncMock(side_effect=RealtimeTrainsApiRateLimitError("Rate limit", retry_after=120))
    api.async_get_access_token = AsyncMock()
    
    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=hass,
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(seconds=30),
        api=api,
        queries=[{"origin": "WAL", "destination": "WAT"}]
    )
    coordinator.current_polling_interval = 30
    
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator.current_polling_interval == 120
    assert coordinator.update_interval == timedelta(seconds=120)

@pytest.mark.asyncio
async def test_coordinator_rate_limit_error_fallback_and_stale_data():
    hass = MagicMock()
    api = MagicMock()
    api.fetch_location_services = AsyncMock(side_effect=RealtimeTrainsApiRateLimitError("Rate limit", retry_after=None))
    api.async_get_access_token = AsyncMock()

    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=hass,
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(seconds=20),
        api=api,
        queries=[{"origin": "WAL", "destination": "WAT"}]
    )
    coordinator.current_polling_interval = 20
    coordinator.data = {"WAL_WAT_all_0": {"next_trains": []}}

    data = await coordinator._async_update_data()
    assert data == {"WAL_WAT_all_0": {"next_trains": []}}
    assert coordinator.data_stale is True
    assert coordinator.current_polling_interval == 60
    assert coordinator.update_interval == timedelta(seconds=60)

@pytest.mark.asyncio
async def test_coordinator_fetches_and_structures_data():
    hass = MagicMock()
    api = MagicMock()
    
    # Mock location services response
    api.fetch_location_services = AsyncMock(return_value={
        "services": [{
            "scheduleMetadata": {
                "identity": "123", 
                "inPassengerService": True,
                "departureDate": "2026-04-07"
            },
            "temporalData": {
                "departure": {
                    "scheduleAdvertised": "2026-04-07T12:05:00Z"
                }
            }
        }]
    })
    # Mock service details response
    api.fetch_service_details = AsyncMock(return_value={"service": {"locations": []}})
    
    # Mock token refresh
    api.async_get_access_token = AsyncMock(return_value="new_token")
    
    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=hass,
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(minutes=1),
        api=api,
        queries=[{"origin": "WAL", "destination": "WAT", "journey_data_for_next_X_trains": 1, "platforms_of_interest": [], "time_offset": timedelta()}]
    )
    
    with freeze_time("2026-04-07 12:00:00"):
        data = await coordinator._async_update_data()
        
    assert "WAL_WAT_all_0" in data
    assert len(data["WAL_WAT_all_0"]["next_trains"]) == 1
    train = data["WAL_WAT_all_0"]["next_trains"][0]
    assert "scheduled" in train
    assert "estimated" in train
    assert train["scheduled"].startswith("2026-04-07T12:05:00")
    assert train["scheduled_time"] == "12:05"
    assert "scheduled_iso" not in train
    assert "estimated_iso" not in train
    api.fetch_location_services.assert_called_once_with(
        "WAL",
        "WAT",
        date(2026, 4, 7),
        "1200",
        time_window=180,
    )

@pytest.mark.asyncio
async def test_coordinator_custom_lookback():
    hass = MagicMock()
    api = MagicMock()
    
    api.fetch_location_services = AsyncMock(return_value={"services": []})
    api.async_get_access_token = AsyncMock(return_value="new_token")
    
    coordinator = RealtimeTrainsUpdateCoordinator(
        hass=hass,
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(minutes=1),
        api=api,
        queries=[{"origin": "WAL", "destination": "WAT", "lookback_minutes": 30, "platforms_of_interest": [], "time_offset": timedelta()}]
    )
    
    with freeze_time("2026-04-07 12:00:00"):
        await coordinator._async_update_data()
        
    api.fetch_location_services.assert_called_once_with(
        "WAL",
        "WAT",
        date(2026, 4, 7),
        "1230",
        time_window=150,
    )

@pytest.mark.asyncio
async def test_coordinator_dynamic_interval():
    hass = MagicMock()
    logger = MagicMock()
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={"services": []})
    
    coordinator = RealtimeTrainsUpdateCoordinator(
        hass, logger, "test", timedelta(seconds=60), api, [{}]
    )
    coordinator.peak_interval = 60
    coordinator.off_peak_interval = 300
    coordinator.peak_windows = [(time(7, 0), time(9, 30))]
    
    # Test during peak
    mock_now_peak = datetime(2026, 4, 11, 8, 0, tzinfo=TIMEZONE)
    with patch("custom_components.realtime_trains_api.coordinator.dt_util.now", return_value=mock_now_peak):
        await coordinator._async_update_data()
        assert getattr(coordinator, "update_interval", None) == timedelta(seconds=60)
        assert coordinator.current_polling_interval == 60
        
    # Test during off-peak
    mock_now_off_peak = datetime(2026, 4, 11, 10, 0, tzinfo=TIMEZONE)
    with patch("custom_components.realtime_trains_api.coordinator.dt_util.now", return_value=mock_now_off_peak):
        await coordinator._async_update_data()
        assert getattr(coordinator, "update_interval", None) == timedelta(seconds=300)
        assert coordinator.current_polling_interval == 300


def _service(uid: str, sched: str = "2026-04-07T12:05:00Z") -> dict:
    return {
        "scheduleMetadata": {
            "identity": uid,
            "inPassengerService": True,
            "departureDate": "2026-04-07",
        },
        "temporalData": {"departure": {"scheduleAdvertised": sched}},
    }


def _make_coordinator(api, queries, **kwargs):
    return RealtimeTrainsUpdateCoordinator(
        hass=MagicMock(),
        logger=MagicMock(),
        name="test",
        update_interval=timedelta(minutes=1),
        api=api,
        queries=queries,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_journey_data_zero_still_lists_trains():
    """Regression: with journey data disabled, next_trains used to come back empty."""
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={
        "services": [_service("S1"), _service("S2", "2026-04-07T12:10:00Z"), _service("S3", "2026-04-07T12:15:00Z")]
    })
    api.fetch_service_details = AsyncMock()

    coordinator = _make_coordinator(api, [{"origin": "WAL", "destination": "WAT"}])

    with freeze_time("2026-04-07 12:00:00"):
        data = await coordinator._async_update_data()

    assert len(data["WAL_WAT_all_0"]["next_trains"]) == 3
    api.fetch_service_details.assert_not_called()
    assert data["WAL_WAT_all_0"]["state"] == 5


@pytest.mark.asyncio
async def test_journey_data_enriches_only_first_n():
    """journey_data_for_next_X_trains controls enrichment, max_trains list length."""
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={
        "services": [_service("S1"), _service("S2", "2026-04-07T12:10:00Z"), _service("S3", "2026-04-07T12:15:00Z")]
    })
    api.fetch_service_details = AsyncMock(return_value={"service": {"locations": []}})

    coordinator = _make_coordinator(
        api,
        [{"origin": "WAL", "destination": "WAT", "journey_data_for_next_X_trains": 1, "max_trains": 10}],
    )

    with freeze_time("2026-04-07 12:00:00"):
        data = await coordinator._async_update_data()

    assert len(data["WAL_WAT_all_0"]["next_trains"]) == 3
    assert api.fetch_service_details.call_count == 1


@pytest.mark.asyncio
async def test_journey_data_default_max_preserves_old_length():
    """Without an explicit max_trains, board length defaults to journey_data_count."""
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={
        "services": [_service("S1"), _service("S2", "2026-04-07T12:10:00Z"), _service("S3", "2026-04-07T12:15:00Z")]
    })
    api.fetch_service_details = AsyncMock(return_value={"service": {"locations": []}})

    coordinator = _make_coordinator(
        api,
        [{"origin": "WAL", "destination": "WAT", "journey_data_for_next_X_trains": 2}],
    )

    with freeze_time("2026-04-07 12:00:00"):
        data = await coordinator._async_update_data()

    assert len(data["WAL_WAT_all_0"]["next_trains"]) == 2
    assert api.fetch_service_details.call_count == 2


@pytest.mark.asyncio
async def test_max_trains_caps_list():
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={
        "services": [_service(f"S{i}", f"2026-04-07T12:{5 + i:02d}:00Z") for i in range(5)]
    })

    coordinator = _make_coordinator(
        api, [{"origin": "WAL", "destination": "WAT", "max_trains": 2}]
    )

    with freeze_time("2026-04-07 12:00:00"):
        data = await coordinator._async_update_data()

    assert len(data["WAL_WAT_all_0"]["next_trains"]) == 2


@pytest.mark.asyncio
async def test_auto_adjust_scans_backs_off_when_no_trains():
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={"services": []})

    coordinator = _make_coordinator(
        api,
        [{"origin": "WAL", "destination": "WAT"}],
        peak_interval=60,
        off_peak_interval=300,
        auto_adjust_scans=True,
    )

    with freeze_time("2026-04-07 12:00:00"):
        await coordinator._async_update_data()
    assert coordinator.current_polling_interval == 1800

    # Trains appear again -> interval returns to normal
    api.fetch_location_services = AsyncMock(return_value={"services": [_service("S1")]})
    with freeze_time("2026-04-07 12:00:00"):
        await coordinator._async_update_data()
    assert coordinator.current_polling_interval == 60


@pytest.mark.asyncio
async def test_stale_data_served_on_rate_limit():
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={"services": [_service("S1")]})

    coordinator = _make_coordinator(api, [{"origin": "WAL", "destination": "WAT"}])

    with freeze_time("2026-04-07 12:00:00"):
        good_data = await coordinator._async_update_data()
    coordinator.data = good_data  # normally done by DataUpdateCoordinator
    assert coordinator.data_stale is False

    api.fetch_location_services = AsyncMock(
        side_effect=RealtimeTrainsApiRateLimitError("Rate limit", retry_after=60)
    )
    with freeze_time("2026-04-07 12:01:00"):
        stale = await coordinator._async_update_data()

    assert stale is good_data
    assert coordinator.data_stale is True
    assert coordinator.last_successful_update is not None


@pytest.mark.asyncio
async def test_enrichment_error_keeps_state_numeric():
    """Journey-data failures surface via the error field, not the state."""
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={"services": [_service("S1")]})
    api.fetch_service_details = AsyncMock(
        side_effect=RealtimeTrainsApiRateLimitError("Rate limit", retry_after=60)
    )

    coordinator = _make_coordinator(
        api,
        [{"origin": "WAL", "destination": "WAT", "journey_data_for_next_X_trains": 1, "max_trains": 5}],
    )

    with freeze_time("2026-04-07 12:00:00"):
        data = await coordinator._async_update_data()

    result = data["WAL_WAT_all_0"]
    assert result["state"] == 5
    assert result["error"] == "Rate Limited"


@pytest.mark.asyncio
async def test_coordinator_journey_enrichment_canonical_contract():
    """Verify that journey enrichment populates contract v2 fields."""
    api = MagicMock()
    api.fetch_location_services = AsyncMock(return_value={
        "services": [{
            "scheduleMetadata": {
                "identity": "S123",
                "inPassengerService": True,
                "departureDate": "2026-04-07",
                "trainReportingIdentity": "2A10",
                "modeType": "TRAIN",
                "operator": {"name": "Southeastern"},
            },
            "locationMetadata": {
                "platform": {"actual": "2"},
                "numberOfVehicles": 8,
                "stockBranding": "City Beam",
            },
            "temporalData": {
                "departure": {
                    "scheduleAdvertised": "2026-04-07T12:10:00Z",
                    "realtimeForecast": "2026-04-07T12:15:00Z",
                }
            },
            "origin": [{"location": {"description": "London Cannon Street"}}],
            "destination": [{"location": {"description": "Dartford"}}],
        }]
    })

    api.fetch_service_details = AsyncMock(return_value={
        "service": {
            "reasons": [{"shortText": "Awaiting track inspection"}],
            "locations": [
                {
                    "location": {"shortCodes": ["CST"], "description": "London Cannon Street"},
                    "temporalData": {
                        "displayAs": "ORIGIN",
                        "departure": {"scheduleAdvertised": "2026-04-07T12:10:00Z", "realtimeActual": "2026-04-07T12:15:00Z"},
                    },
                },
                {
                    "location": {"shortCodes": ["LEW"], "description": "Lewisham"},
                    "temporalData": {
                        "displayAs": "CALL",
                        "arrival": {"scheduleAdvertised": "2026-04-07T12:25:00Z", "realtimeForecast": "2026-04-07T12:29:00Z"},
                    },
                },
                {
                    "location": {"shortCodes": ["DFD"], "description": "Dartford"},
                    "temporalData": {
                        "displayAs": "DEST",
                        "arrival": {"scheduleAdvertised": "2026-04-07T12:45:00Z", "realtimeForecast": "2026-04-07T12:50:00Z"},
                    },
                },
            ],
        }
    })

    coordinator = _make_coordinator(
        api,
        [{"origin": "CST", "destination": "DFD", "journey_data_for_next_X_trains": 1, "max_trains": 5}],
    )

    with freeze_time("2026-04-07 12:00:00"):
        data = await coordinator._async_update_data()

    query_res = data["CST_DFD_all_0"]
    assert len(query_res["next_trains"]) == 1
    train = query_res["next_trains"][0]

    # Origin timings & status
    assert train["scheduled_time"] == "12:10"
    assert train["estimated_time"] == "12:15"
    assert train["delay_minutes"] == 5
    assert train["status"] == "delayed"
    assert train["status_class"] == "delayed"
    assert train["status_label"] == "Exp 12:15"
    assert train["offset_label"] == "+5m"
    assert train["operator_name"] == "Southeastern"
    assert train["stock"] == "City Beam"

    # Destination arrival
    assert train["destination_arrival_time"] == "12:50"
    assert train["destination_status"] == "delayed"
    assert train["destination_delay_minutes"] == 5
    assert train["journey_duration_minutes"] == 35  # 12:50 - 12:15 = 35 mins
    assert train["stops_count"] == 1
    assert train["disruption_reason"] == "Awaiting track inspection"

    # Calling points (includes departed origin injected before first call)
    assert len(train["calling_points"]) == 3
    assert train["calling_points"][0]["crs"] == "CST"
    assert train["calling_points"][0]["is_passed"] is True
    assert train["calling_points"][1]["crs"] == "LEW"
    assert train["calling_points"][1]["station_name"] == "Lewisham"
    assert train["calling_points"][1]["time"] == "12:25"
    assert train["calling_points"][1]["status"] == "delayed"
    assert train["calling_points"][1]["status_label"] == "Exp 12:29"
    assert train["calling_points"][1]["is_between_previous"] is True
    assert train["calling_points"][2]["crs"] == "DFD"
    assert train["calling_points"][2]["station_name"] == "Dartford"
    assert train["calling_points"][2]["status_label"] == "Exp 12:50"

    for legacy_field in [
        "scheduled_iso",
        "estimated_iso",
        "subsequent_stops",
        "reason",
        "scheduled_arrival",
        "estimate_arrival",
        "scheduled_arrival_iso",
        "estimate_arrival_iso",
        "journey_time_mins",
        "stops",
        "last_report_time_iso",
    ]:
        assert legacy_field not in train


def test_build_train_field_type_normalization():
    now = datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc)
    scheduled_dt = datetime(2026, 4, 7, 12, 10, tzinfo=timezone.utc)
    estimated_dt = datetime(2026, 4, 7, 12, 10, tzinfo=timezone.utc)

    # All primitives None or uncoercible or non-string
    raw_departure = {
        "origin": [],
        "destination": [],
        "scheduleMetadata": {
            "identity": None,
            "trainReportingIdentity": 1234,  # non-string int
            "modeType": None,
            "operator": {"name": None},
        },
        "locationMetadata": {
            "platform": {"actual": "  "},  # whitespace
            "stockBranding": "   ",  # whitespace
            "numberOfVehicles": "invalid",  # non-int
        },
        "temporalData": {
            "departure": {
                "realtimeAdvertisedLateness": "not_an_int",
            }
        },
    }

    train = RealtimeTrainsUpdateCoordinator._build_train(
        raw_departure, scheduled_dt, estimated_dt, now, platform="  "
    )

    # Documented always-present primitive strings
    assert isinstance(train["origin_name"], str) and train["origin_name"] == ""
    assert isinstance(train["destination_name"], str) and train["destination_name"] == ""
    assert isinstance(train["service_uid"], str) and train["service_uid"] == ""
    assert isinstance(train["headcode"], str) and train["headcode"] == "1234"
    assert isinstance(train["type"], str) and train["type"] == ""
    assert isinstance(train["operator_name"], str) and train["operator_name"] == ""

    # Platform and stock should be string-or-null
    assert train["platform"] is None
    assert train["stock"] is None

    # Length and lateness integer-or-null
    assert train["length"] is None
    assert train["lateness"] is None

    # Test valid coercible values
    raw_valid = {
        "scheduleMetadata": {
            "identity": "P123",
            "trainReportingIdentity": "2A69",
            "modeType": "TRAIN",
            "operator": {"name": "Southeastern"},
        },
        "locationMetadata": {
            "stockBranding": "City Beam",
            "numberOfVehicles": "8",  # coercible string int
        },
        "temporalData": {
            "departure": {
                "realtimeAdvertisedLateness": "3",  # coercible string int
            }
        },
    }
    train_valid = RealtimeTrainsUpdateCoordinator._build_train(
        raw_valid, scheduled_dt, estimated_dt, now, platform=1
    )
    assert train_valid["platform"] == "1"
    assert train_valid["stock"] == "City Beam"
    assert train_valid["length"] == 8
    assert train_valid["lateness"] == 3
