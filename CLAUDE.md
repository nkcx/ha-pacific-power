# ha-pacific-power

Home Assistant integration (domain `pacific_power`) for Pacific Power and Rocky Mountain Power (PacifiCorp) energy usage. Public on GitHub (`nkcx/ha-pacific-power`), installed through HACS. Its only dependency is `cryptography`; no browser is needed.

## Releases

The integration has real users, so every release ships with full notes:

1. Bump `version` in `manifest.json` (semantic versioning: patch for fixes, minor for features, major for breaking changes).
2. Commit and push, and fix any HACS, hassfest or CI warning the run shows (only by-design warnings stay).
3. `gh release create vX.Y.Z` with notes covering what changed, what was fixed, and any breaking change or migration step.

Manifest keys: `domain`, `name`, then alphabetical (hassfest).

## API protocol

Reverse-engineered from the Angular customer portal.

1. Load `/idm/login` to get the `XSRF-TOKEN` cookie.
2. RSA-4096 key exchange: POST our public keys to `/idm/handshake`. The server returns an AES-256 key encrypted with our RSA-OAEP key. Key generation runs in `asyncio.to_thread` so it doesn't block the event loop.
3. Azure AD B2C login through `login.csapps.pacificpower.net`, policy `B2C_1A_PAC_SIGNIN`. Use `DummyCookieJar` plus manual cookie tracking: aiohttp's normal jar drops cookies between `csapps.pacificpower.net` and `login.csapps.pacificpower.net`.
4. Every API call is encrypted: the body is AES-256-GCM ciphertext, and the `X-WCSSS-Content-Signature` header is an RSASSA-PKCS1-v1_5 signature.

Call order matters:

1. `/api/user/me` (body `null`): returns `webUserId`.
2. `/api/self-service/getAccountList`: **must** be called first to set the account context, or every account endpoint returns 403.
3. `/api/account/getMeteredAgreements`: returns `agreementSequence`, `siteIDN`, `serviceSequence`. Discover `agreementSequence` here; never assume it.
4. `/api/energy-usage/getMeterType`: `isAMIMeter` says whether hourly data exists.
5. `/api/energy-usage/getIntervalUsageForDate`: hourly kWh (AMI only; needs `siteIDN`, `serviceSequence`, `registerType="KWH"`).
6. `/api/energy-usage/getUsageForDateRange`: daily kWh, the fallback for non-AMI meters.

## Quirks

- `getUsageForDateRange` only returns data inside one billing cycle. A range across cycles returns only the first, so request month by month.
- `getIntervalUsageForDate` reports end of day as `"24:00"`. Use `timedelta(hours=hour)`, not `replace(hour=hour)`.
- ISO dates (`2026-07-01`) work for both usage endpoints.
- Timestamps are in the utility's local time zone (Pacific or Mountain), not UTC. Parse them with `ZoneInfo` before writing HA statistics.
- `getGreenButtonData` returns a 400 validation error even with the portal's exact request, so it isn't used.
- Account numbers look like `XXXXXXXX-XXX`: `customerIDN` is the first 8 digits, `accountSequence` the last 3.
- The initial backfill is capped at 30 days. The utility keeps only about 90 days of hourly data, and 365 days timed out HA's setup.
- Hourly refreshes overlap 3 days (daily ones 30) to keep API calls down.
