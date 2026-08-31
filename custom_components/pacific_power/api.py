"""PacifiCorp (Pacific Power / Rocky Mountain Power) API client.

Authenticates via Azure AD B2C, performs an RSA/AES key exchange with the
portal's /idm/handshake endpoint, then makes encrypted API calls using
RSASSA-PKCS1-v1_5 signatures and AES-256-GCM encryption — the same
protocol the portal's Angular app uses.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urljoin

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .const import UTILITY_DOMAINS, UTILITY_PACIFIC_POWER

_LOGGER = logging.getLogger(__name__)

_SETTINGS_RE = re.compile(r"var\s+SETTINGS\s*=\s*(\{.*?\})\s*;", re.DOTALL)
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)


class PacificPowerApiError(Exception):
    """Base exception for Pacific Power API errors."""


class PacificPowerAuthError(PacificPowerApiError):
    """Authentication failed."""


class PacificPowerConnectionError(PacificPowerApiError):
    """Connection to Pacific Power failed."""


@dataclass
class AccountInfo:
    """Pacific Power account information."""

    customer_idn: str
    account_sequence: str
    agreement_sequence: str
    address: str
    site_idn: int = 0
    service_sequence: int = 0


@dataclass
class DailyUsage:
    """A single day's energy usage."""

    date: str
    kwh: float


@dataclass
class HourlyUsage:
    """A single hour's energy usage."""

    date: str
    time: str
    kwh: float


def _collect_cookies(resp: aiohttp.ClientResponse, cookies: dict[str, str]) -> None:
    for header_val in resp.headers.getall("Set-Cookie", []):
        parts = header_val.split(";")
        if parts:
            nv = parts[0].strip()
            eq = nv.find("=")
            if eq > 0:
                cookies[nv[:eq]] = nv[eq + 1 :]


class PacificPowerApi:
    """Client for PacifiCorp's encrypted API."""

    def __init__(
        self,
        username: str,
        password: str,
        utility: str = UTILITY_PACIFIC_POWER,
    ) -> None:
        self._username = username
        self._password = password

        info = UTILITY_DOMAINS.get(utility, UTILITY_DOMAINS[UTILITY_PACIFIC_POWER])
        self._base_url: str = info["base_url"]
        self._login_url: str = info["login_url"]
        self._subsidiary: str = info["subsidiary"]
        self._policy: str = info["policy"]

        self._cookies: dict[str, str] = {}
        self._session: aiohttp.ClientSession | None = None
        self._sign_key: rsa.RSAPrivateKey | None = None
        self._aes_key: bytes | None = None
        self._web_user_id: str | None = None

    async def async_start(self) -> None:
        """Create the HTTP session."""
        self._session = aiohttp.ClientSession(
            cookie_jar=aiohttp.DummyCookieJar(),
            headers={"User-Agent": _USER_AGENT},
        )

    async def async_stop(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def async_login(self) -> None:
        """Full login: load IDM page, key exchange, B2C auth."""
        assert self._session is not None
        try:
            await self._load_idm()
            await self._handshake()
            await self._b2c_login()
        except PacificPowerApiError:
            raise
        except aiohttp.ClientError as err:
            raise PacificPowerConnectionError(
                "Failed to connect to utility portal"
            ) from err

    async def async_get_accounts(self, web_user_id: str) -> list[AccountInfo]:
        """Discover accounts and their metered agreements."""
        acct_resp = await self._api_call(
            "/api/self-service/getAccountList",
            {
                "getAccountListRequestBody": {
                    "request": {"webUserID": web_user_id},
                    "domain": {"pacifiCorpSubsidiary": self._subsidiary},
                }
            },
        )
        web_accounts = (
            acct_resp.get("getAccountListResponseBody", {})
            .get("accountList", {})
            .get("webAccount", [])
        )
        if isinstance(web_accounts, dict):
            web_accounts = [web_accounts]

        accounts: list[AccountInfo] = []
        for wa in web_accounts:
            cid = str(wa.get("customer", {}).get("idn", ""))
            aseq = str(wa.get("sequence", ""))
            addr = wa.get("mailingAddressLine1", "").strip()
            if not cid or not aseq:
                continue
            ma_resp = await self._api_call(
                "/api/account/getMeteredAgreements",
                {
                    "getMeteredAgreementsRequestBody": {
                        "agreementRequest": {
                            "customerIDN": cid,
                            "accountSequence": aseq,
                        }
                    }
                },
            )
            metered_agreements = (
                ma_resp.get("getMeteredAgreementsResponseBody", {})
                .get("meteredAgreementList", {})
                .get("meteredAgreement", [])
            )
            if isinstance(metered_agreements, dict):
                metered_agreements = [metered_agreements]
            for ma in metered_agreements:
                if ma.get("agreementStatus") == "Active":
                    accounts.append(
                        AccountInfo(
                            customer_idn=cid,
                            account_sequence=aseq,
                            agreement_sequence=str(ma.get("agreementSequence", "")),
                            address=addr,
                            site_idn=ma.get("siteIDN", 0),
                            service_sequence=ma.get("serviceSequence", 0),
                        )
                    )
        return accounts

    async def async_is_ami_meter(self, account: AccountInfo) -> bool:
        """Check if the account has an AMI smart meter (hourly data)."""
        try:
            data = await self._api_call(
                "/api/energy-usage/getMeterType",
                {
                    "getMeterTypeRequestBody": {
                        "agreement": {
                            "customerIDN": account.customer_idn,
                            "accountSequence": account.account_sequence,
                            "agreementSequence": account.agreement_sequence,
                        },
                        "webUserid": self._web_user_id or "",
                    }
                },
            )
            return data.get("getMeterTypeResponseBody", {}).get("isAMIMeter", False)
        except PacificPowerApiError:
            return False

    async def async_get_hourly_usage(
        self,
        account: AccountInfo,
        date: datetime,
    ) -> list[HourlyUsage]:
        """Fetch hourly energy usage for a single day."""
        data = await self._api_call(
            "/api/energy-usage/getIntervalUsageForDate",
            {
                "getIntervalUsageForDateRequestBody": {
                    "request": {
                        "siteIDN": account.site_idn,
                        "registerType": "KWH",
                        "serviceSequence": account.service_sequence,
                        "readDate": date.strftime("%Y-%m-%d"),
                        "agreement": {
                            "customerIDN": account.customer_idn,
                            "accountSequence": account.account_sequence,
                            "agreementSequence": account.agreement_sequence,
                        },
                    }
                }
            },
        )
        resp = data.get("getIntervalUsageForDateResponseBody", {})
        if not resp.get("intervalDataExists"):
            return []
        items = (
            resp.get("response", {}).get("intervalDataResponse", [])
            if resp.get("response")
            else []
        )
        return [
            HourlyUsage(
                date=item["readDate"],
                time=item["readTime"],
                kwh=float(item.get("usage", 0)),
            )
            for item in items
            if "readDate" in item and "readTime" in item
        ]

    async def async_get_user_info(self) -> dict:
        """Get the logged-in user's profile."""
        data = await self._api_call("/api/user/me", None)
        self._web_user_id = data.get("webUserId")
        return data

    async def async_get_daily_usage(
        self,
        account: AccountInfo,
        start_date: datetime,
        end_date: datetime,
    ) -> list[DailyUsage]:
        """Fetch daily energy usage for a date range."""
        data = await self._api_call(
            "/api/energy-usage/getUsageForDateRange",
            {
                "getUsageForDateRangeRequestBody": {
                    "agreement": {
                        "customerIDN": account.customer_idn,
                        "accountSequence": account.account_sequence,
                        "agreementSequence": account.agreement_sequence,
                    },
                    "dateRange": {
                        "startDate": start_date.strftime("%Y-%m-%d"),
                        "endDate": end_date.strftime("%Y-%m-%d"),
                    },
                    "graphWindow": "daily",
                    "graphView": "usage",
                }
            },
        )
        items = (
            data.get("getUsageForDateRangeResponseBody", {})
            .get("dailyUsageList", {})
            .get("usgHistoryLineItem", [])
        )
        return [
            DailyUsage(
                date=item["usagePeriodEndDate"],
                kwh=float(item.get("kwhUsageQuantity", 0)),
            )
            for item in items
            if "usagePeriodEndDate" in item
        ]

    # -- Internal: login steps --

    async def _load_idm(self) -> None:
        async with self._session.get(
            f"{self._base_url}/idm/login",
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            _collect_cookies(resp, self._cookies)

    async def _handshake(self) -> None:
        self._sign_key = await asyncio.to_thread(
            rsa.generate_private_key, public_exponent=65537, key_size=4096
        )
        enc_key = await asyncio.to_thread(
            rsa.generate_private_key, public_exponent=65537, key_size=4096
        )
        sign_pub = base64.b64encode(
            self._sign_key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode()
        enc_pub = base64.b64encode(
            enc_key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).decode()

        async with self._session.post(
            f"{self._base_url}/idm/handshake",
            data=f"{sign_pub}:{enc_pub}".encode(),
            headers={
                "Cookie": self._cookie_str(),
                "X-XSRF-TOKEN": self._cookies.get("XSRF-TOKEN", ""),
                "Content-Type": "application/octet-stream",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            _collect_cookies(resp, self._cookies)
            if resp.status != 200:
                raise PacificPowerConnectionError(f"Key exchange failed: {resp.status}")
            encrypted_aes = await resp.text()

        self._aes_key = enc_key.decrypt(
            base64.b64decode(encrypted_aes),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

    async def _b2c_login(self) -> None:
        url = f"{self._base_url}/oauth2/authorization/{self._policy}"
        for _ in range(10):
            async with self._session.get(
                url,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Cookie": self._cookie_str()},
            ) as resp:
                _collect_cookies(resp, self._cookies)
                if resp.status in (301, 302, 303, 307, 308):
                    url = resp.headers["Location"]
                    if not url.startswith("http"):
                        url = urljoin(str(resp.url), url)
                    continue
                html = await resp.text()
                final_url = str(resp.url)
                break
        else:
            raise PacificPowerConnectionError("Too many redirects")

        match = _SETTINGS_RE.search(html)
        if not match:
            raise PacificPowerAuthError("Login page format changed")
        raw = re.sub(r",\s*}", "}", match.group(1))
        raw = re.sub(r",\s*]", "]", raw)
        try:
            settings = json.loads(raw)
        except json.JSONDecodeError as err:
            raise PacificPowerAuthError("Could not parse login page") from err

        trans_id = settings.get("transId", "")
        csrf = settings.get("csrf", "")
        tenant_path = settings.get("hosts", {}).get("tenant", "")
        policy = settings.get("hosts", {}).get("policy", "")
        if not trans_id or not csrf:
            raise PacificPowerAuthError("Missing auth tokens on login page")

        body = (
            f"request_type=RESPONSE"
            f"&signInName={quote(self._username, safe='')}"
            f"&password={quote(self._password, safe='')}"
        )
        async with self._session.post(
            f"{self._login_url}{tenant_path}/SelfAsserted?tx={trans_id}&p={policy}",
            data=body.encode("utf-8"),
            headers={
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": ("application/x-www-form-urlencoded; charset=UTF-8"),
                "Origin": self._login_url,
                "Referer": final_url,
                "Cookie": self._cookie_str(),
            },
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            _collect_cookies(resp, self._cookies)
            resp_body = await resp.text()
            if '"status":"200"' not in resp_body:
                raise PacificPowerAuthError("Invalid username or password")

        confirm_url = (
            f"{self._login_url}{tenant_path}"
            f"/api/CombinedSigninAndSignup/confirmed"
            f"?csrf_token={csrf}&tx={trans_id}&p={policy}"
        )
        for _ in range(10):
            async with self._session.get(
                confirm_url,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Cookie": self._cookie_str()},
            ) as resp:
                _collect_cookies(resp, self._cookies)
                if resp.status in (301, 302, 303, 307, 308):
                    confirm_url = resp.headers.get("Location", "")
                    if not confirm_url.startswith("http"):
                        confirm_url = urljoin(str(resp.url), confirm_url)
                    continue
                break
        else:
            raise PacificPowerConnectionError("Too many redirects")

    # -- Internal: encrypted API calls --

    async def _api_call(self, path: str, body: dict | None) -> dict:
        assert self._sign_key is not None and self._aes_key is not None
        plaintext = (
            json.dumps(body, separators=(",", ":")).encode("utf-8")
            if body is not None
            else b"null"
        )
        signature = self._sign_key.sign(plaintext, padding.PKCS1v15(), hashes.SHA256())
        iv = os.urandom(12)
        ciphertext = AESGCM(self._aes_key).encrypt(iv, plaintext, None)
        encrypted_body = (
            base64.b64encode(iv).decode() + base64.b64encode(ciphertext).decode()
        )

        async with self._session.post(
            f"{self._base_url}{path}",
            data=encrypted_body.encode("utf-8"),
            headers={
                "Cookie": self._cookie_str(),
                "X-XSRF-TOKEN": self._cookies.get("XSRF-TOKEN", ""),
                "X-WCSSS-Content-Signature": base64.b64encode(signature).decode(),
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Origin": self._base_url,
                "Referer": f"{self._base_url}/secure/my-account/energy-usage",
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            _collect_cookies(resp, self._cookies)
            text = await resp.text()

            if resp.status == 401:
                raise PacificPowerAuthError("Session expired")
            if resp.status == 403:
                raise PacificPowerApiError(f"Access denied: {path}")
            if resp.status == 400 and text:
                try:
                    msg = (
                        json.loads(text)
                        .get("fault", {})
                        .get("faultmessage", text[:200])
                    )
                except (json.JSONDecodeError, ValueError):
                    msg = text[:200]
                raise PacificPowerApiError(f"API error: {msg}")
            if resp.status != 200:
                raise PacificPowerApiError(f"{path}: HTTP {resp.status}")

            try:
                resp_iv = base64.b64decode(text[:16])
                resp_ct = base64.b64decode(text[16:])
                return json.loads(
                    AESGCM(self._aes_key)
                    .decrypt(resp_iv, resp_ct, None)
                    .decode("utf-8")
                )
            except Exception:
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, ValueError) as err:
                    raise PacificPowerApiError(
                        f"Unexpected response from {path}: {text[:200]}"
                    ) from err

    def _cookie_str(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self._cookies.items())
