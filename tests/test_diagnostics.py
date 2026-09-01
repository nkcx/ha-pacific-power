"""Tests for the Pacific Power diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from custom_components.pacific_power.api import AccountInfo
from custom_components.pacific_power.coordinator import PacificPowerData
from custom_components.pacific_power.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)


class TestToRedact:
    def test_contains_sensitive_fields(self):
        assert "password" in TO_REDACT
        assert "username" in TO_REDACT
        assert "customer_idn" in TO_REDACT
        assert "service_address" in TO_REDACT
        assert "title" in TO_REDACT


class TestDiagnostics:
    def _make_entry(self, *, with_data=True):
        account = AccountInfo(
            customer_idn="12345",
            account_sequence="001",
            agreement_sequence="01",
            address="123 Main St",
        )
        data = PacificPowerData(
            account=account,
            last_data_received=datetime(2025, 8, 1, 12, 0, tzinfo=UTC),
            last_updated=datetime(2025, 8, 1, 14, 0, tzinfo=UTC),
        ) if with_data else None

        coordinator = MagicMock()
        coordinator.data = data

        entry = MagicMock()
        entry.runtime_data = coordinator
        entry.as_dict.return_value = {
            "entry_id": "test123",
            "title": "Pacific Power (123 Main St)",
            "data": {
                "username": "user@example.com",
                "password": "secret",
                "customer_idn": "12345",
                "service_address": "123 Main St",
            },
        }
        return entry

    @pytest.mark.asyncio
    async def test_returns_correct_shape(self):
        entry = self._make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert "config_entry" in result
        assert "coordinator_data" in result

    @pytest.mark.asyncio
    async def test_redacts_password(self):
        entry = self._make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        config = result["config_entry"]
        assert config["data"]["password"] == "**REDACTED**"

    @pytest.mark.asyncio
    async def test_redacts_username(self):
        entry = self._make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        config = result["config_entry"]
        assert config["data"]["username"] == "**REDACTED**"

    @pytest.mark.asyncio
    async def test_redacts_customer_idn(self):
        entry = self._make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        config = result["config_entry"]
        assert config["data"]["customer_idn"] == "**REDACTED**"

    @pytest.mark.asyncio
    async def test_redacts_service_address(self):
        entry = self._make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        config = result["config_entry"]
        assert config["data"]["service_address"] == "**REDACTED**"

    @pytest.mark.asyncio
    async def test_redacts_title(self):
        entry = self._make_entry()
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        config = result["config_entry"]
        assert config["title"] == "**REDACTED**"

    @pytest.mark.asyncio
    async def test_coordinator_data_with_data(self):
        entry = self._make_entry(with_data=True)
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        cd = result["coordinator_data"]
        assert "account_0" in cd
        assert cd["account_0"]["last_data_received"] == "2025-08-01T12:00:00+00:00"
        assert cd["account_0"]["last_updated"] == "2025-08-01T14:00:00+00:00"

    @pytest.mark.asyncio
    async def test_coordinator_data_without_data(self):
        entry = self._make_entry(with_data=False)
        result = await async_get_config_entry_diagnostics(MagicMock(), entry)
        assert result["coordinator_data"] == {}
