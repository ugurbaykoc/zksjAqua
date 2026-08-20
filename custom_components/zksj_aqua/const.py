"""Constants for the ZKSJ AQUA integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "zksj_aqua"

CONF_DEVICE_ID: Final = "device_id"
CONF_LOCAL_KEY: Final = "local_key"
CONF_PROTOCOL_VERSION: Final = "protocol_version"

MANUFACTURER: Final = "ZKSJ (Zhongke)"
MODEL: Final = "Smart Wave Pump"

# Tuya product ids that the vendor app treats as wave pumps
# (ProductHelper.SMART_WAVE_PID).
SMART_WAVE_PRODUCT_IDS: Final = (
    "icgtkgzy9gvaaixh",
    "lwlbhsifgw7ec8nk",
    "2twbidw8gmdxup5c",
)

# Protocol versions tinytuya knows, newest first: a pump that answers on one
# will not answer on the others, so probing in this order finds it fastest.
PROTOCOL_VERSIONS: Final = ("3.5", "3.4", "3.3", "3.2", "3.1")
DEFAULT_PROTOCOL_VERSION: Final = "3.3"

# The port Tuya devices listen on for local control.  Reachable without
# credentials, which is what makes the monitor-only mode possible.
TUYA_LOCAL_PORT: Final = 6668

DEFAULT_UPDATE_INTERVAL: Final = 30.0
CONNECTION_TIMEOUT: Final = 5.0

# What the app sends when you tap feed: the pump runs its own countdown and
# returns to the program on its own.
DEFAULT_FEED_DURATION: Final = 600
