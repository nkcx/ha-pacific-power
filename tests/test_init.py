"""Tests for the Pacific Power integration setup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.pacific_power import (
    PLATFORMS,
    _async_options_updated,
    async_remove_config_entry_device,
    async_setup_entry,
    async_unload_entry,
)


class TestPlatforms:
    def test_sensor_only(self):
        assert len(PLATFORMS) == 1
        assert PLATFORMS[0] == "sensor"


class TestAsyncSetupEntry:
    @pytest.mark.asyncio
    async def test_creates_coordinator_and_stores_runtime_data(self, mock_hass, mock_entry):
        with patch(
            "custom_components.pacific_power.PacificPowerCoordinator"
        ) as mock_coord_cls:
            coordinator = AsyncMock()
            coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coord_cls.return_value = coordinator

            result = await async_setup_entry(mock_hass, mock_entry)

        assert result is True
        mock_coord_cls.assert_called_once_with(mock_hass, mock_entry)
        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        assert mock_entry.runtime_data == coordinator
        mock_hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_forwards_sensor_platform(self, mock_hass, mock_entry):
        with patch(
            "custom_components.pacific_power.PacificPowerCoordinator"
        ) as mock_coord_cls:
            coordinator = AsyncMock()
            coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coord_cls.return_value = coordinator

            await async_setup_entry(mock_hass, mock_entry)

        call_args = mock_hass.config_entries.async_forward_entry_setups.call_args
        assert call_args[0][1] == PLATFORMS


class TestAsyncUnloadEntry:
    @pytest.mark.asyncio
    async def test_unloads_platforms(self, mock_hass, mock_entry):
        result = await async_unload_entry(mock_hass, mock_entry)
        assert result is True
        mock_hass.config_entries.async_unload_platforms.assert_awaited_once_with(
            mock_entry, PLATFORMS
        )


class TestAsyncRemoveConfigEntryDevice:
    @pytest.mark.asyncio
    async def test_returns_true(self, mock_hass, mock_entry):
        device = MagicMock()
        result = await async_remove_config_entry_device(mock_hass, mock_entry, device)
        assert result is True


class TestOptionsUpdateListener:
    @pytest.mark.asyncio
    async def test_setup_registers_listener_for_unload(self, mock_hass, mock_entry):
        with patch(
            "custom_components.pacific_power.PacificPowerCoordinator"
        ) as mock_coord_cls:
            coordinator = AsyncMock()
            coordinator.async_config_entry_first_refresh = AsyncMock()
            mock_coord_cls.return_value = coordinator

            await async_setup_entry(mock_hass, mock_entry)

        mock_entry.add_update_listener.assert_called_once_with(_async_options_updated)
        mock_entry.async_on_unload.assert_called_once_with(
            mock_entry.add_update_listener.return_value
        )

    @pytest.mark.asyncio
    async def test_options_update_reloads_entry(self, mock_hass, mock_entry):
        await _async_options_updated(mock_hass, mock_entry)
        mock_hass.config_entries.async_reload.assert_awaited_once_with(
            mock_entry.entry_id
        )
