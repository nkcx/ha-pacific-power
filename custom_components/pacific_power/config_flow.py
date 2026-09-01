"""Config flow for Pacific Power / Rocky Mountain Power integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .api import (
    AccountInfo,
    PacificPowerApi,
    PacificPowerAuthError,
    PacificPowerConnectionError,
)
from .const import (
    CONF_ACCOUNT_SEQUENCE,
    CONF_AGREEMENT_SEQUENCE,
    CONF_CUSTOMER_IDN,
    CONF_SERVICE_ADDRESS,
    CONF_TIMEZONE,
    CONF_UTILITY,
    DOMAIN,
    UTILITY_DOMAINS,
    UTILITY_PACIFIC_POWER,
    UTILITY_ROCKY_MOUNTAIN,
)

_LOGGER = logging.getLogger(__name__)

CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_UTILITY, default=UTILITY_PACIFIC_POWER): vol.In(
            {
                UTILITY_PACIFIC_POWER: "Pacific Power",
                UTILITY_ROCKY_MOUNTAIN: "Rocky Mountain Power",
            }
        ),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


def _account_label(account: AccountInfo) -> str:
    return f"{account.address} ({account.customer_idn}-{account.account_sequence})"


class PacificPowerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pacific Power."""

    VERSION = 1

    def __init__(self) -> None:
        self._utility: str = UTILITY_PACIFIC_POWER
        self._username: str = ""
        self._password: str = ""
        self._accounts: list[AccountInfo] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle credentials and account discovery."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._utility = user_input[CONF_UTILITY]
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            api = PacificPowerApi(self._username, self._password, utility=self._utility)
            try:
                await api.async_start()
                await api.async_login()
                user = await api.async_get_user_info()
                self._accounts = await api.async_get_accounts(user.get("webUserId", ""))
            except PacificPowerAuthError:
                errors["base"] = "invalid_auth"
            except PacificPowerConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "unknown"
            else:
                if len(self._accounts) == 1:
                    return await self._create_entry(self._accounts[0])
                if len(self._accounts) > 1:
                    return await self.async_step_select_account()
                errors["base"] = "no_accounts"
            finally:
                await api.async_stop()

        return self.async_show_form(
            step_id="user",
            data_schema=CREDENTIALS_SCHEMA,
            errors=errors,
        )

    async def async_step_select_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which account to add."""
        if user_input is not None:
            selected = user_input["account"]
            for account in self._accounts:
                if _account_label(account) == selected:
                    return await self._create_entry(account)

        labels = [_account_label(a) for a in self._accounts]
        return self.async_show_form(
            step_id="select_account",
            data_schema=vol.Schema({vol.Required("account"): vol.In(labels)}),
        )

    async def _create_entry(self, account: AccountInfo) -> ConfigFlowResult:
        unique_id = f"{account.customer_idn}_{account.account_sequence}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        utility_name = UTILITY_DOMAINS[self._utility]["name"]
        return self.async_create_entry(
            title=f"{utility_name} ({account.address})",
            data={
                CONF_UTILITY: self._utility,
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_CUSTOMER_IDN: account.customer_idn,
                CONF_ACCOUNT_SEQUENCE: account.account_sequence,
                CONF_AGREEMENT_SEQUENCE: account.agreement_sequence,
                CONF_SERVICE_ADDRESS: account.address,
                CONF_TIMEZONE: self.hass.config.time_zone,
            },
        )

    async def _validate_and_update_credentials(
        self,
        username: str,
        password: str,
        utility: str,
        errors: dict[str, str],
    ) -> list[AccountInfo] | None:
        """Validate credentials and check account access."""
        api = PacificPowerApi(username, password, utility=utility)
        try:
            await api.async_start()
            await api.async_login()
            user = await api.async_get_user_info()
            return await api.async_get_accounts(user.get("webUserId", ""))
        except PacificPowerAuthError:
            errors["base"] = "invalid_auth"
        except PacificPowerConnectionError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error during credential validation")
            errors["base"] = "unknown"
        finally:
            await api.async_stop()
        return None

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to update credentials for an existing entry."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        current_utility = entry.data.get(CONF_UTILITY, UTILITY_PACIFIC_POWER)

        if user_input is not None:
            accounts = await self._validate_and_update_credentials(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                current_utility,
                errors,
            )
            if accounts is not None:
                configured_idn = entry.data.get(CONF_CUSTOMER_IDN)
                has_access = any(a.customer_idn == configured_idn for a in accounts)
                if not has_access:
                    errors["base"] = "invalid_auth"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data={
                            **entry.data,
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                        },
                    )

        reconfigure_schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=entry.data.get(CONF_USERNAME, ""),
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=reconfigure_schema,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if not entry:
            return self.async_abort(reason="reauth_successful")

        current_utility = entry.data.get(CONF_UTILITY, UTILITY_PACIFIC_POWER)

        if user_input is not None:
            accounts = await self._validate_and_update_credentials(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                current_utility,
                errors,
            )
            if accounts is not None:
                configured_idn = entry.data.get(CONF_CUSTOMER_IDN)
                has_access = any(a.customer_idn == configured_idn for a in accounts)
                if not has_access:
                    errors["base"] = "invalid_auth"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                        },
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

        reauth_schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=entry.data.get(CONF_USERNAME, ""),
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=reauth_schema,
            errors=errors,
        )
