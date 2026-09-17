"""Tests for the PacificPower API client."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from custom_components.pacific_power.api import (
    AccountInfo,
    DailyUsage,
    HourlyUsage,
    PacificPowerApi,
    PacificPowerApiError,
    PacificPowerAuthError,
    PacificPowerConnectionError,
    _collect_cookies,
)
from custom_components.pacific_power.const import (
    UTILITY_DOMAINS,
    UTILITY_PACIFIC_POWER,
    UTILITY_ROCKY_MOUNTAIN,
)

# Use 2048-bit keys for test speed; production uses 4096.
_TEST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_AES_KEY = os.urandom(32)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(
    status: int = 200,
    text: str = "",
    headers: dict[str, str] | None = None,
    url: str = "https://example.com",
) -> MagicMock:
    """Build a mock aiohttp response with async context-manager support."""
    resp = MagicMock()
    resp.status = status
    resp.url = url
    resp.text = AsyncMock(return_value=text)
    resp.headers = MagicMock()
    resp.headers.getall = MagicMock(return_value=[])
    if headers:
        cookie_vals = []
        for k, v in headers.items():
            if k.lower() == "set-cookie":
                cookie_vals.append(v)
        resp.headers.getall = MagicMock(
            side_effect=lambda name, default=None: cookie_vals
            if name == "Set-Cookie"
            else (default or [])
        )
        resp.headers.__getitem__ = lambda self_inner, key: headers.get(key, "")
        resp.headers.get = lambda key, default="": headers.get(key, default)
    return resp


def _encrypt_response(plaintext: bytes, aes_key: bytes) -> str:
    """Encrypt a response body the way the real API does."""
    iv = os.urandom(12)
    ct = AESGCM(aes_key).encrypt(iv, plaintext, None)
    return base64.b64encode(iv).decode() + base64.b64encode(ct).decode()


class _AsyncContextManager:
    """Minimal async context manager wrapping a mock response."""

    def __init__(self, resp: MagicMock) -> None:
        self._resp = resp

    async def __aenter__(self) -> MagicMock:
        return self._resp

    async def __aexit__(self, *args: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------


class TestConstructor:
    """Test PacificPowerApi initialisation."""

    def test_defaults_to_pacific_power(self) -> None:
        api = PacificPowerApi("user@test.com", "pass123")
        assert api._base_url == UTILITY_DOMAINS[UTILITY_PACIFIC_POWER]["base_url"]
        assert api._login_url == UTILITY_DOMAINS[UTILITY_PACIFIC_POWER]["login_url"]
        assert api._subsidiary == "PacificPower"
        assert api._policy == "B2C_1A_PAC_SIGNIN"

    def test_rocky_mountain_power(self) -> None:
        api = PacificPowerApi("u", "p", utility=UTILITY_ROCKY_MOUNTAIN)
        assert api._base_url == UTILITY_DOMAINS[UTILITY_ROCKY_MOUNTAIN]["base_url"]
        assert api._login_url == UTILITY_DOMAINS[UTILITY_ROCKY_MOUNTAIN]["login_url"]
        assert api._subsidiary == "RockyMountainPower"
        assert api._policy == "B2C_1A_PAC_SIGNIN"

    def test_unknown_utility_falls_back_to_pacific_power(self) -> None:
        api = PacificPowerApi("u", "p", utility="nonexistent")
        assert api._base_url == UTILITY_DOMAINS[UTILITY_PACIFIC_POWER]["base_url"]

    def test_initial_state(self) -> None:
        api = PacificPowerApi("u", "p")
        assert api._cookies == {}
        assert api._session is None
        assert api._sign_key is None
        assert api._aes_key is None
        assert api._web_user_id is None


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    """Test async_start and async_stop."""

    @pytest.mark.asyncio
    async def test_start_creates_session(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()
        assert api._session is not None
        await api.async_stop()

    @pytest.mark.asyncio
    async def test_stop_closes_and_clears_session(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()
        session = api._session
        await api.async_stop()
        assert api._session is None
        assert session.closed  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_stop()  # should not raise


# ---------------------------------------------------------------------------
# Cookie collection
# ---------------------------------------------------------------------------


class TestCollectCookies:
    """Test the _collect_cookies helper."""

    def test_parses_simple_cookie(self) -> None:
        resp = _make_mock_response(
            headers={"Set-Cookie": "session=abc123; Path=/; HttpOnly"}
        )
        cookies: dict[str, str] = {}
        _collect_cookies(resp, cookies)
        assert cookies == {"session": "abc123"}

    def test_parses_multiple_cookies(self) -> None:
        resp = MagicMock()
        resp.headers = MagicMock()
        resp.headers.getall = MagicMock(
            side_effect=lambda name, default=None: [
                "a=1; Path=/",
                "b=2; Secure",
                "c=3",
            ]
            if name == "Set-Cookie"
            else (default or [])
        )
        cookies: dict[str, str] = {}
        _collect_cookies(resp, cookies)
        assert cookies == {"a": "1", "b": "2", "c": "3"}

    def test_overwrites_existing_cookie(self) -> None:
        resp = _make_mock_response(headers={"Set-Cookie": "k=new; Path=/"})
        cookies = {"k": "old"}
        _collect_cookies(resp, cookies)
        assert cookies["k"] == "new"

    def test_empty_set_cookie(self) -> None:
        resp = _make_mock_response()
        cookies: dict[str, str] = {}
        _collect_cookies(resp, cookies)
        assert cookies == {}

    def test_cookie_with_equals_in_value(self) -> None:
        resp = _make_mock_response(
            headers={"Set-Cookie": "token=abc=def=ghi; Path=/"}
        )
        cookies: dict[str, str] = {}
        _collect_cookies(resp, cookies)
        assert cookies["token"] == "abc=def=ghi"


# ---------------------------------------------------------------------------
# Crypto: handshake
# ---------------------------------------------------------------------------


class TestHandshake:
    """Test the _handshake key exchange."""

    @pytest.mark.asyncio
    async def test_handshake_decrypts_aes_key(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()
        api._cookies = {"XSRF-TOKEN": "tok"}

        aes_key = os.urandom(32)

        def mock_post(url: str, **kwargs: object) -> _AsyncContextManager:
            data = kwargs.get("data", b"")
            assert isinstance(data, bytes)
            parts = data.decode().split(":")
            assert len(parts) == 2  # sign_pub:enc_pub

            enc_pub_bytes = base64.b64decode(parts[1])
            enc_pub = serialization.load_der_public_key(enc_pub_bytes)
            encrypted_aes = base64.b64encode(
                enc_pub.encrypt(  # type: ignore[union-attr]
                    aes_key,
                    padding.OAEP(
                        mgf=padding.MGF1(algorithm=hashes.SHA256()),
                        algorithm=hashes.SHA256(),
                        label=None,
                    ),
                )
            ).decode()
            return _AsyncContextManager(
                _make_mock_response(status=200, text=encrypted_aes)
            )

        api._session.post = mock_post  # type: ignore[union-attr]
        await api._handshake()

        assert api._aes_key == aes_key
        assert api._sign_key is not None
        await api.async_stop()

    @pytest.mark.asyncio
    async def test_handshake_failure_raises(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()
        api._cookies = {"XSRF-TOKEN": "tok"}

        def mock_post(url: str, **kwargs: object) -> _AsyncContextManager:
            return _AsyncContextManager(_make_mock_response(status=500, text="error"))

        api._session.post = mock_post  # type: ignore[union-attr]
        with pytest.raises(PacificPowerConnectionError, match="Key exchange failed"):
            await api._handshake()
        await api.async_stop()


# ---------------------------------------------------------------------------
# Crypto: _api_call round-trip
# ---------------------------------------------------------------------------


class TestApiCall:
    """Test encrypted API call round-trip."""

    @pytest.mark.asyncio
    async def test_encrypts_request_and_decrypts_response(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()
        api._sign_key = _TEST_KEY
        api._aes_key = _TEST_AES_KEY
        api._cookies = {"XSRF-TOKEN": "tok"}

        response_payload = {"result": "ok", "value": 42}
        encrypted_response = _encrypt_response(
            json.dumps(response_payload).encode(), _TEST_AES_KEY
        )

        captured_body: str | None = None

        def mock_post(url: str, **kwargs: object) -> _AsyncContextManager:
            nonlocal captured_body
            captured_body = kwargs.get("data")  # type: ignore[assignment]
            return _AsyncContextManager(
                _make_mock_response(status=200, text=encrypted_response)
            )

        api._session.post = mock_post  # type: ignore[union-attr]
        result = await api._api_call("/api/test", {"input": "data"})

        assert result == response_payload
        assert captured_body is not None
        # The sent body should be base64-encoded (IV + ciphertext)
        assert isinstance(captured_body, bytes)
        await api.async_stop()

    @pytest.mark.asyncio
    async def test_null_body(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()
        api._sign_key = _TEST_KEY
        api._aes_key = _TEST_AES_KEY
        api._cookies = {}

        response_payload = {"user": "me"}
        encrypted_response = _encrypt_response(
            json.dumps(response_payload).encode(), _TEST_AES_KEY
        )

        def mock_post(url: str, **kwargs: object) -> _AsyncContextManager:
            return _AsyncContextManager(
                _make_mock_response(status=200, text=encrypted_response)
            )

        api._session.post = mock_post  # type: ignore[union-attr]
        result = await api._api_call("/api/user/me", None)
        assert result == response_payload
        await api.async_stop()

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()
        api._sign_key = _TEST_KEY
        api._aes_key = _TEST_AES_KEY
        api._cookies = {}

        def mock_post(url: str, **kwargs: object) -> _AsyncContextManager:
            return _AsyncContextManager(
                _make_mock_response(status=401, text="unauthorized")
            )

        api._session.post = mock_post  # type: ignore[union-attr]
        with pytest.raises(PacificPowerAuthError, match="Session expired"):
            await api._api_call("/api/test", None)
        await api.async_stop()

    @pytest.mark.asyncio
    async def test_403_raises_api_error(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()
        api._sign_key = _TEST_KEY
        api._aes_key = _TEST_AES_KEY
        api._cookies = {}

        def mock_post(url: str, **kwargs: object) -> _AsyncContextManager:
            return _AsyncContextManager(
                _make_mock_response(status=403, text="forbidden")
            )

        api._session.post = mock_post  # type: ignore[union-attr]
        with pytest.raises(PacificPowerApiError, match="Access denied"):
            await api._api_call("/api/test", None)
        await api.async_stop()

    @pytest.mark.asyncio
    async def test_400_with_fault_message(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()
        api._sign_key = _TEST_KEY
        api._aes_key = _TEST_AES_KEY
        api._cookies = {}

        fault_body = json.dumps(
            {"fault": {"faultmessage": "Invalid account"}}
        )

        def mock_post(url: str, **kwargs: object) -> _AsyncContextManager:
            return _AsyncContextManager(
                _make_mock_response(status=400, text=fault_body)
            )

        api._session.post = mock_post  # type: ignore[union-attr]
        with pytest.raises(PacificPowerApiError, match="Invalid account"):
            await api._api_call("/api/test", None)
        await api.async_stop()

    @pytest.mark.asyncio
    async def test_500_raises_api_error(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()
        api._sign_key = _TEST_KEY
        api._aes_key = _TEST_AES_KEY
        api._cookies = {}

        def mock_post(url: str, **kwargs: object) -> _AsyncContextManager:
            return _AsyncContextManager(
                _make_mock_response(status=500, text="internal error")
            )

        api._session.post = mock_post  # type: ignore[union-attr]
        with pytest.raises(PacificPowerApiError, match="HTTP 500"):
            await api._api_call("/api/test", None)
        await api.async_stop()

    @pytest.mark.asyncio
    async def test_plaintext_json_fallback(self) -> None:
        """When response is not encrypted, fall back to plain JSON parse."""
        api = PacificPowerApi("u", "p")
        await api.async_start()
        api._sign_key = _TEST_KEY
        api._aes_key = _TEST_AES_KEY
        api._cookies = {}

        plain_response = json.dumps({"plaintext": True})

        def mock_post(url: str, **kwargs: object) -> _AsyncContextManager:
            return _AsyncContextManager(
                _make_mock_response(status=200, text=plain_response)
            )

        api._session.post = mock_post  # type: ignore[union-attr]
        result = await api._api_call("/api/test", None)
        assert result == {"plaintext": True}
        await api.async_stop()


# ---------------------------------------------------------------------------
# Account parsing
# ---------------------------------------------------------------------------


class TestGetAccounts:
    """Test async_get_accounts response parsing."""

    @pytest.mark.asyncio
    async def test_parses_single_account(self) -> None:
        api = PacificPowerApi("u", "p")
        api._web_user_id = "web123"

        canned_account_list = {
            "getAccountListResponseBody": {
                "accountList": {
                    "webAccount": {
                        "customer": {"idn": "12345"},
                        "sequence": "001",
                        "mailingAddressLine1": "  123 Main St  ",
                    }
                }
            }
        }
        canned_agreements = {
            "getMeteredAgreementsResponseBody": {
                "meteredAgreementList": {
                    "meteredAgreement": {
                        "agreementSequence": "01",
                        "agreementStatus": "Active",
                        "siteIDN": 999,
                        "serviceSequence": 1,
                    }
                }
            }
        }

        call_count = 0

        async def mock_api_call(path: str, body: dict | None) -> dict:
            nonlocal call_count
            call_count += 1
            if "getAccountList" in path:
                return canned_account_list
            return canned_agreements

        api._api_call = mock_api_call  # type: ignore[assignment]

        accounts = await api.async_get_accounts("web123")
        assert len(accounts) == 1
        assert accounts[0].customer_idn == "12345"
        assert accounts[0].account_sequence == "001"
        assert accounts[0].agreement_sequence == "01"
        assert accounts[0].address == "123 Main St"
        assert accounts[0].site_idn == 999
        assert accounts[0].service_sequence == 1

    @pytest.mark.asyncio
    async def test_parses_multiple_accounts(self) -> None:
        api = PacificPowerApi("u", "p")

        canned_account_list = {
            "getAccountListResponseBody": {
                "accountList": {
                    "webAccount": [
                        {
                            "customer": {"idn": "AAA"},
                            "sequence": "01",
                            "mailingAddressLine1": "Addr A",
                        },
                        {
                            "customer": {"idn": "BBB"},
                            "sequence": "02",
                            "mailingAddressLine1": "Addr B",
                        },
                    ]
                }
            }
        }
        canned_agreements = {
            "getMeteredAgreementsResponseBody": {
                "meteredAgreementList": {
                    "meteredAgreement": {
                        "agreementSequence": "01",
                        "agreementStatus": "Active",
                        "siteIDN": 1,
                        "serviceSequence": 1,
                    }
                }
            }
        }

        async def mock_api_call(path: str, body: dict | None) -> dict:
            if "getAccountList" in path:
                return canned_account_list
            return canned_agreements

        api._api_call = mock_api_call  # type: ignore[assignment]
        accounts = await api.async_get_accounts("w")
        assert len(accounts) == 2
        assert accounts[0].customer_idn == "AAA"
        assert accounts[1].customer_idn == "BBB"

    @pytest.mark.asyncio
    async def test_skips_inactive_agreements(self) -> None:
        api = PacificPowerApi("u", "p")

        canned_account_list = {
            "getAccountListResponseBody": {
                "accountList": {
                    "webAccount": {
                        "customer": {"idn": "X"},
                        "sequence": "01",
                        "mailingAddressLine1": "Addr",
                    }
                }
            }
        }
        canned_agreements = {
            "getMeteredAgreementsResponseBody": {
                "meteredAgreementList": {
                    "meteredAgreement": [
                        {
                            "agreementSequence": "01",
                            "agreementStatus": "Closed",
                            "siteIDN": 1,
                            "serviceSequence": 1,
                        },
                        {
                            "agreementSequence": "02",
                            "agreementStatus": "Active",
                            "siteIDN": 2,
                            "serviceSequence": 2,
                        },
                    ]
                }
            }
        }

        async def mock_api_call(path: str, body: dict | None) -> dict:
            if "getAccountList" in path:
                return canned_account_list
            return canned_agreements

        api._api_call = mock_api_call  # type: ignore[assignment]
        accounts = await api.async_get_accounts("w")
        assert len(accounts) == 1
        assert accounts[0].agreement_sequence == "02"

    @pytest.mark.asyncio
    async def test_skips_accounts_missing_idn(self) -> None:
        api = PacificPowerApi("u", "p")

        canned = {
            "getAccountListResponseBody": {
                "accountList": {
                    "webAccount": {
                        "customer": {},
                        "sequence": "01",
                        "mailingAddressLine1": "Addr",
                    }
                }
            }
        }

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return canned

        api._api_call = mock_api_call  # type: ignore[assignment]
        accounts = await api.async_get_accounts("w")
        assert len(accounts) == 0

    @pytest.mark.asyncio
    async def test_uses_correct_subsidiary(self) -> None:
        api = PacificPowerApi("u", "p", utility=UTILITY_ROCKY_MOUNTAIN)
        captured_bodies: list[dict] = []

        async def mock_api_call(path: str, body: dict | None) -> dict:
            if body:
                captured_bodies.append(body)
            if "getAccountList" in path:
                return {
                    "getAccountListResponseBody": {
                        "accountList": {"webAccount": []}
                    }
                }
            return {}

        api._api_call = mock_api_call  # type: ignore[assignment]
        await api.async_get_accounts("w")
        assert len(captured_bodies) == 1
        subsidiary = (
            captured_bodies[0]["getAccountListRequestBody"]["domain"][
                "pacifiCorpSubsidiary"
            ]
        )
        assert subsidiary == "RockyMountainPower"


# ---------------------------------------------------------------------------
# Daily usage parsing
# ---------------------------------------------------------------------------


class TestDailyUsage:
    """Test async_get_daily_usage response parsing."""

    @pytest.mark.asyncio
    async def test_parses_daily_readings(self) -> None:
        api = PacificPowerApi("u", "p")
        account = AccountInfo("C1", "01", "01", "Addr")

        canned = {
            "getUsageForDateRangeResponseBody": {
                "dailyUsageList": {
                    "usgHistoryLineItem": [
                        {
                            "usagePeriodEndDate": "2025-08-01",
                            "kwhUsageQuantity": 15.5,
                        },
                        {
                            "usagePeriodEndDate": "2025-08-02",
                            "kwhUsageQuantity": 22.3,
                        },
                    ]
                }
            }
        }

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return canned

        api._api_call = mock_api_call  # type: ignore[assignment]
        start = datetime(2025, 8, 1)
        end = datetime(2025, 8, 2)
        result = await api.async_get_daily_usage(account, start, end)
        assert len(result) == 2
        assert result[0] == DailyUsage(date="2025-08-01", kwh=15.5)
        assert result[1] == DailyUsage(date="2025-08-02", kwh=22.3)

    @pytest.mark.asyncio
    async def test_empty_daily_response(self) -> None:
        api = PacificPowerApi("u", "p")
        account = AccountInfo("C1", "01", "01", "Addr")

        canned: dict = {"getUsageForDateRangeResponseBody": {"dailyUsageList": {"usgHistoryLineItem": []}}}

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return canned

        api._api_call = mock_api_call  # type: ignore[assignment]
        result = await api.async_get_daily_usage(
            account, datetime(2025, 8, 1), datetime(2025, 8, 2)
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_items_without_date(self) -> None:
        api = PacificPowerApi("u", "p")
        account = AccountInfo("C1", "01", "01", "Addr")

        canned = {
            "getUsageForDateRangeResponseBody": {
                "dailyUsageList": {
                    "usgHistoryLineItem": [
                        {"kwhUsageQuantity": 10.0},
                        {
                            "usagePeriodEndDate": "2025-08-01",
                            "kwhUsageQuantity": 5.0,
                        },
                    ]
                }
            }
        }

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return canned

        api._api_call = mock_api_call  # type: ignore[assignment]
        result = await api.async_get_daily_usage(
            account, datetime(2025, 8, 1), datetime(2025, 8, 1)
        )
        assert len(result) == 1
        assert result[0].date == "2025-08-01"


# ---------------------------------------------------------------------------
# Hourly usage parsing
# ---------------------------------------------------------------------------


class TestHourlyUsage:
    """Test async_get_hourly_usage response parsing."""

    @pytest.mark.asyncio
    async def test_parses_hourly_readings(self) -> None:
        api = PacificPowerApi("u", "p")
        account = AccountInfo("C1", "01", "01", "Addr", site_idn=1, service_sequence=1)

        canned = {
            "getIntervalUsageForDateResponseBody": {
                "intervalDataExists": True,
                "response": {
                    "intervalDataResponse": [
                        {
                            "readDate": "2025-08-01",
                            "readTime": "00:00",
                            "usage": "1.5",
                        },
                        {
                            "readDate": "2025-08-01",
                            "readTime": "01:00",
                            "usage": "2.0",
                        },
                    ]
                },
            }
        }

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return canned

        api._api_call = mock_api_call  # type: ignore[assignment]
        result = await api.async_get_hourly_usage(account, datetime(2025, 8, 1))
        assert len(result) == 2
        assert result[0] == HourlyUsage(date="2025-08-01", time="00:00", kwh=1.5)
        assert result[1] == HourlyUsage(date="2025-08-01", time="01:00", kwh=2.0)

    @pytest.mark.asyncio
    async def test_no_interval_data(self) -> None:
        api = PacificPowerApi("u", "p")
        account = AccountInfo("C1", "01", "01", "Addr", site_idn=1, service_sequence=1)

        canned = {
            "getIntervalUsageForDateResponseBody": {
                "intervalDataExists": False,
            }
        }

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return canned

        api._api_call = mock_api_call  # type: ignore[assignment]
        result = await api.async_get_hourly_usage(account, datetime(2025, 8, 1))
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_items_without_required_fields(self) -> None:
        api = PacificPowerApi("u", "p")
        account = AccountInfo("C1", "01", "01", "Addr", site_idn=1, service_sequence=1)

        canned = {
            "getIntervalUsageForDateResponseBody": {
                "intervalDataExists": True,
                "response": {
                    "intervalDataResponse": [
                        {"readDate": "2025-08-01", "usage": "1.0"},
                        {
                            "readDate": "2025-08-01",
                            "readTime": "02:00",
                            "usage": "3.0",
                        },
                    ]
                },
            }
        }

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return canned

        api._api_call = mock_api_call  # type: ignore[assignment]
        result = await api.async_get_hourly_usage(account, datetime(2025, 8, 1))
        assert len(result) == 1
        assert result[0].time == "02:00"


# ---------------------------------------------------------------------------
# AMI meter check
# ---------------------------------------------------------------------------


class TestIsAmiMeter:
    """Test async_is_ami_meter."""

    @pytest.mark.asyncio
    async def test_returns_true_for_ami(self) -> None:
        api = PacificPowerApi("u", "p")
        api._web_user_id = "w"
        account = AccountInfo("C1", "01", "01", "Addr")

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return {"getMeterTypeResponseBody": {"isAMIMeter": True}}

        api._api_call = mock_api_call  # type: ignore[assignment]
        assert await api.async_is_ami_meter(account) is True

    @pytest.mark.asyncio
    async def test_returns_false_for_non_ami(self) -> None:
        api = PacificPowerApi("u", "p")
        api._web_user_id = "w"
        account = AccountInfo("C1", "01", "01", "Addr")

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return {"getMeterTypeResponseBody": {"isAMIMeter": False}}

        api._api_call = mock_api_call  # type: ignore[assignment]
        assert await api.async_is_ami_meter(account) is False

    @pytest.mark.asyncio
    async def test_returns_false_on_api_error(self) -> None:
        api = PacificPowerApi("u", "p")
        api._web_user_id = "w"
        account = AccountInfo("C1", "01", "01", "Addr")

        async def mock_api_call(path: str, body: dict | None) -> dict:
            raise PacificPowerApiError("fail")

        api._api_call = mock_api_call  # type: ignore[assignment]
        assert await api.async_is_ami_meter(account) is False

    @pytest.mark.asyncio
    async def test_returns_false_on_missing_key(self) -> None:
        api = PacificPowerApi("u", "p")
        api._web_user_id = "w"
        account = AccountInfo("C1", "01", "01", "Addr")

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return {"getMeterTypeResponseBody": {}}

        api._api_call = mock_api_call  # type: ignore[assignment]
        assert await api.async_is_ami_meter(account) is False


# ---------------------------------------------------------------------------
# User info
# ---------------------------------------------------------------------------


class TestGetUserInfo:
    """Test async_get_user_info."""

    @pytest.mark.asyncio
    async def test_sets_web_user_id(self) -> None:
        api = PacificPowerApi("u", "p")
        assert api._web_user_id is None

        async def mock_api_call(path: str, body: dict | None) -> dict:
            return {"webUserId": "user42", "email": "u@test.com"}

        api._api_call = mock_api_call  # type: ignore[assignment]
        result = await api.async_get_user_info()
        assert api._web_user_id == "user42"
        assert result["email"] == "u@test.com"


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------


class TestLogin:
    """Test async_login error wrapping."""

    @pytest.mark.asyncio
    async def test_wraps_client_error_as_connection_error(self) -> None:
        import aiohttp

        api = PacificPowerApi("u", "p")
        await api.async_start()

        async def mock_load_idm() -> None:
            raise aiohttp.ClientError("network down")

        api._load_idm = mock_load_idm  # type: ignore[assignment]
        with pytest.raises(PacificPowerConnectionError, match="Failed to connect"):
            await api.async_login()
        await api.async_stop()

    @pytest.mark.asyncio
    async def test_reraises_api_errors(self) -> None:
        api = PacificPowerApi("u", "p")
        await api.async_start()

        async def mock_load_idm() -> None:
            raise PacificPowerAuthError("bad creds")

        api._load_idm = mock_load_idm  # type: ignore[assignment]
        with pytest.raises(PacificPowerAuthError, match="bad creds"):
            await api.async_login()
        await api.async_stop()


# ---------------------------------------------------------------------------
# Cookie string helper
# ---------------------------------------------------------------------------


class TestCookieStr:
    """Test _cookie_str formatting."""

    def test_formats_cookies(self) -> None:
        api = PacificPowerApi("u", "p")
        api._cookies = {"a": "1", "b": "2"}
        result = api._cookie_str()
        assert "a=1" in result
        assert "b=2" in result
        assert "; " in result

    def test_empty_cookies(self) -> None:
        api = PacificPowerApi("u", "p")
        api._cookies = {}
        assert api._cookie_str() == ""
