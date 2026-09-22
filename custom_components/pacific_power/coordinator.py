"""DataUpdateCoordinator for Pacific Power energy data."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import (
    AccountInfo,
    DailyUsage,
    HourlyUsage,
    PacificPowerApi,
    PacificPowerApiError,
    PacificPowerAuthError,
    PacificPowerConnectionError,
)
from .const import (
    CONF_ACCOUNT_SEQUENCE,
    CONF_AGREEMENT_SEQUENCE,
    CONF_COST_PER_KWH,
    CONF_CUSTOMER_IDN,
    CONF_SERVICE_ADDRESS,
    CONF_TIMEZONE,
    CONF_UTILITY,
    DOMAIN,
    UTILITY_DOMAINS,
    UTILITY_PACIFIC_POWER,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(hours=12)
OVERLAP_DAYS_DAILY = 30
OVERLAP_DAYS_HOURLY = 3
INITIAL_HISTORY_DAYS = 30
CONSECUTIVE_FAILURES_FOR_REPAIR = 3

_STAT_ID_RE = re.compile(r"[^a-z0-9_]")

PacificPowerConfigEntry = ConfigEntry["PacificPowerCoordinator"]


@dataclass
class PacificPowerData:
    """Data returned by the coordinator."""

    account: AccountInfo
    last_data_received: datetime | None
    last_updated: datetime


def _slugify(value: str) -> str:
    return _STAT_ID_RE.sub("_", value.lower()).strip("_")


def _make_stat_id(account: AccountInfo, kind: str = "energy_consumption") -> str:
    slug = _slugify(f"{account.customer_idn}_{account.account_sequence}")
    return f"{DOMAIN}:{slug}_{kind}"


class PacificPowerCoordinator(DataUpdateCoordinator[PacificPowerData]):
    """Fetches energy data and inserts HA statistics."""

    config_entry: PacificPowerConfigEntry

    def __init__(self, hass: Any, entry: PacificPowerConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self._entry = entry
        self._last_data_received: datetime | None = None
        self._consecutive_failures: int = 0
        self._account = AccountInfo(
            customer_idn=entry.data[CONF_CUSTOMER_IDN],
            account_sequence=entry.data[CONF_ACCOUNT_SEQUENCE],
            agreement_sequence=entry.data[CONF_AGREEMENT_SEQUENCE],
            address=entry.data[CONF_SERVICE_ADDRESS],
        )

    async def _async_update_data(self) -> PacificPowerData:
        api = PacificPowerApi(
            username=self._entry.data[CONF_USERNAME],
            password=self._entry.data[CONF_PASSWORD],
            utility=self._entry.data.get(CONF_UTILITY, UTILITY_PACIFIC_POWER),
        )
        try:
            await api.async_start()
            await api.async_login()

            user = await api.async_get_user_info()
            accounts = await api.async_get_accounts(user.get("webUserId", ""))

            for acct in accounts:
                if (
                    acct.customer_idn == self._account.customer_idn
                    and acct.account_sequence == self._account.account_sequence
                ):
                    self._account.site_idn = acct.site_idn
                    self._account.service_sequence = acct.service_sequence
                    break

            is_ami = await api.async_is_ami_meter(self._account)
            _LOGGER.debug("AMI meter: %s", is_ami)

            if is_ami and self._account.site_idn:
                new_data_ts = await self._fetch_hourly(api)
            else:
                new_data_ts = await self._fetch_daily(api)
        except PacificPowerAuthError as err:
            self._consecutive_failures += 1
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except PacificPowerConnectionError as err:
            self._consecutive_failures += 1
            self._maybe_create_repair(str(err))
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="connection_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except PacificPowerApiError as err:
            self._consecutive_failures += 1
            self._maybe_create_repair(str(err))
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            await api.async_stop()

        self._consecutive_failures = 0
        async_delete_issue(
            self.hass,
            DOMAIN,
            f"persistent_failure_{self._entry.entry_id}",
        )

        if new_data_ts:
            self._last_data_received = new_data_ts
        now = datetime.now(UTC)
        return PacificPowerData(
            account=self._account,
            last_data_received=self._last_data_received,
            last_updated=now,
        )

    async def _fetch_hourly(self, api: PacificPowerApi) -> datetime | None:
        """Fetch hour-by-hour data for AMI meters."""
        start = await self._fetch_start(OVERLAP_DAYS_HOURLY)

        tz = ZoneInfo(self._entry.data.get(CONF_TIMEZONE, self.hass.config.time_zone))
        now = datetime.now(UTC)
        cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
        all_readings: list[HourlyUsage] = []

        while cursor.date() <= now.date():
            try:
                readings = await api.async_get_hourly_usage(self._account, cursor)
                all_readings.extend(readings)
            except PacificPowerApiError:
                _LOGGER.debug("No hourly data for %s", cursor.date())
            cursor += timedelta(days=1)

        deduped: dict[tuple[str, str], HourlyUsage] = {}
        for reading in all_readings:
            deduped[(reading.date, reading.time)] = reading

        normalized: list[tuple[datetime, float]] = []
        for reading in deduped.values():
            if reading.kwh < 0:
                continue
            # readTime is hour-ending: "01:00" covers 00:00-01:00
            hour = int(reading.time.split(":")[0])
            start_dt = datetime.strptime(reading.date, "%Y-%m-%d").replace(
                tzinfo=tz
            ) + timedelta(hours=hour - 1)
            normalized.append((start_dt, reading.kwh))

        return await self._insert_statistics(normalized)

    async def _fetch_daily(self, api: PacificPowerApi) -> datetime | None:
        """Fetch day-by-day data for non-AMI meters."""
        start = await self._fetch_start(OVERLAP_DAYS_DAILY)

        tz = ZoneInfo(self._entry.data.get(CONF_TIMEZONE, self.hass.config.time_zone))
        now = datetime.now(UTC)
        all_readings: list[DailyUsage] = []
        cursor = start.replace(day=1)
        while cursor <= now:
            month_end = (cursor.replace(day=28) + timedelta(days=4)).replace(
                day=1
            ) - timedelta(days=1)
            if month_end > now:
                month_end = now
            try:
                readings = await api.async_get_daily_usage(
                    self._account, cursor, month_end
                )
                all_readings.extend(readings)
            except PacificPowerApiError:
                _LOGGER.debug("No daily data for %s", cursor.strftime("%Y-%m"))
            cursor = month_end + timedelta(days=1)

        deduped: dict[str, DailyUsage] = {}
        for reading in all_readings:
            deduped[reading.date] = reading

        normalized: list[tuple[datetime, float]] = []
        for reading in deduped.values():
            if reading.kwh < 0:
                continue
            # usagePeriodEndDate is period-ending; shift back one day
            start_dt = datetime.strptime(reading.date, "%Y-%m-%d").replace(
                tzinfo=tz
            ) - timedelta(days=1)
            normalized.append((start_dt, reading.kwh))

        return await self._insert_statistics(normalized)

    async def _fetch_start(self, overlap_days: int) -> datetime:
        """Return the datetime to start fetching from."""
        stat_id = _make_stat_id(self._account)
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, stat_id, False, {"start"}
        )
        if last_stats and stat_id in last_stats:
            last_ts = datetime.fromtimestamp(last_stats[stat_id][0]["start"], tz=UTC)
            return last_ts - timedelta(days=overlap_days)
        return datetime.now(UTC) - timedelta(days=INITIAL_HISTORY_DAYS)

    async def _insert_statistics(
        self, readings: list[tuple[datetime, float]]
    ) -> datetime | None:
        """Build and insert usage (and optional cost) statistics from readings."""
        if not readings:
            _LOGGER.debug("No usage data returned")
            return None

        readings.sort(key=lambda r: r[0])
        first_start = readings[0][0]

        stat_id = _make_stat_id(self._account)
        running_sum = await self._sum_before(stat_id, first_start)

        statistics: list[StatisticData] = []
        for start_dt, kwh in readings:
            running_sum += kwh
            statistics.append(StatisticData(start=start_dt, state=kwh, sum=running_sum))

        async_add_external_statistics(
            self.hass, self._make_metadata(stat_id), statistics
        )
        _LOGGER.debug("Inserted %d statistics for %s", len(statistics), stat_id)

        rate = float(self._entry.options.get(CONF_COST_PER_KWH, 0) or 0)
        if rate > 0:
            cost_id = _make_stat_id(self._account, "energy_cost")
            cost_sum = await self._sum_before(cost_id, first_start)
            cost_statistics: list[StatisticData] = []
            for start_dt, kwh in readings:
                cost = kwh * rate
                cost_sum += cost
                cost_statistics.append(
                    StatisticData(start=start_dt, state=cost, sum=cost_sum)
                )
            async_add_external_statistics(
                self.hass, self._make_cost_metadata(cost_id), cost_statistics
            )
            _LOGGER.debug(
                "Inserted %d statistics for %s", len(cost_statistics), cost_id
            )

        return readings[-1][0]

    async def _sum_before(self, stat_id: str, first_start: datetime) -> float:
        """Return the cumulative sum just before first_start.

        Seeds the running sum from the row immediately preceding the
        first re-inserted timestamp, not from the newest row — otherwise
        the overlap window's usage would be double-counted on every refresh.
        """

        def _lookup() -> float:
            rows = statistics_during_period(
                self.hass,
                first_start,
                first_start + timedelta(hours=1),
                {stat_id},
                "hour",
                None,
                {"state", "sum"},
            ).get(stat_id)
            if rows and abs(rows[0]["start"] - first_start.timestamp()) < 1:
                return (rows[0].get("sum") or 0.0) - (rows[0].get("state") or 0.0)

            rows = statistics_during_period(
                self.hass,
                first_start - timedelta(days=31),
                first_start,
                {stat_id},
                "hour",
                None,
                {"sum"},
            ).get(stat_id)
            if rows:
                return rows[-1].get("sum") or 0.0
            return 0.0

        return await get_instance(self.hass).async_add_executor_job(_lookup)

    def _maybe_create_repair(self, error: str) -> None:
        if self._consecutive_failures < CONSECUTIVE_FAILURES_FOR_REPAIR:
            return
        async_create_issue(
            self.hass,
            DOMAIN,
            f"persistent_failure_{self._entry.entry_id}",
            is_fixable=False,
            severity=IssueSeverity.ERROR,
            translation_key="persistent_failure",
            translation_placeholders={
                "name": self._entry.title,
                "error": error,
                "count": str(self._consecutive_failures),
            },
        )

    def _make_metadata(self, stat_id: str) -> StatisticMetaData:
        utility = self._entry.data.get(CONF_UTILITY, UTILITY_PACIFIC_POWER)
        utility_name = UTILITY_DOMAINS[utility]["name"]
        return StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{utility_name} {self._account.address}",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class="energy",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )

    def _make_cost_metadata(self, stat_id: str) -> StatisticMetaData:
        utility = self._entry.data.get(CONF_UTILITY, UTILITY_PACIFIC_POWER)
        utility_name = UTILITY_DOMAINS[utility]["name"]
        return StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{utility_name} {self._account.address} Cost",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class=None,
            unit_of_measurement=None,
        )
