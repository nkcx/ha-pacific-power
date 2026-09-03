"""Pacific Power / Rocky Mountain Power integration for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .coordinator import PacificPowerConfigEntry, PacificPowerCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: PacificPowerConfigEntry
) -> bool:
    """Set up Pacific Power from a config entry."""
    coordinator = PacificPowerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: PacificPowerConfigEntry
) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: PacificPowerConfigEntry
) -> bool:
    """Unload a Pacific Power config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: PacificPowerConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow manual removal of a device via the UI."""
    return True
