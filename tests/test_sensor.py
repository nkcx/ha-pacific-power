"""Tests for the Pacific Power sensor entities."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.pacific_power.api import AccountInfo
from custom_components.pacific_power.coordinator import PacificPowerData
from custom_components.pacific_power.sensor import (
    PARALLEL_UPDATES,
    SENSOR_DESCRIPTIONS,
    PacificPowerSensor,
    async_setup_entry,
)


class TestParallelUpdates:
    def test_is_zero(self):
        assert PARALLEL_UPDATES == 0


class TestSensorDescriptions:
    def test_two_descriptions(self):
        assert len(SENSOR_DESCRIPTIONS) == 2

    def test_last_data_received(self):
        desc = SENSOR_DESCRIPTIONS[0]
        assert desc.key == "last_data_received"
        assert desc.translation_key == "last_data_received"
        assert desc.device_class == "timestamp"
        assert desc.entity_category == "diagnostic"

    def test_last_updated(self):
        desc = SENSOR_DESCRIPTIONS[1]
        assert desc.key == "last_updated"
        assert desc.translation_key == "last_updated"


class TestPacificPowerSensor:
    def _make_sensor(self, description=None, utility_name="Pacific Power"):
        account = AccountInfo(
            customer_idn="12345",
            account_sequence="001",
            agreement_sequence="01",
            address="123 Main St",
            site_idn=100,
            service_sequence=1,
        )
        data = PacificPowerData(
            account=account,
            last_data_received=datetime(2025, 8, 1, 12, 0, tzinfo=UTC),
            last_updated=datetime(2025, 8, 1, 14, 0, tzinfo=UTC),
        )
        coordinator = MagicMock()
        coordinator.data = data
        desc = description or SENSOR_DESCRIPTIONS[0]
        return PacificPowerSensor(
            coordinator=coordinator,
            description=desc,
            account_key="12345_001",
            account_data=data,
            utility_name=utility_name,
        )

    def test_unique_id(self):
        sensor = self._make_sensor()
        assert sensor._attr_unique_id == "12345_001_last_data_received"

    def test_unique_id_last_updated(self):
        sensor = self._make_sensor(description=SENSOR_DESCRIPTIONS[1])
        assert sensor._attr_unique_id == "12345_001_last_updated"

    def test_device_info_pacific_power(self):
        sensor = self._make_sensor(utility_name="Pacific Power")
        info = sensor._attr_device_info
        assert info["manufacturer"] == "PacifiCorp"
        assert info["name"] == "Pacific Power 123 Main St"
        assert info["model"] == "Energy Usage"
        assert ("pacific_power", "12345_001") in info["identifiers"]

    def test_device_info_rocky_mountain(self):
        sensor = self._make_sensor(utility_name="Rocky Mountain Power")
        info = sensor._attr_device_info
        assert info["name"] == "Rocky Mountain Power 123 Main St"
        assert info["manufacturer"] == "PacifiCorp"

    def test_has_entity_name(self):
        sensor = self._make_sensor()
        assert sensor._attr_has_entity_name is True

    def test_native_value_last_data_received(self):
        sensor = self._make_sensor(description=SENSOR_DESCRIPTIONS[0])
        assert sensor.native_value == datetime(2025, 8, 1, 12, 0, tzinfo=UTC)

    def test_native_value_last_updated(self):
        sensor = self._make_sensor(description=SENSOR_DESCRIPTIONS[1])
        assert sensor.native_value == datetime(2025, 8, 1, 14, 0, tzinfo=UTC)

    def test_native_value_none_when_no_data(self):
        sensor = self._make_sensor()
        sensor.coordinator.data = None
        assert sensor.native_value is None


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_creates_correct_entities(self):
        account = AccountInfo(
            customer_idn="12345",
            account_sequence="001",
            agreement_sequence="01",
            address="123 Main St",
        )
        data = PacificPowerData(
            account=account,
            last_data_received=None,
            last_updated=datetime(2025, 8, 1, 14, 0, tzinfo=UTC),
        )
        coordinator = MagicMock()
        coordinator.data = data

        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.data = {"utility": "pacific_power"}

        add_entities = MagicMock()
        await async_setup_entry(MagicMock(), entry, add_entities)

        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]
        assert len(entities) == 2
        assert entities[0]._attr_unique_id == "12345_001_last_data_received"
        assert entities[1]._attr_unique_id == "12345_001_last_updated"

    @pytest.mark.asyncio
    async def test_rocky_mountain_utility(self):
        account = AccountInfo(
            customer_idn="12345",
            account_sequence="001",
            agreement_sequence="01",
            address="123 Main St",
        )
        data = PacificPowerData(
            account=account,
            last_data_received=None,
            last_updated=datetime(2025, 8, 1, 14, 0, tzinfo=UTC),
        )
        coordinator = MagicMock()
        coordinator.data = data

        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.data = {"utility": "rocky_mountain_power"}

        add_entities = MagicMock()
        await async_setup_entry(MagicMock(), entry, add_entities)

        entities = add_entities.call_args[0][0]
        assert entities[0]._attr_device_info["name"] == "Rocky Mountain Power 123 Main St"
