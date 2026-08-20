"""Finding pumps on the local network.

Tuya devices announce themselves by UDP broadcast, and the announcement is
encrypted with a key that is the same on every Tuya device ever made -- so
anyone on the network can read it.  It carries the device id, its address and
its protocol version.

It does not carry the local key, and nothing on the network does: that value
lives in the Tuya account the pump was paired to.  So discovery cannot
finish the job, but it can reduce it to one field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import tinytuya
import tinytuya.scanner
from homeassistant.core import HomeAssistant

from .const import SMART_WAVE_PRODUCT_IDS

_LOGGER = logging.getLogger(__name__)

# Long enough for a device that broadcasts every few seconds to be heard,
# short enough not to stall the config flow.
SCAN_SECONDS = 8


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    """A Tuya device seen broadcasting on the network."""

    device_id: str
    host: str
    product_id: str
    version: str

    @property
    def is_wave_pump(self) -> bool:
        return self.product_id in SMART_WAVE_PRODUCT_IDS

    @property
    def label(self) -> str:
        kind = "ZKSJ wave pump" if self.is_wave_pump else "Tuya device"
        return f"{kind} — {self.host} ({self.device_id})"


def _scan() -> list[DiscoveredDevice]:
    # poll=False: polling reads data points, which needs the local keys we do
    # not have yet, and turns a quick listen into a long series of timeouts.
    # scanner.devices() rather than tinytuya.deviceScan(): only the former
    # takes a scan duration, and an unbounded scan blocks the config flow.
    found = tinytuya.scanner.devices(
        verbose=False,
        poll=False,
        color=False,
        scantime=SCAN_SECONDS,
        discover=True,
    )

    devices: list[DiscoveredDevice] = []
    for entry in found.values():
        device_id = entry.get("gwId") or entry.get("id")
        host = entry.get("ip")
        if not device_id or not host:
            continue
        devices.append(
            DiscoveredDevice(
                device_id=device_id,
                host=host,
                product_id=entry.get("productKey") or "",
                version=str(entry.get("version") or ""),
            )
        )
    return devices


async def async_discover(hass: HomeAssistant) -> list[DiscoveredDevice]:
    """Listen for Tuya broadcasts, pumps first.

    Devices whose product id the vendor app treats as a wave pump are sorted
    to the top, but everything found is offered: the product id list comes
    from one app version and a newer pump may not be on it.
    """
    try:
        devices = await hass.async_add_executor_job(_scan)
    except OSError as err:
        # Another process already holds the Tuya broadcast ports, or the
        # container has no access to the LAN broadcast domain.
        _LOGGER.debug("Tuya discovery could not listen: %s", err)
        return []

    devices.sort(key=lambda device: (not device.is_wave_pump, device.host))
    _LOGGER.debug(
        "Discovery found %d Tuya device(s), %d matching a known wave pump",
        len(devices),
        sum(device.is_wave_pump for device in devices),
    )
    return devices
