"""Constants for the Vorwerk Kobold integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "kobold"

CONF_MARKET: Final = "market"
CONF_ID_TOKEN: Final = "id_token"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_OTP: Final = "otp"

# Cloud endpoints used by the MyKobold app
AUTH_HOST: Final = "https://mykobold.eu.auth0.com"
AUTH_CLIENT_ID: Final = "FPSBig7ePFvAE6q99cEDROM8gYUTygkD"
ORBITAL_HOST: Final = "https://orbital.ksecosys.com"
COMPANION_HOST: Final = "https://api-2-prod.companion.kobold.vorwerk.com"
COMPANION_WS_URL: Final = "wss://api-2-prod.companion.kobold.vorwerk.com/api/ws"
VENDOR_ID: Final = 3

APP_VERSION: Final = "3.12.1"
APP_BUILD: Final = "40408"
APP_OS: Final = "android"
APP_OS_VERSION: Final = "11"
APP_USER_AGENT: Final = "okhttp/5.1.0"

MARKETS: Final = {
    "de": "Deutschland / Österreich / Schweiz",
    "en": "United Kingdom / International",
    "fr": "France",
    "it": "Italia",
    "es": "España",
}
DEFAULT_MARKET: Final = "de"

# Fallback refresh: re-request the state over the socket and reload maps
REFRESH_INTERVAL: Final = timedelta(minutes=10)
# Without any message for this long the robot is reported unavailable
STALE_AFTER: Final = timedelta(minutes=30)
HEARTBEAT_INTERVAL: Final = 30
RECONNECT_MAX_DELAY: Final = 300
