"""Wire protocol for ZKSJ AQUA wave pumps.

Everything the pump understands is described here and nowhere else: the GATT
characteristics, the framing, and the mode/speed encoding.  The rest of the
integration talks to a :class:`DeviceProfile` and never touches raw bytes, so
adding a second pump generation means adding a profile, not editing platforms.

The constants come out of the vendor Android app
(``com.zhongkesz.smartaquariumpro``).  Until a profile is filled in from that
app, :func:`get_profile` raises and the config entry fails with a message that
says so -- an integration that guesses at a checksum would silently write
garbage to a pump running someone's tank.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Final

_LOGGER = logging.getLogger(__name__)


class ZksjError(Exception):
    """Base class for protocol errors."""


class ProtocolNotAvailable(ZksjError):
    """No decoded protocol profile is available for this device."""


class FrameError(ZksjError):
    """A frame from the pump was malformed or failed its checksum."""


@dataclass(frozen=True, slots=True)
class WaveMode:
    """One selectable wave pattern.

    ``key`` is what Home Assistant stores and what translations key off, so it
    must stay stable even if the vendor renames the mode.  ``code`` is the
    on-the-wire value and may differ between pump generations.
    """

    key: str
    code: int


@dataclass(frozen=True, slots=True)
class PumpState:
    """A snapshot of the pump, as last reported by it."""

    power: bool | None = None
    mode: str | None = None
    speed: int | None = None
    feed_active: bool | None = None
    feed_remaining: int | None = None
    rpm: int | None = None


class Codec(ABC):
    """Turns intents into frames and notifications back into state."""

    @abstractmethod
    def encode_query(self) -> bytes:
        """Ask the pump for its current state."""

    @abstractmethod
    def encode_power(self, on: bool) -> bytes:
        """Start or stop the pump."""

    @abstractmethod
    def encode_speed(self, level: int) -> bytes:
        """Set the flow level."""

    @abstractmethod
    def encode_mode(self, mode: WaveMode) -> bytes:
        """Switch the wave pattern."""

    @abstractmethod
    def encode_feed(self, on: bool) -> bytes:
        """Enter or leave feed mode."""

    @abstractmethod
    def decode(self, payload: bytes, previous: PumpState) -> PumpState:
        """Fold a notification into the known state.

        Pumps report partial state, so unparsed fields must be carried over
        from ``previous`` rather than reset to ``None``.
        """


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Everything model-specific about one pump generation."""

    key: str
    model: str
    service_uuid: str
    write_uuid: str
    notify_uuid: str
    codec: Codec
    modes: tuple[WaveMode, ...]
    min_speed: int
    max_speed: int
    # Advertised names that identify this generation.  Matched case
    # insensitively against the BLE local name, with a trailing ``*``
    # meaning prefix.
    local_names: tuple[str, ...] = field(default=())

    def mode_by_key(self, key: str) -> WaveMode:
        for mode in self.modes:
            if mode.key == key:
                return mode
        raise ProtocolNotAvailable(f"{self.model} has no wave mode {key!r}")

    def matches_name(self, local_name: str | None) -> bool:
        if not local_name:
            return False
        candidate = local_name.strip().lower()
        for pattern in self.local_names:
            pattern = pattern.lower()
            if pattern.endswith("*"):
                if candidate.startswith(pattern[:-1]):
                    return True
            elif candidate == pattern:
                return True
        return False


# Populated from the decompiled vendor app.  See docs/PROTOCOL.md.
PROFILES: Final[dict[str, DeviceProfile]] = {}


def get_profile(key: str) -> DeviceProfile:
    """Look up a profile by key."""
    try:
        return PROFILES[key]
    except KeyError:
        raise ProtocolNotAvailable(
            f"No decoded ZKSJ protocol profile named {key!r}. "
            "See docs/PROTOCOL.md for how profiles are derived from the "
            "vendor app."
        ) from None


def match_profile(local_name: str | None) -> DeviceProfile | None:
    """Find the profile for an advertised name, if one claims it."""
    for profile in PROFILES.values():
        if profile.matches_name(local_name):
            return profile
    return None
