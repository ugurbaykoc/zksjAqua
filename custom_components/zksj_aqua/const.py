"""Constants for the ZKSJ AQUA integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "zksj_aqua"

CONF_DEVICE_ID: Final = "device_id"
CONF_LOCAL_KEY: Final = "local_key"
CONF_PROTOCOL_VERSION: Final = "protocol_version"

# Cloud (MQTT) transport. The pump ignores local control entirely, so for
# this product these are what actually carry data points; see cloud.py.
CONF_TRANSPORT: Final = "transport"
CONF_MQTT_BROKER: Final = "mqtt_broker"
CONF_MQTT_PORT: Final = "mqtt_port"
CONF_MQTT_CLIENT_ID: Final = "mqtt_client_id"
CONF_MQTT_USERNAME: Final = "mqtt_username"
CONF_MQTT_PASSWORD: Final = "mqtt_password"
CONF_MQTT_SOURCE_ID: Final = "mqtt_source_id"

TRANSPORT_LOCAL: Final = "local"
TRANSPORT_CLOUD: Final = "cloud"

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

# Tuya's MQTT broker. Which regional host to use is decided by where the
# device was registered, not where you are: this pump is on a ZKSJ OEM
# account in Tuya's China data centre, which is what the app connects to.
DEFAULT_MQTT_BROKER: Final = "m1.tuyacn.com"
DEFAULT_MQTT_PORT: Final = 8883

# Topics are named from the app's point of view, so "out" is the one we
# publish commands to and "in" is the one the pump reports state on.
CLOUD_TOPIC_OUT: Final = "smart/mb/out/{device_id}"
CLOUD_TOPIC_IN: Final = "smart/mb/in/{device_id}"

# Frame envelope: version tag, then crc32 ‖ seq ‖ src ‖ AES-128-ECB(json).
MQTT_FRAME_PREFIX: Final = b"2.2"
# The app stamps its writes as protocol 5; the pump answers with 4.
CLOUD_PROTOCOL_PUBLISH: Final = 5

DEFAULT_UPDATE_INTERVAL: Final = 30.0
CONNECTION_TIMEOUT: Final = 5.0

# How long to let the pump answer the opening state request before the first
# refresh reports what it has. Long enough for a cloud round trip, short
# enough not to stall setup.
CLOUD_PRIME_DELAY: Final = 2.0

# What the app sends when you tap feed: the pump runs its own countdown and
# returns to the program on its own.
DEFAULT_FEED_DURATION: Final = 600
