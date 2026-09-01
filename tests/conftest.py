"""Shared test fixtures for Pacific Power integration tests.

Stubs out homeassistant and its heavy transitive dependencies so tests
run without a full HA installation.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stub registry — all stubs must be installed before integration imports.
# ---------------------------------------------------------------------------

def _mod(name: str, **attrs: object) -> ModuleType:
    """Create a fake module and register it in sys.modules."""
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# -- Fake HA exception classes --
class _ConfigEntryAuthFailed(Exception):
    def __init__(self, *a, **kw):
        self.translation_domain = kw.get("translation_domain")
        self.translation_key = kw.get("translation_key")
        self.translation_placeholders = kw.get("translation_placeholders")
        super().__init__(*a)


class _UpdateFailed(Exception):
    def __init__(self, *a, **kw):
        self.translation_domain = kw.get("translation_domain")
        self.translation_key = kw.get("translation_key")
        self.translation_placeholders = kw.get("translation_placeholders")
        super().__init__(*a)


# -- Fake base classes --
class _DataUpdateCoordinator:
    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, update_interval):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data = None

    async def async_config_entry_first_refresh(self):
        self.data = await self._async_update_data()


class _CoordinatorEntity:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator


class _ConfigFlow:
    def __init_subclass__(cls, domain=None, **kw):
        super().__init_subclass__(**kw)


class _ConfigEntry:
    def __class_getitem__(cls, item):
        return cls


class _SensorEntity:
    pass


from dataclasses import dataclass as _dc


@_dc(frozen=True)
class _SensorEntityDescription:
    key: str = ""
    translation_key: str | None = None
    device_class: str | None = None
    entity_category: str | None = None
    name: str | None = None


class _SensorDeviceClass:
    TIMESTAMP = "timestamp"


class _EntityCategory:
    DIAGNOSTIC = "diagnostic"


class _DeviceEntryType:
    SERVICE = "service"


class _Platform:
    SENSOR = "sensor"


class _UnitOfEnergy:
    KILO_WATT_HOUR = "kWh"


class _StatisticMeanType:
    NONE = "none"


class _StatisticData:
    def __init__(self, *, start, state, sum):
        self.start = start
        self.state = state
        self.sum = sum


class _StatisticMetaData:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _IssueSeverity:
    ERROR = "error"
    WARNING = "warning"


def _fake_redact(data, to_redact):
    if isinstance(data, dict):
        return {
            k: "**REDACTED**" if k in to_redact else _fake_redact(v, to_redact)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_fake_redact(i, to_redact) for i in data]
    return data


# -- Global mocks we inspect in tests --
mock_async_add_external_statistics = MagicMock()
mock_get_last_statistics = MagicMock(return_value={})
mock_statistics_during_period = MagicMock(return_value={})
mock_async_create_issue = MagicMock()
mock_async_delete_issue = MagicMock()


# -- Register all stubs --

# homeassistant core
_mod("homeassistant")
_mod("homeassistant.core", HomeAssistant=MagicMock)
_mod("homeassistant.const",
     CONF_PASSWORD="password", CONF_USERNAME="username",
     Platform=_Platform, UnitOfEnergy=_UnitOfEnergy,
     EntityCategory=_EntityCategory)
_mod("homeassistant.config_entries",
     ConfigEntry=_ConfigEntry, ConfigFlow=_ConfigFlow,
     ConfigFlowResult=dict)
_mod("homeassistant.data_entry_flow")
_mod("homeassistant.exceptions",
     ConfigEntryAuthFailed=_ConfigEntryAuthFailed,
     ConfigEntryError=Exception, ConfigEntryNotReady=Exception,
     HomeAssistantError=Exception)

# homeassistant.components
_mod("homeassistant.components")
_mod("homeassistant.components.recorder")
_mod("homeassistant.components.recorder.models",
     StatisticData=_StatisticData, StatisticMeanType=_StatisticMeanType,
     StatisticMetaData=_StatisticMetaData)
_mod("homeassistant.components.recorder.statistics",
     async_add_external_statistics=mock_async_add_external_statistics,
     get_last_statistics=mock_get_last_statistics,
     statistics_during_period=mock_statistics_during_period)
_mod("homeassistant.components.sensor",
     SensorDeviceClass=_SensorDeviceClass, SensorEntity=_SensorEntity,
     SensorEntityDescription=_SensorEntityDescription)
_mod("homeassistant.components.diagnostics",
     async_redact_data=_fake_redact)

# homeassistant.helpers
_mod("homeassistant.helpers")
_mod("homeassistant.helpers.device_registry",
     DeviceEntryType=_DeviceEntryType, DeviceInfo=dict,
     DeviceEntry=MagicMock)
_mod("homeassistant.helpers.entity_platform",
     AddEntitiesCallback=MagicMock)
_mod("homeassistant.helpers.issue_registry",
     IssueSeverity=_IssueSeverity,
     async_create_issue=mock_async_create_issue,
     async_delete_issue=mock_async_delete_issue)
_mod("homeassistant.helpers.update_coordinator",
     DataUpdateCoordinator=_DataUpdateCoordinator,
     UpdateFailed=_UpdateFailed,
     CoordinatorEntity=_CoordinatorEntity)

# aiohttp and cryptography are real installed packages — do NOT stub them.
# They are needed by test_api.py for actual crypto round-trip tests.

# ---------------------------------------------------------------------------
# NOW we can import integration code.
# ---------------------------------------------------------------------------
from custom_components.pacific_power.api import AccountInfo, DailyUsage, HourlyUsage  # noqa: E402
from custom_components.pacific_power.const import (  # noqa: E402
    CONF_ACCOUNT_SEQUENCE,
    CONF_AGREEMENT_SEQUENCE,
    CONF_CUSTOMER_IDN,
    CONF_SERVICE_ADDRESS,
    CONF_TIMEZONE,
    CONF_UTILITY,
    UTILITY_PACIFIC_POWER,
)
from custom_components.pacific_power.coordinator import PacificPowerData  # noqa: E402


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

MOCK_USERNAME = "user@example.com"
MOCK_PASSWORD = "s3cret"
MOCK_WEB_USER_ID = "web-user-123"
MOCK_TIMEZONE = "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_account() -> AccountInfo:
    return AccountInfo(
        customer_idn="12345",
        account_sequence="001",
        agreement_sequence="01",
        address="123 Main St",
        site_idn=100,
        service_sequence=1,
    )


@pytest.fixture
def mock_account_2() -> AccountInfo:
    return AccountInfo(
        customer_idn="67890",
        account_sequence="002",
        agreement_sequence="02",
        address="456 Oak Ave",
        site_idn=200,
        service_sequence=2,
    )


@pytest.fixture
def mock_accounts(mock_account: AccountInfo, mock_account_2: AccountInfo) -> list[AccountInfo]:
    return [mock_account, mock_account_2]


@pytest.fixture
def mock_api(mock_account: AccountInfo) -> AsyncMock:
    api = AsyncMock()
    api.async_start = AsyncMock()
    api.async_stop = AsyncMock()
    api.async_login = AsyncMock()
    api.async_get_user_info = AsyncMock(return_value={"webUserId": MOCK_WEB_USER_ID})
    api.async_get_accounts = AsyncMock(return_value=[mock_account])
    api.async_is_ami_meter = AsyncMock(return_value=False)
    api.async_get_daily_usage = AsyncMock(return_value=[])
    api.async_get_hourly_usage = AsyncMock(return_value=[])
    return api


@pytest.fixture
def mock_entry_data(mock_account: AccountInfo) -> dict:
    return {
        CONF_UTILITY: UTILITY_PACIFIC_POWER,
        "username": MOCK_USERNAME,
        "password": MOCK_PASSWORD,
        CONF_CUSTOMER_IDN: mock_account.customer_idn,
        CONF_ACCOUNT_SEQUENCE: mock_account.account_sequence,
        CONF_AGREEMENT_SEQUENCE: mock_account.agreement_sequence,
        CONF_SERVICE_ADDRESS: mock_account.address,
        CONF_TIMEZONE: MOCK_TIMEZONE,
    }


@pytest.fixture
def mock_entry(mock_entry_data: dict) -> MagicMock:
    entry = MagicMock()
    entry.data = mock_entry_data
    entry.entry_id = "test_entry_id_123"
    entry.title = "Pacific Power (123 Main St)"
    entry.as_dict.return_value = {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "data": mock_entry_data,
    }
    entry.runtime_data = None
    return entry


@pytest.fixture
def mock_hass() -> MagicMock:
    hass = MagicMock()
    hass.config.time_zone = MOCK_TIMEZONE

    async def _run_executor(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=_run_executor)
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_get_entry = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    return hass


@pytest.fixture
def sample_daily_readings() -> list[DailyUsage]:
    return [
        DailyUsage(date="2025-08-01", kwh=15.5),
        DailyUsage(date="2025-08-02", kwh=22.3),
        DailyUsage(date="2025-08-03", kwh=18.1),
    ]


@pytest.fixture
def sample_hourly_readings() -> list[HourlyUsage]:
    return [
        HourlyUsage(date="2025-08-01", time="0:00", kwh=1.2),
        HourlyUsage(date="2025-08-01", time="1:00", kwh=0.8),
        HourlyUsage(date="2025-08-01", time="2:00", kwh=0.5),
        HourlyUsage(date="2025-08-01", time="3:00", kwh=1.1),
    ]


@pytest.fixture
def sample_data(mock_account: AccountInfo) -> PacificPowerData:
    return PacificPowerData(
        account=mock_account,
        last_data_received=datetime(2025, 8, 3, 12, 0, tzinfo=UTC),
        last_updated=datetime(2025, 8, 3, 14, 0, tzinfo=UTC),
    )
