"""Tests for the Pacific Power coordinator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.pacific_power.api import (
    AccountInfo,
    DailyUsage,
    HourlyUsage,
    PacificPowerApiError,
    PacificPowerAuthError,
    PacificPowerConnectionError,
)
from custom_components.pacific_power.const import CONF_COST_PER_KWH
from custom_components.pacific_power.coordinator import (
    CONSECUTIVE_FAILURES_FOR_REPAIR,
    INITIAL_HISTORY_DAYS,
    PacificPowerCoordinator,
    PacificPowerData,
    _make_stat_id,
    _slugify,
)

from .conftest import (
    _ConfigEntryAuthFailed,
    _UpdateFailed,
    mock_async_add_external_statistics,
    mock_async_create_issue,
    mock_async_delete_issue,
    mock_get_last_statistics,
    mock_statistics_during_period,
)


# ---- Helpers ----


def _make_coordinator(hass, entry):
    return PacificPowerCoordinator(hass, entry)


def _make_mock_api(
    accounts=None,
    is_ami=False,
    daily=None,
    hourly=None,
    auth_error=False,
    conn_error=False,
    api_error=False,
):
    api = AsyncMock()
    api.async_start = AsyncMock()
    api.async_stop = AsyncMock()
    if auth_error:
        api.async_login = AsyncMock(side_effect=PacificPowerAuthError("bad creds"))
    elif conn_error:
        api.async_login = AsyncMock(side_effect=PacificPowerConnectionError("timeout"))
    elif api_error:
        api.async_login = AsyncMock(side_effect=PacificPowerApiError("server error"))
    else:
        api.async_login = AsyncMock()
    api.async_get_user_info = AsyncMock(return_value={"webUserId": "user123"})
    api.async_get_accounts = AsyncMock(return_value=accounts or [])
    api.async_is_ami_meter = AsyncMock(return_value=is_ami)
    api.async_get_daily_usage = AsyncMock(return_value=daily or [])
    api.async_get_hourly_usage = AsyncMock(return_value=hourly or [])
    return api


# ---- Unit tests for helper functions ----


class TestSlugify:
    def test_lowercase(self):
        assert _slugify("ABC") == "abc"

    def test_replaces_special_chars(self):
        assert _slugify("a-b.c") == "a_b_c"

    def test_strips_leading_trailing_underscores(self):
        assert _slugify("__hello__") == "hello"

    def test_numbers_preserved(self):
        assert _slugify("12345_001") == "12345_001"


class TestMakeStatId:
    def test_basic(self, mock_account):
        result = _make_stat_id(mock_account)
        assert result == "pacific_power:12345_001_energy_consumption"

    def test_special_chars_in_idn(self):
        acct = AccountInfo(
            customer_idn="ABC-123",
            account_sequence="X.Y",
            agreement_sequence="01",
            address="test",
        )
        result = _make_stat_id(acct)
        assert result == "pacific_power:abc_123_x_y_energy_consumption"


# ---- Coordinator constructor ----


class TestCoordinatorInit:
    def test_sets_account_from_entry(self, mock_hass, mock_entry):
        coord = _make_coordinator(mock_hass, mock_entry)
        assert coord._account.customer_idn == "12345"
        assert coord._account.account_sequence == "001"
        assert coord._account.agreement_sequence == "01"
        assert coord._account.address == "123 Main St"

    def test_initial_state(self, mock_hass, mock_entry):
        coord = _make_coordinator(mock_hass, mock_entry)
        assert coord._last_data_received is None
        assert coord._consecutive_failures == 0
        assert coord.data is None


# ---- _async_update_data ----


class TestAsyncUpdateData:
    @pytest.mark.asyncio
    async def test_success_daily(self, mock_hass, mock_entry, mock_account):
        daily = [
            DailyUsage(date="2025-08-01", kwh=10.0),
            DailyUsage(date="2025-08-02", kwh=20.0),
        ]
        api = _make_mock_api(accounts=[mock_account], daily=daily)
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}

        coord = _make_coordinator(mock_hass, mock_entry)
        with patch(
            "custom_components.pacific_power.coordinator.PacificPowerApi",
            return_value=api,
        ):
            result = await coord._async_update_data()

        assert isinstance(result, PacificPowerData)
        assert result.account.customer_idn == "12345"
        assert result.last_updated is not None
        api.async_start.assert_awaited_once()
        api.async_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_hourly_ami(self, mock_hass, mock_entry, mock_account):
        hourly = [
            HourlyUsage(date="2025-08-01", time="0:00", kwh=1.0),
            HourlyUsage(date="2025-08-01", time="1:00", kwh=2.0),
        ]
        mock_account.site_idn = 100
        api = _make_mock_api(accounts=[mock_account], is_ami=True, hourly=hourly)
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}

        coord = _make_coordinator(mock_hass, mock_entry)
        with patch(
            "custom_components.pacific_power.coordinator.PacificPowerApi",
            return_value=api,
        ):
            result = await coord._async_update_data()

        assert isinstance(result, PacificPowerData)
        api.async_is_ami_meter.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auth_error_raises(self, mock_hass, mock_entry):
        api = _make_mock_api(auth_error=True)
        coord = _make_coordinator(mock_hass, mock_entry)
        with (
            patch(
                "custom_components.pacific_power.coordinator.PacificPowerApi",
                return_value=api,
            ),
            pytest.raises(_ConfigEntryAuthFailed),
        ):
            await coord._async_update_data()
        api.async_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connection_error_raises(self, mock_hass, mock_entry):
        api = _make_mock_api(conn_error=True)
        coord = _make_coordinator(mock_hass, mock_entry)
        with (
            patch(
                "custom_components.pacific_power.coordinator.PacificPowerApi",
                return_value=api,
            ),
            pytest.raises(_UpdateFailed),
        ):
            await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_hass, mock_entry):
        api = _make_mock_api(api_error=True)
        coord = _make_coordinator(mock_hass, mock_entry)
        with (
            patch(
                "custom_components.pacific_power.coordinator.PacificPowerApi",
                return_value=api,
            ),
            pytest.raises(_UpdateFailed),
        ):
            await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_always_stops_api_on_error(self, mock_hass, mock_entry):
        api = _make_mock_api(auth_error=True)
        coord = _make_coordinator(mock_hass, mock_entry)
        with (
            patch(
                "custom_components.pacific_power.coordinator.PacificPowerApi",
                return_value=api,
            ),
            pytest.raises(_ConfigEntryAuthFailed),
        ):
            await coord._async_update_data()
        api.async_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updates_site_idn_from_accounts(self, mock_hass, mock_entry, mock_account):
        fresh_account = AccountInfo(
            customer_idn="12345",
            account_sequence="001",
            agreement_sequence="01",
            address="123 Main St",
            site_idn=999,
            service_sequence=42,
        )
        api = _make_mock_api(accounts=[fresh_account])
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}

        coord = _make_coordinator(mock_hass, mock_entry)
        with patch(
            "custom_components.pacific_power.coordinator.PacificPowerApi",
            return_value=api,
        ):
            await coord._async_update_data()

        assert coord._account.site_idn == 999
        assert coord._account.service_sequence == 42


# ---- Consecutive failure tracking and repair issues ----


class TestRepairIssues:
    @pytest.mark.asyncio
    async def test_no_repair_before_threshold(self, mock_hass, mock_entry):
        api = _make_mock_api(conn_error=True)
        coord = _make_coordinator(mock_hass, mock_entry)
        mock_async_create_issue.reset_mock()

        for _ in range(CONSECUTIVE_FAILURES_FOR_REPAIR - 1):
            with (
                patch(
                    "custom_components.pacific_power.coordinator.PacificPowerApi",
                    return_value=_make_mock_api(conn_error=True),
                ),
                pytest.raises(_UpdateFailed),
            ):
                await coord._async_update_data()

        mock_async_create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_repair_created_at_threshold(self, mock_hass, mock_entry):
        coord = _make_coordinator(mock_hass, mock_entry)
        mock_async_create_issue.reset_mock()

        for _ in range(CONSECUTIVE_FAILURES_FOR_REPAIR):
            with (
                patch(
                    "custom_components.pacific_power.coordinator.PacificPowerApi",
                    return_value=_make_mock_api(conn_error=True),
                ),
                pytest.raises(_UpdateFailed),
            ):
                await coord._async_update_data()

        mock_async_create_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_repair_cleared_on_success(self, mock_hass, mock_entry, mock_account):
        coord = _make_coordinator(mock_hass, mock_entry)
        mock_async_delete_issue.reset_mock()
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}

        api = _make_mock_api(accounts=[mock_account])
        with patch(
            "custom_components.pacific_power.coordinator.PacificPowerApi",
            return_value=api,
        ):
            await coord._async_update_data()

        mock_async_delete_issue.assert_called_once()
        assert coord._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_failure_counter_increments(self, mock_hass, mock_entry):
        coord = _make_coordinator(mock_hass, mock_entry)
        for i in range(3):
            with (
                patch(
                    "custom_components.pacific_power.coordinator.PacificPowerApi",
                    return_value=_make_mock_api(conn_error=True),
                ),
                pytest.raises(_UpdateFailed),
            ):
                await coord._async_update_data()
        assert coord._consecutive_failures == 3


# ---- _fetch_daily ----


class TestFetchDaily:
    @pytest.mark.asyncio
    async def test_no_readings_returns_none(self, mock_hass, mock_entry):
        api = _make_mock_api()
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}
        coord = _make_coordinator(mock_hass, mock_entry)
        result = await coord._fetch_daily(api)
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_negative_kwh(self, mock_hass, mock_entry):
        readings = [
            DailyUsage(date="2025-08-01", kwh=10.0),
            DailyUsage(date="2025-08-02", kwh=-5.0),
            DailyUsage(date="2025-08-03", kwh=15.0),
        ]
        api = AsyncMock()
        api.async_get_daily_usage = AsyncMock(
            side_effect=[readings] + [[] for _ in range(12)]
        )
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}
        mock_async_add_external_statistics.reset_mock()

        coord = _make_coordinator(mock_hass, mock_entry)
        result = await coord._fetch_daily(api)

        assert result is not None
        call_args = mock_async_add_external_statistics.call_args
        stats = call_args[0][2]
        assert len(stats) == 2
        assert stats[0].state == 10.0
        assert stats[1].state == 15.0
        assert stats[1].sum == 25.0

    @pytest.mark.asyncio
    async def test_per_month_api_error_continues(self, mock_hass, mock_entry):
        """A single month's API error should not discard other months."""
        good_readings = [DailyUsage(date="2025-09-15", kwh=12.0)]
        api = AsyncMock()
        api.async_get_daily_usage = AsyncMock(
            side_effect=[
                PacificPowerApiError("server hiccup"),
                good_readings,
            ]
            + [[] for _ in range(12)]
        )
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}
        mock_async_add_external_statistics.reset_mock()

        coord = _make_coordinator(mock_hass, mock_entry)
        result = await coord._fetch_daily(api)

        assert result is not None
        call_args = mock_async_add_external_statistics.call_args
        stats = call_args[0][2]
        assert len(stats) == 1
        assert stats[0].state == 12.0

    @pytest.mark.asyncio
    async def test_shifts_dates_back_one_day(self, mock_hass, mock_entry):
        """usagePeriodEndDate is period-ending, so stats shift back one day."""
        readings = [DailyUsage(date="2025-08-02", kwh=10.0)]
        api = AsyncMock()
        api.async_get_daily_usage = AsyncMock(
            side_effect=[readings] + [[] for _ in range(12)]
        )
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}
        mock_async_add_external_statistics.reset_mock()

        coord = _make_coordinator(mock_hass, mock_entry)
        await coord._fetch_daily(api)

        stats = mock_async_add_external_statistics.call_args[0][2]
        assert stats[0].start.day == 1


# ---- _fetch_hourly ----


class TestFetchHourly:
    @pytest.mark.asyncio
    async def test_deduplicates_readings(self, mock_hass, mock_entry):
        api = AsyncMock()
        api.async_get_hourly_usage = AsyncMock(
            return_value=[
                HourlyUsage(date="2025-08-01", time="0:00", kwh=1.0),
                HourlyUsage(date="2025-08-01", time="0:00", kwh=1.5),
                HourlyUsage(date="2025-08-01", time="1:00", kwh=2.0),
            ]
        )
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}
        mock_async_add_external_statistics.reset_mock()

        coord = _make_coordinator(mock_hass, mock_entry)
        result = await coord._fetch_hourly(api)

        assert result is not None
        call_args = mock_async_add_external_statistics.call_args
        stats = call_args[0][2]
        assert len(stats) == 2
        assert stats[0].state == 1.5
        assert stats[0].sum == 1.5
        assert stats[1].state == 2.0
        assert stats[1].sum == 3.5

    @pytest.mark.asyncio
    async def test_no_readings_returns_none(self, mock_hass, mock_entry):
        api = AsyncMock()
        api.async_get_hourly_usage = AsyncMock(side_effect=PacificPowerApiError("none"))
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}

        coord = _make_coordinator(mock_hass, mock_entry)
        result = await coord._fetch_hourly(api)
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_negative_kwh(self, mock_hass, mock_entry):
        api = AsyncMock()
        api.async_get_hourly_usage = AsyncMock(
            return_value=[
                HourlyUsage(date="2025-08-01", time="0:00", kwh=1.0),
                HourlyUsage(date="2025-08-01", time="1:00", kwh=-0.5),
                HourlyUsage(date="2025-08-01", time="2:00", kwh=2.0),
            ]
        )
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}
        mock_async_add_external_statistics.reset_mock()

        coord = _make_coordinator(mock_hass, mock_entry)
        result = await coord._fetch_hourly(api)

        call_args = mock_async_add_external_statistics.call_args
        stats = call_args[0][2]
        assert len(stats) == 2
        assert stats[0].sum == 1.0
        assert stats[1].sum == 3.0

    @pytest.mark.asyncio
    async def test_shifts_times_back_one_hour(self, mock_hass, mock_entry):
        """readTime is hour-ending, so stats shift back one hour."""
        api = AsyncMock()
        api.async_get_hourly_usage = AsyncMock(
            return_value=[
                HourlyUsage(date="2025-08-01", time="1:00", kwh=3.0),
            ]
        )
        mock_get_last_statistics.return_value = {}
        mock_statistics_during_period.return_value = {}
        mock_async_add_external_statistics.reset_mock()

        coord = _make_coordinator(mock_hass, mock_entry)
        await coord._fetch_hourly(api)

        stats = mock_async_add_external_statistics.call_args[0][2]
        assert stats[0].start.hour == 0


# ---- _make_metadata ----


class TestMakeMetadata:
    def test_pacific_power(self, mock_hass, mock_entry):
        coord = _make_coordinator(mock_hass, mock_entry)
        meta = coord._make_metadata("pacific_power:test_stat")
        assert meta.name == "Pacific Power 123 Main St"
        assert meta.statistic_id == "pacific_power:test_stat"
        assert meta.unit_of_measurement == "kWh"

    def test_rocky_mountain(self, mock_hass, mock_entry):
        mock_entry.data["utility"] = "rocky_mountain_power"
        coord = _make_coordinator(mock_hass, mock_entry)
        meta = coord._make_metadata("pacific_power:test_stat")
        assert meta.name == "Rocky Mountain Power 123 Main St"


# ---- _get_last_stat ----


class TestFetchStart:
    @pytest.mark.asyncio
    async def test_no_existing_stats(self, mock_hass, mock_entry):
        mock_get_last_statistics.return_value = {}
        coord = _make_coordinator(mock_hass, mock_entry)
        start = await coord._fetch_start(30)
        assert (datetime.now(UTC) - start).days <= INITIAL_HISTORY_DAYS + 1

    @pytest.mark.asyncio
    async def test_with_existing_stats(self, mock_hass, mock_entry):
        ts = datetime(2025, 8, 1, 12, 0, tzinfo=UTC).timestamp()
        stat_id = "pacific_power:12345_001_energy_consumption"
        mock_get_last_statistics.return_value = {
            stat_id: [{"start": ts}]
        }
        coord = _make_coordinator(mock_hass, mock_entry)
        start = await coord._fetch_start(30)
        expected = datetime(2025, 8, 1, 12, 0, tzinfo=UTC) - timedelta(days=30)
        assert start == expected
        mock_get_last_statistics.return_value = {}


class TestSumBefore:
    @pytest.mark.asyncio
    async def test_no_existing_stats(self, mock_hass, mock_entry):
        mock_statistics_during_period.return_value = {}
        coord = _make_coordinator(mock_hass, mock_entry)
        result = await coord._sum_before("test_id", datetime(2025, 8, 1, tzinfo=UTC))
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_row_at_first_start(self, mock_hass, mock_entry):
        first = datetime(2025, 8, 1, tzinfo=UTC)
        mock_statistics_during_period.return_value = {
            "test_id": [{"start": first.timestamp(), "sum": 100.0, "state": 5.0}]
        }
        coord = _make_coordinator(mock_hass, mock_entry)
        result = await coord._sum_before("test_id", first)
        assert result == 95.0
        mock_statistics_during_period.return_value = {}

    @pytest.mark.asyncio
    async def test_row_before_first_start(self, mock_hass, mock_entry):
        first = datetime(2025, 8, 10, tzinfo=UTC)
        before = datetime(2025, 8, 9, tzinfo=UTC)
        call_count = [0]

        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {}
            return {"test_id": [{"start": before.timestamp(), "sum": 80.0}]}

        mock_statistics_during_period.side_effect = _side_effect
        coord = _make_coordinator(mock_hass, mock_entry)
        result = await coord._sum_before("test_id", first)
        assert result == 80.0
        mock_statistics_during_period.side_effect = None
        mock_statistics_during_period.return_value = {}


# ---- Cost statistics ----


class TestCostStatistics:
    def test_make_stat_id_with_kind(self, mock_account):
        assert _make_stat_id(mock_account, "energy_cost") == (
            "pacific_power:12345_001_energy_cost"
        )

    @pytest.mark.asyncio
    async def test_no_cost_stat_when_rate_unset(self, mock_hass, mock_entry):
        mock_statistics_during_period.return_value = {}
        mock_async_add_external_statistics.reset_mock()

        coord = _make_coordinator(mock_hass, mock_entry)
        await coord._insert_statistics([(datetime(2025, 8, 1, tzinfo=UTC), 10.0)])

        assert mock_async_add_external_statistics.call_count == 1

    @pytest.mark.asyncio
    async def test_no_cost_stat_when_rate_zero(self, mock_hass, mock_entry):
        mock_entry.options = {CONF_COST_PER_KWH: 0}
        mock_statistics_during_period.return_value = {}
        mock_async_add_external_statistics.reset_mock()

        coord = _make_coordinator(mock_hass, mock_entry)
        await coord._insert_statistics([(datetime(2025, 8, 1, tzinfo=UTC), 10.0)])

        assert mock_async_add_external_statistics.call_count == 1

    @pytest.mark.asyncio
    async def test_cost_stat_inserted_when_rate_set(self, mock_hass, mock_entry):
        mock_entry.options = {CONF_COST_PER_KWH: 0.25}
        mock_statistics_during_period.return_value = {}
        mock_async_add_external_statistics.reset_mock()

        coord = _make_coordinator(mock_hass, mock_entry)
        await coord._insert_statistics(
            [
                (datetime(2025, 8, 1, 0, tzinfo=UTC), 10.0),
                (datetime(2025, 8, 1, 1, tzinfo=UTC), 4.0),
            ]
        )

        assert mock_async_add_external_statistics.call_count == 2
        calls = mock_async_add_external_statistics.call_args_list
        usage_meta, usage_stats = calls[0][0][1], calls[0][0][2]
        cost_meta, cost_stats = calls[1][0][1], calls[1][0][2]

        assert usage_meta.statistic_id == "pacific_power:12345_001_energy_consumption"
        assert cost_meta.statistic_id == "pacific_power:12345_001_energy_cost"
        assert cost_meta.name == "Pacific Power 123 Main St Cost"
        assert cost_meta.has_sum is True
        assert cost_meta.unit_of_measurement is None
        assert [s.state for s in cost_stats] == [2.5, 1.0]
        assert [s.sum for s in cost_stats] == [2.5, 3.5]
        assert [s.start for s in cost_stats] == [s.start for s in usage_stats]

    @pytest.mark.asyncio
    async def test_cost_sum_seeded_from_its_own_series(self, mock_hass, mock_entry):
        """The cost sum continues the cost statistic, not the kWh one."""
        mock_entry.options = {CONF_COST_PER_KWH: 0.5}
        first = datetime(2025, 8, 1, tzinfo=UTC)

        def _lookup(hass, start, end, stat_ids, *args):
            stat_id = next(iter(stat_ids))
            if stat_id.endswith("_energy_cost"):
                row = {"start": first.timestamp(), "sum": 50.0, "state": 5.0}
            else:
                row = {"start": first.timestamp(), "sum": 100.0, "state": 10.0}
            return {stat_id: [row]}

        mock_statistics_during_period.side_effect = _lookup
        mock_async_add_external_statistics.reset_mock()

        coord = _make_coordinator(mock_hass, mock_entry)
        await coord._insert_statistics([(first, 10.0)])

        mock_statistics_during_period.side_effect = None
        mock_statistics_during_period.return_value = {}

        calls = mock_async_add_external_statistics.call_args_list
        assert calls[0][0][2][0].sum == 100.0  # (100 - 10) + 10
        assert calls[1][0][2][0].sum == 50.0  # (50 - 5) + 10 * 0.5
