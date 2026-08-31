# Pacific Power / Rocky Mountain Power for Home Assistant

A [Home Assistant](https://www.home-assistant.io/) custom integration that imports daily energy usage data from [Pacific Power](https://www.pacificpower.net/) and [Rocky Mountain Power](https://www.rockymountainpower.net/) (both PacifiCorp subsidiaries) into the HA Energy Dashboard.

## Features

- Supports both Pacific Power and Rocky Mountain Power
- Automatic login and data fetch — no manual downloads
- Daily kWh consumption data inserted into HA long-term statistics
- Hourly data for AMI smart meters
- Works with the Energy Dashboard out of the box
- Auto-discovers accounts and metered agreements during setup
- Refreshes every 12 hours
- Pure Python — no browser or external service dependencies

## How It Works

PacifiCorp's web portals encrypt all API traffic using RSA signatures and AES-256-GCM encryption. This integration reverse-engineers that protocol:

1. Authenticates via Azure AD B2C (PacifiCorp's login provider)
2. Performs an RSA-4096 key exchange with the portal's `/idm/handshake` endpoint
3. Makes encrypted, signed API calls to fetch energy usage data
4. Inserts the data as external statistics in Home Assistant's recorder

## Installation

### HACS (Recommended)

1. Open HACS → **Custom repositories**
2. Add `https://github.com/nkcx/ha-pacific-power` as an **Integration**
3. Search for "Pacific Power" and install
4. Restart Home Assistant

### Manual

Copy `custom_components/pacific_power` to your HA `custom_components` folder and restart.

## Setup

1. **Settings** → **Devices & Services** → **Add Integration** → **Pacific Power**
2. Select your utility — **Pacific Power** or **Rocky Mountain Power**
3. Enter your username and password
4. Your account is auto-discovered — select it if you have multiple

## Energy Dashboard

After the first data fetch:

1. **Settings** → **Dashboards** → **Energy**
2. **Grid consumption** → **Add consumption**
3. Select the `pacific_power:...energy_consumption` statistic

## Requirements

- A Pacific Power or Rocky Mountain Power account with online access
- MFA must be disabled on the account
- `cryptography` Python package (installed automatically)

## Removal

To remove the integration:

1. **Settings** → **Devices & Services**
2. Find your Pacific Power / Rocky Mountain Power entry
3. Click the three-dot menu → **Delete**

This removes the integration and its entities. Long-term statistics data already recorded in the HA database is preserved.

## Disclaimer

This is an independent, community-developed project and is not affiliated with, endorsed by, or sponsored by Pacific Power, Rocky Mountain Power, PacifiCorp, or Berkshire Hathaway Energy. "Pacific Power" and "Rocky Mountain Power" are trademarks of PacifiCorp. Use of these names is solely for identification purposes.

This integration accesses PacifiCorp's web portals using your own account credentials. Use it at your own risk. The authors are not responsible for any issues arising from its use, including but not limited to account access problems or terms of service violations.

## License

MIT
