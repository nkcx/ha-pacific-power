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

### Setup Parameters

| Field | Description |
|-------|-------------|
| **Utility** | Your PacifiCorp utility brand — Pacific Power (OR, WA, CA) or Rocky Mountain Power (UT, WY, ID). |
| **Username** | The email address you use to log in to the utility's web portal. |
| **Password** | Your portal password. Stored in HA's encrypted config store. |

### Prerequisites

- An active Pacific Power or Rocky Mountain Power account with online portal access
- Multi-factor authentication (MFA) **must be disabled** on the account — the integration cannot handle MFA challenges
- The `cryptography` Python package (installed automatically by HA)

## What You Get

### Energy Statistics

The integration creates a long-term statistic (`pacific_power:..._energy_consumption`) that tracks cumulative kWh consumption. This is the primary data source for the Energy Dashboard.

- **AMI smart meters** — hourly readings with per-hour kWh values
- **Non-AMI meters** — daily readings with per-day kWh totals

The meter type is auto-detected. If your meter supports hourly data, the integration will use it automatically.

### Diagnostic Sensors

| Sensor | Description |
|--------|-------------|
| **Last data received** | Timestamp of the most recent usage reading from the utility |
| **Last updated** | Timestamp of the last successful data refresh |

Both sensors are marked as diagnostic entities.

## Energy Dashboard

After the first data fetch:

1. **Settings** → **Dashboards** → **Energy**
2. **Grid consumption** → **Add consumption**
3. Select the `pacific_power:...energy_consumption` statistic

## Data Updates

The integration polls PacifiCorp's servers every **12 hours**. Each refresh:

1. Logs in to the utility portal
2. Checks whether the meter is AMI (hourly) or non-AMI (daily)
3. Fetches usage data with an overlap window to catch late-arriving readings
4. Inserts or updates the data as HA long-term statistics

Usage data from the utility is typically delayed by **24–48 hours** — yesterday's usage usually appears sometime today. The 12-hour polling interval is conservative to avoid unnecessary load on PacifiCorp's servers.

## Use Cases

- **Energy Dashboard** — track daily and monthly electricity consumption directly in HA
- **Usage alerts** — trigger automations when daily consumption exceeds a threshold
- **Seasonal comparisons** — use HA's statistics graphs to compare usage across months
- **Cost estimation** — combine with a utility rate helper to estimate electricity costs

## Automation Examples

Alert when daily energy usage is high (using a threshold on the statistic):

```yaml
automation:
  - alias: "High energy usage alert"
    trigger:
      - platform: numeric_state
        entity_id: sensor.pacific_power_123_main_st_last_data_received
        # Triggers whenever new data arrives; check usage in the condition
    condition:
      - condition: template
        value_template: >
          {{ states('sensor.pacific_power_123_main_st_last_data_received') != 'unavailable' }}
    action:
      - service: notify.mobile_app
        data:
          title: "High Energy Usage"
          message: "New energy data received — check the Energy Dashboard for details."
```

## Known Limitations

- **MFA must be disabled** — the integration authenticates programmatically and cannot handle multi-factor challenges
- **Data is not real-time** — usage data from the utility is typically delayed 24–48 hours
- **Reverse-engineered API** — this integration uses the same encrypted protocol as PacifiCorp's web portal; if PacifiCorp changes their portal, the integration may break until updated
- **One account per entry** — each config entry tracks a single account/meter; add multiple entries for multiple accounts
- **No cost data** — the API provides kWh consumption only, not dollar amounts; use HA's energy cost tracking features to estimate costs based on your rate

## Troubleshooting

### "Invalid username or password"
- Verify your credentials work on the utility's website ([Pacific Power](https://www.pacificpower.net/) or [Rocky Mountain Power](https://www.rockymountainpower.net/))
- Make sure you selected the correct utility in the setup dropdown
- Check that MFA is disabled on your account

### "Unable to connect"
- PacifiCorp's servers may be temporarily down — try again later
- Check your HA instance has outbound internet access

### No data showing in the Energy Dashboard
- The first data fetch happens after setup; wait up to 12 hours for the initial poll
- Force a refresh: **Settings** → **Devices & Services** → find the entry → **Reload**
- Usage data from the utility is delayed 24–48 hours — very recent usage won't appear yet

### Only daily data, no hourly
- Hourly data requires an AMI smart meter — not all meters support it
- The integration auto-detects the meter type; if yours is non-AMI, only daily totals are available
- Contact your utility to ask about meter upgrades

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
