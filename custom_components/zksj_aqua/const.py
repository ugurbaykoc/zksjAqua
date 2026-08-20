"""Constants for the ZKSJ AQUA integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "zksj_aqua"

CONF_PROFILE: Final = "profile"

MANUFACTURER: Final = "ZKSJ (Zhongke)"

# The pump drops the link if we sit idle on it, and holding a connection open
# blocks the phone app from pairing.  We connect on demand, run the command,
# then keep the link warm for a short grace period so that a burst of changes
# from the UI does not pay the connect cost every time.
DISCONNECT_DELAY: Final = 30.0

# Nothing about the pump changes without us asking, but the connection itself
# is unreliable enough that we re-read state periodically to notice a pump that
# was power cycled or driven from its physical buttons.
UPDATE_INTERVAL: Final = 60.0

# A single command round trip: write, then wait for the pump to notify back.
COMMAND_TIMEOUT: Final = 10.0
