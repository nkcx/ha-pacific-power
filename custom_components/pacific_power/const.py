"""Constants for the Pacific Power / Rocky Mountain Power integration."""

from typing import Final

DOMAIN: Final = "pacific_power"

CONF_CUSTOMER_IDN = "customer_idn"
CONF_ACCOUNT_SEQUENCE = "account_sequence"
CONF_AGREEMENT_SEQUENCE = "agreement_sequence"
CONF_SERVICE_ADDRESS = "service_address"
CONF_TIMEZONE = "timezone"

CONF_UTILITY = "utility"
UTILITY_PACIFIC_POWER = "pacific_power"
UTILITY_ROCKY_MOUNTAIN = "rocky_mountain_power"

UTILITY_DOMAINS = {
    UTILITY_PACIFIC_POWER: {
        "name": "Pacific Power",
        "base_url": "https://csapps.pacificpower.net",
        "login_url": "https://login.csapps.pacificpower.net",
        "subsidiary": "PacificPower",
        "policy": "B2C_1A_PAC_SIGNIN",
    },
    UTILITY_ROCKY_MOUNTAIN: {
        "name": "Rocky Mountain Power",
        "base_url": "https://csapps.rockymountainpower.net",
        "login_url": "https://login.csapps.rockymountainpower.net",
        "subsidiary": "RockyMountainPower",
        "policy": "B2C_1A_RMP_SIGNIN",
    },
}
