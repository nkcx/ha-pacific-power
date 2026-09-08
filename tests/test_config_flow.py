"""Tests for Pacific Power config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.pacific_power.api import (
    AccountInfo,
    PacificPowerAuthError,
    PacificPowerConnectionError,
)
from custom_components.pacific_power.config_flow import (
    PacificPowerConfigFlow,
    PacificPowerOptionsFlow,
)
from custom_components.pacific_power.const import (
    CONF_ACCOUNT_SEQUENCE,
    CONF_AGREEMENT_SEQUENCE,
    CONF_COST_PER_KWH,
    CONF_CUSTOMER_IDN,
    CONF_SERVICE_ADDRESS,
    CONF_TIMEZONE,
    CONF_UTILITY,
    DOMAIN,
    UTILITY_PACIFIC_POWER,
    UTILITY_ROCKY_MOUNTAIN,
)

from .conftest import MOCK_PASSWORD, MOCK_TIMEZONE, MOCK_USERNAME, MOCK_WEB_USER_ID

MODULE = "custom_components.pacific_power.config_flow"


def _make_flow(hass: MagicMock) -> PacificPowerConfigFlow:
    """Create a config flow with a mocked hass and stubbed base-class methods."""
    with patch(f"{MODULE}.ConfigFlow.__init_subclass__", lambda **kw: None):
        flow = PacificPowerConfigFlow.__new__(PacificPowerConfigFlow)
    flow.__init__()
    flow.hass = hass
    flow.context = {}

    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_abort = MagicMock(return_value={"type": "abort"})
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
    return flow


def _user_input(
    utility: str = UTILITY_PACIFIC_POWER,
    username: str = MOCK_USERNAME,
    password: str = MOCK_PASSWORD,
) -> dict:
    return {
        CONF_UTILITY: utility,
        "username": username,
        "password": password,
    }


# ---------------------------------------------------------------------------
# async_step_user
# ---------------------------------------------------------------------------


class TestAsyncStepUser:
    @pytest.mark.asyncio
    async def test_show_form_on_first_call(self, mock_hass: MagicMock) -> None:
        flow = _make_flow(mock_hass)
        result = await flow.async_step_user(user_input=None)
        flow.async_show_form.assert_called_once()
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["step_id"] == "user"
        assert call_kw.kwargs["errors"] == {}

    @pytest.mark.asyncio
    async def test_single_account_creates_entry(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_account: AccountInfo,
    ) -> None:
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_user(_user_input())

        mock_api.async_start.assert_awaited_once()
        mock_api.async_login.assert_awaited_once()
        mock_api.async_get_user_info.assert_awaited_once()
        mock_api.async_get_accounts.assert_awaited_once_with(MOCK_WEB_USER_ID)
        mock_api.async_stop.assert_awaited_once()

        flow.async_set_unique_id.assert_awaited_once_with(
            f"{mock_account.customer_idn}_{mock_account.account_sequence}"
        )
        flow._abort_if_unique_id_configured.assert_called_once()
        flow.async_create_entry.assert_called_once()
        entry_data = flow.async_create_entry.call_args.kwargs["data"]
        assert entry_data[CONF_UTILITY] == UTILITY_PACIFIC_POWER
        assert entry_data["username"] == MOCK_USERNAME
        assert entry_data["password"] == MOCK_PASSWORD
        assert entry_data[CONF_CUSTOMER_IDN] == mock_account.customer_idn
        assert entry_data[CONF_ACCOUNT_SEQUENCE] == mock_account.account_sequence
        assert entry_data[CONF_AGREEMENT_SEQUENCE] == mock_account.agreement_sequence
        assert entry_data[CONF_SERVICE_ADDRESS] == mock_account.address
        assert entry_data[CONF_TIMEZONE] == MOCK_TIMEZONE

    @pytest.mark.asyncio
    async def test_multiple_accounts_shows_select(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_accounts: list[AccountInfo],
    ) -> None:
        mock_api.async_get_accounts.return_value = mock_accounts
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            result = await flow.async_step_user(_user_input())

        assert flow._accounts == mock_accounts
        flow.async_show_form.assert_called_once()
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["step_id"] == "select_account"

    @pytest.mark.asyncio
    async def test_auth_error(
        self, mock_hass: MagicMock, mock_api: AsyncMock
    ) -> None:
        mock_api.async_login.side_effect = PacificPowerAuthError("bad creds")
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_user(_user_input())

        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["errors"] == {"base": "invalid_auth"}
        mock_api.async_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connection_error(
        self, mock_hass: MagicMock, mock_api: AsyncMock
    ) -> None:
        mock_api.async_login.side_effect = PacificPowerConnectionError("timeout")
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_user(_user_input())

        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["errors"] == {"base": "cannot_connect"}

    @pytest.mark.asyncio
    async def test_unknown_error(
        self, mock_hass: MagicMock, mock_api: AsyncMock
    ) -> None:
        mock_api.async_login.side_effect = RuntimeError("kaboom")
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_user(_user_input())

        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["errors"] == {"base": "unknown"}

    @pytest.mark.asyncio
    async def test_no_accounts(
        self, mock_hass: MagicMock, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_accounts.return_value = []
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_user(_user_input())

        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["errors"] == {"base": "no_accounts"}

    @pytest.mark.asyncio
    async def test_api_always_stopped_on_success(
        self, mock_hass: MagicMock, mock_api: AsyncMock
    ) -> None:
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_user(_user_input())
        mock_api.async_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_api_always_stopped_on_error(
        self, mock_hass: MagicMock, mock_api: AsyncMock
    ) -> None:
        mock_api.async_login.side_effect = PacificPowerAuthError("fail")
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_user(_user_input())
        mock_api.async_stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# async_step_select_account
# ---------------------------------------------------------------------------


class TestAsyncStepSelectAccount:
    @pytest.mark.asyncio
    async def test_show_form_no_input(
        self, mock_hass: MagicMock, mock_accounts: list[AccountInfo]
    ) -> None:
        flow = _make_flow(mock_hass)
        flow._accounts = mock_accounts
        result = await flow.async_step_select_account(user_input=None)
        flow.async_show_form.assert_called_once()
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["step_id"] == "select_account"

    @pytest.mark.asyncio
    async def test_select_creates_entry(
        self,
        mock_hass: MagicMock,
        mock_account: AccountInfo,
    ) -> None:
        flow = _make_flow(mock_hass)
        flow._utility = UTILITY_PACIFIC_POWER
        flow._username = MOCK_USERNAME
        flow._password = MOCK_PASSWORD
        flow._accounts = [mock_account]

        label = f"{mock_account.address} ({mock_account.customer_idn}-{mock_account.account_sequence})"
        await flow.async_step_select_account({"account": label})

        flow.async_create_entry.assert_called_once()
        entry_data = flow.async_create_entry.call_args.kwargs["data"]
        assert entry_data[CONF_CUSTOMER_IDN] == mock_account.customer_idn


# ---------------------------------------------------------------------------
# _create_entry
# ---------------------------------------------------------------------------


class TestCreateEntry:
    @pytest.mark.asyncio
    async def test_entry_title_uses_utility_name(
        self, mock_hass: MagicMock, mock_account: AccountInfo
    ) -> None:
        flow = _make_flow(mock_hass)
        flow._utility = UTILITY_PACIFIC_POWER
        flow._username = MOCK_USERNAME
        flow._password = MOCK_PASSWORD
        await flow._create_entry(mock_account)

        title = flow.async_create_entry.call_args.kwargs["title"]
        assert title == f"Pacific Power ({mock_account.address})"

    @pytest.mark.asyncio
    async def test_rocky_mountain_title(
        self, mock_hass: MagicMock, mock_account: AccountInfo
    ) -> None:
        flow = _make_flow(mock_hass)
        flow._utility = UTILITY_ROCKY_MOUNTAIN
        flow._username = MOCK_USERNAME
        flow._password = MOCK_PASSWORD
        await flow._create_entry(mock_account)

        title = flow.async_create_entry.call_args.kwargs["title"]
        assert title == f"Rocky Mountain Power ({mock_account.address})"

    @pytest.mark.asyncio
    async def test_unique_id_set(
        self, mock_hass: MagicMock, mock_account: AccountInfo
    ) -> None:
        flow = _make_flow(mock_hass)
        flow._utility = UTILITY_PACIFIC_POWER
        flow._username = MOCK_USERNAME
        flow._password = MOCK_PASSWORD
        await flow._create_entry(mock_account)

        expected = f"{mock_account.customer_idn}_{mock_account.account_sequence}"
        flow.async_set_unique_id.assert_awaited_once_with(expected)
        flow._abort_if_unique_id_configured.assert_called_once()


# ---------------------------------------------------------------------------
# Utility selection
# ---------------------------------------------------------------------------


class TestUtilitySelection:
    @pytest.mark.asyncio
    async def test_pacific_power_default(
        self, mock_hass: MagicMock, mock_api: AsyncMock
    ) -> None:
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api) as api_cls:
            await flow.async_step_user(_user_input(utility=UTILITY_PACIFIC_POWER))
        api_cls.assert_called_once_with(
            MOCK_USERNAME, MOCK_PASSWORD, utility=UTILITY_PACIFIC_POWER
        )

    @pytest.mark.asyncio
    async def test_rocky_mountain_selection(
        self, mock_hass: MagicMock, mock_api: AsyncMock
    ) -> None:
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api) as api_cls:
            await flow.async_step_user(_user_input(utility=UTILITY_ROCKY_MOUNTAIN))
        api_cls.assert_called_once_with(
            MOCK_USERNAME, MOCK_PASSWORD, utility=UTILITY_ROCKY_MOUNTAIN
        )

    @pytest.mark.asyncio
    async def test_rocky_mountain_entry_title(
        self, mock_hass: MagicMock, mock_api: AsyncMock
    ) -> None:
        flow = _make_flow(mock_hass)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_user(_user_input(utility=UTILITY_ROCKY_MOUNTAIN))

        title = flow.async_create_entry.call_args.kwargs["title"]
        assert title.startswith("Rocky Mountain Power")


# ---------------------------------------------------------------------------
# async_step_reconfigure
# ---------------------------------------------------------------------------


class TestAsyncStepReconfigure:
    def _setup_flow(
        self, mock_hass: MagicMock, entry_data: dict
    ) -> PacificPowerConfigFlow:
        flow = _make_flow(mock_hass)
        mock_entry = MagicMock()
        mock_entry.data = entry_data
        mock_entry.entry_id = "test_entry_id"
        flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)
        return flow

    @pytest.mark.asyncio
    async def test_show_form_no_input(
        self, mock_hass: MagicMock, mock_entry_data: dict
    ) -> None:
        flow = self._setup_flow(mock_hass, mock_entry_data)
        await flow.async_step_reconfigure(user_input=None)
        flow.async_show_form.assert_called_once()
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["step_id"] == "reconfigure"

    @pytest.mark.asyncio
    async def test_success_updates_and_aborts(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
    ) -> None:
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_reconfigure(
                {"username": "new@example.com", "password": "newpass"}
            )
        flow.async_update_reload_and_abort.assert_called_once()
        call_args = flow.async_update_reload_and_abort.call_args
        updated_data = call_args.kwargs["data"]
        assert updated_data["username"] == "new@example.com"
        assert updated_data["password"] == "newpass"
        assert updated_data[CONF_CUSTOMER_IDN] == mock_entry_data[CONF_CUSTOMER_IDN]

    @pytest.mark.asyncio
    async def test_auth_error(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
    ) -> None:
        mock_api.async_login.side_effect = PacificPowerAuthError("bad")
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_reconfigure(
                {"username": MOCK_USERNAME, "password": "wrong"}
            )
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["errors"] == {"base": "invalid_auth"}

    @pytest.mark.asyncio
    async def test_no_access_to_configured_account(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
        mock_account_2: AccountInfo,
    ) -> None:
        mock_api.async_get_accounts.return_value = [mock_account_2]
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_reconfigure(
                {"username": MOCK_USERNAME, "password": MOCK_PASSWORD}
            )
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["errors"] == {"base": "invalid_auth"}

    @pytest.mark.asyncio
    async def test_uses_current_utility(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
    ) -> None:
        mock_entry_data[CONF_UTILITY] = UTILITY_ROCKY_MOUNTAIN
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api) as api_cls:
            await flow.async_step_reconfigure(
                {"username": MOCK_USERNAME, "password": MOCK_PASSWORD}
            )
        api_cls.assert_called_once_with(
            MOCK_USERNAME, MOCK_PASSWORD, utility=UTILITY_ROCKY_MOUNTAIN
        )


# ---------------------------------------------------------------------------
# async_step_reauth / async_step_reauth_confirm
# ---------------------------------------------------------------------------


class TestAsyncStepReauth:
    def _setup_flow(
        self, mock_hass: MagicMock, entry_data: dict
    ) -> PacificPowerConfigFlow:
        flow = _make_flow(mock_hass)
        mock_entry = MagicMock()
        mock_entry.data = entry_data
        mock_entry.entry_id = "test_entry_id"
        mock_hass.config_entries.async_get_entry.return_value = mock_entry
        flow.context = {"entry_id": "test_entry_id"}
        return flow

    @pytest.mark.asyncio
    async def test_reauth_delegates_to_confirm(self, mock_hass: MagicMock) -> None:
        flow = _make_flow(mock_hass)
        mock_entry = MagicMock()
        mock_entry.data = {}
        mock_hass.config_entries.async_get_entry.return_value = mock_entry
        flow.context = {"entry_id": "test_entry_id"}
        await flow.async_step_reauth({})
        flow.async_show_form.assert_called_once()
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["step_id"] == "reauth_confirm"

    @pytest.mark.asyncio
    async def test_show_form_no_input(
        self, mock_hass: MagicMock, mock_entry_data: dict
    ) -> None:
        flow = self._setup_flow(mock_hass, mock_entry_data)
        await flow.async_step_reauth_confirm(user_input=None)
        flow.async_show_form.assert_called_once()
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["step_id"] == "reauth_confirm"

    @pytest.mark.asyncio
    async def test_success_updates_entry_and_reloads(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
    ) -> None:
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            result = await flow.async_step_reauth_confirm(
                {"username": "new@example.com", "password": "newpass"}
            )

        mock_hass.config_entries.async_update_entry.assert_called_once()
        update_call = mock_hass.config_entries.async_update_entry.call_args
        updated_data = update_call.kwargs["data"]
        assert updated_data["username"] == "new@example.com"
        assert updated_data["password"] == "newpass"

        mock_hass.config_entries.async_reload.assert_awaited_once_with("test_entry_id")
        flow.async_abort.assert_called_once_with(reason="reauth_successful")

    @pytest.mark.asyncio
    async def test_auth_error(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
    ) -> None:
        mock_api.async_login.side_effect = PacificPowerAuthError("bad")
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_reauth_confirm(
                {"username": MOCK_USERNAME, "password": "wrong"}
            )
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["errors"] == {"base": "invalid_auth"}

    @pytest.mark.asyncio
    async def test_connection_error(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
    ) -> None:
        mock_api.async_login.side_effect = PacificPowerConnectionError("down")
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_reauth_confirm(
                {"username": MOCK_USERNAME, "password": MOCK_PASSWORD}
            )
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["errors"] == {"base": "cannot_connect"}

    @pytest.mark.asyncio
    async def test_wrong_account_returns_invalid_auth(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
        mock_account_2: AccountInfo,
    ) -> None:
        mock_api.async_get_accounts.return_value = [mock_account_2]
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_reauth_confirm(
                {"username": MOCK_USERNAME, "password": MOCK_PASSWORD}
            )
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["errors"] == {"base": "invalid_auth"}

    @pytest.mark.asyncio
    async def test_uses_entry_utility(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
    ) -> None:
        mock_entry_data[CONF_UTILITY] = UTILITY_ROCKY_MOUNTAIN
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api) as api_cls:
            await flow.async_step_reauth_confirm(
                {"username": MOCK_USERNAME, "password": MOCK_PASSWORD}
            )
        api_cls.assert_called_once_with(
            MOCK_USERNAME, MOCK_PASSWORD, utility=UTILITY_ROCKY_MOUNTAIN
        )

    @pytest.mark.asyncio
    async def test_defaults_to_pacific_power_when_utility_missing(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
    ) -> None:
        del mock_entry_data[CONF_UTILITY]
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api) as api_cls:
            await flow.async_step_reauth_confirm(
                {"username": MOCK_USERNAME, "password": MOCK_PASSWORD}
            )
        api_cls.assert_called_once_with(
            MOCK_USERNAME, MOCK_PASSWORD, utility=UTILITY_PACIFIC_POWER
        )

    @pytest.mark.asyncio
    async def test_unknown_error(
        self,
        mock_hass: MagicMock,
        mock_api: AsyncMock,
        mock_entry_data: dict,
    ) -> None:
        mock_api.async_get_accounts.side_effect = RuntimeError("surprise")
        flow = self._setup_flow(mock_hass, mock_entry_data)
        with patch(f"{MODULE}.PacificPowerApi", return_value=mock_api):
            await flow.async_step_reauth_confirm(
                {"username": MOCK_USERNAME, "password": MOCK_PASSWORD}
            )
        call_kw = flow.async_show_form.call_args
        assert call_kw.kwargs["errors"] == {"base": "unknown"}

    @pytest.mark.asyncio
    async def test_prefills_username(
        self, mock_hass: MagicMock, mock_entry_data: dict
    ) -> None:
        flow = self._setup_flow(mock_hass, mock_entry_data)
        await flow.async_step_reauth_confirm(user_input=None)
        call_kw = flow.async_show_form.call_args
        schema = call_kw.kwargs["data_schema"]
        schema_dict = dict(schema.schema)
        for key in schema_dict:
            if hasattr(key, "default") and str(key) == "username":
                assert key.default() == MOCK_USERNAME


# ---- Options flow ----


def _make_options_flow(entry: MagicMock) -> PacificPowerOptionsFlow:
    flow = PacificPowerOptionsFlow()
    flow.config_entry = entry
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    return flow


class TestOptionsFlow:
    def test_config_flow_provides_options_flow(self, mock_entry: MagicMock) -> None:
        flow = PacificPowerConfigFlow.async_get_options_flow(mock_entry)
        assert isinstance(flow, PacificPowerOptionsFlow)

    @pytest.mark.asyncio
    async def test_show_form_defaults_to_zero(self, mock_entry: MagicMock) -> None:
        flow = _make_options_flow(mock_entry)
        result = await flow.async_step_init()

        assert result == {"type": "form"}
        assert flow.async_show_form.call_args[1]["step_id"] == "init"
        schema = flow.async_show_form.call_args[1]["data_schema"]
        assert schema({}) == {CONF_COST_PER_KWH: 0.0}

    @pytest.mark.asyncio
    async def test_show_form_prefills_current_rate(self, mock_entry: MagicMock) -> None:
        mock_entry.options = {CONF_COST_PER_KWH: 0.12}
        flow = _make_options_flow(mock_entry)
        await flow.async_step_init()

        schema = flow.async_show_form.call_args[1]["data_schema"]
        assert schema({}) == {CONF_COST_PER_KWH: 0.12}

    @pytest.mark.asyncio
    async def test_schema_coerces_and_rejects_negative(
        self, mock_entry: MagicMock
    ) -> None:
        flow = _make_options_flow(mock_entry)
        await flow.async_step_init()
        schema = flow.async_show_form.call_args[1]["data_schema"]

        assert schema({CONF_COST_PER_KWH: "0.2"}) == {CONF_COST_PER_KWH: 0.2}
        with pytest.raises(vol.Invalid):
            schema({CONF_COST_PER_KWH: -1})

    @pytest.mark.asyncio
    async def test_submit_creates_entry(self, mock_entry: MagicMock) -> None:
        flow = _make_options_flow(mock_entry)
        result = await flow.async_step_init({CONF_COST_PER_KWH: 0.15})

        assert result == {"type": "create_entry"}
        flow.async_create_entry.assert_called_once_with(data={CONF_COST_PER_KWH: 0.15})
