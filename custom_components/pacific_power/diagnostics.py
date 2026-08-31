"""Diagnostics for Pacific Power integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_CUSTOMER_IDN, CONF_SERVICE_ADDRESS
from .coordinator import PacificPowerConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_CUSTOMER_IDN,
    CONF_SERVICE_ADDRESS,
    "title",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PacificPowerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    coordinator_data = {}
    if coordinator.data:
        data = coordinator.data
        coordinator_data["account_0"] = {
            "last_data_received": (
                data.last_data_received.isoformat() if data.last_data_received else None
            ),
            "last_updated": data.last_updated.isoformat(),
        }

    return async_redact_data(
        {
            "config_entry": entry.as_dict(),
            "coordinator_data": coordinator_data,
        },
        TO_REDACT,
    )
