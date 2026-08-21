"""Wire format for ZKSJ AQUA wave pumps.

The pump is a Tuya device, but almost nothing about it is a standard Tuya
data point: the interesting state lives in raw hex DPs that generic Tuya
integrations pass through untouched, which is why the pump shows up as an
inert switch (or not at all) under them.  This module is the missing half --
it encodes and decodes those blobs.

Everything here was derived from the vendor Android app,
``com.zhongkesz.smartaquariumpro`` 1.7.0, specifically
``com.zhongkesz.smartaquariumpro.zhongke.smart_wave.beans``.  All multi-byte
fields are big-endian.

Data points
-----------

===  ==============  =====  =====================================================
DP   Name            R/W    Payload
===  ==============  =====  =====================================================
101  cur_mode        R/W    N x 12-byte :class:`WaveSegment` -- the active program
102  cur_power       R      1 byte, current output 0-100 %
103  feed            R/W    write ``state,duration``; read adds countdown & power
104  preview         W      11 bytes, run one segment briefly without saving
105  wave_action     R/W    ``action,identity`` + N x 12-byte segments
106  get_mode        W      ``identity,query_dp`` -- asks the pump to report a DP
107  sync_time       W      4 bytes, milliseconds since local midnight
108  switch          R/W    bool, pump on/off
===  ==============  =====  =====================================================
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Final

DP_CUR_MODE: Final = "101"
DP_CUR_POWER: Final = "102"
DP_FEED: Final = "103"
DP_PREVIEW: Final = "104"
DP_WAVE_ACTION: Final = "105"
DP_GET_MODE: Final = "106"
DP_SYNC_TIME: Final = "107"
DP_SWITCH: Final = "108"

SEGMENT_SIZE: Final = 12
SECONDS_PER_DAY: Final = 86400


class ZksjProtocolError(Exception):
    """A payload from the pump did not match the format the app uses."""


class WaveType(IntEnum):
    """Wave patterns, numbered as the app's ``WaveOptionType`` numbers them."""

    CONSTANT = 0
    PULSE = 1
    GYRE = 2
    NUTRIENT_TRANSPORT = 3
    TIDAL_SWELL = 4
    RANDOM = 5


@dataclass(frozen=True, slots=True)
class WaveLimits:
    """The ranges the app enforces in its own UI for one wave type.

    Worth honouring: the pump is not known to range-check these, and the app
    is the only evidence of what it was built to accept.
    """

    power: tuple[int, int]
    freq: tuple[int, int]
    pwm: tuple[int, int]

    @property
    def has_freq(self) -> bool:
        return self.freq != (0, 0)

    @property
    def has_pwm(self) -> bool:
        return self.pwm != (0, 0)


LIMITS: Final[dict[WaveType, WaveLimits]] = {
    WaveType.CONSTANT: WaveLimits(power=(0, 100), freq=(0, 0), pwm=(0, 0)),
    WaveType.PULSE: WaveLimits(power=(10, 100), freq=(3, 100), pwm=(30, 70)),
    WaveType.GYRE: WaveLimits(power=(10, 100), freq=(20, 300), pwm=(30, 70)),
    WaveType.NUTRIENT_TRANSPORT: WaveLimits(power=(50, 100), freq=(0, 0), pwm=(0, 0)),
    WaveType.TIDAL_SWELL: WaveLimits(power=(50, 100), freq=(0, 0), pwm=(0, 0)),
    WaveType.RANDOM: WaveLimits(power=(0, 0), freq=(0, 0), pwm=(0, 0)),
}

# Byte 1 of a segment is overloaded: for the two waveforms that have a duty
# cycle it carries pwm biased by 128, and for every other type it carries the
# segment's identity.
_PWM_BIAS: Final = 128
_PWM_TYPES: Final = frozenset({WaveType.PULSE, WaveType.GYRE})


@dataclass(slots=True)
class WaveSegment:
    """One stretch of the day running one waveform.

    A pump's program (DP 101) is a list of these.  Times are seconds since
    local midnight; a segment covering the whole day is how the app expresses
    "just run this pattern".
    """

    type: WaveType = WaveType.CONSTANT
    identity: int = 0
    min_power: int = 0
    max_power: int = 100
    freq: int = 0
    pwm: int = 0
    start_time: int = 0
    end_time: int = SECONDS_PER_DAY

    @property
    def limits(self) -> WaveLimits:
        return LIMITS[self.type]

    @property
    def has_pwm(self) -> bool:
        return self.type in _PWM_TYPES

    def encode(self) -> bytes:
        raw = bytearray(SEGMENT_SIZE)
        raw[0] = self.type & 0xFF
        # pwm and identity share byte 1; which one is written depends on type.
        raw[1] = (
            (self.pwm + _PWM_BIAS) & 0xFF if self.has_pwm else self.identity & 0xFF
        )
        raw[2] = self.min_power & 0xFF
        raw[3] = self.max_power & 0xFF
        raw[4:6] = self.freq.to_bytes(2, "big")
        raw[6:9] = self.start_time.to_bytes(3, "big")
        raw[9:12] = self.end_time.to_bytes(3, "big")
        return bytes(raw)

    @classmethod
    def decode(cls, raw: bytes) -> WaveSegment:
        if len(raw) != SEGMENT_SIZE:
            raise ZksjProtocolError(
                f"wave segment must be {SEGMENT_SIZE} bytes, got {len(raw)}"
            )
        try:
            wave_type = WaveType(raw[0])
        except ValueError as err:
            raise ZksjProtocolError(f"unknown wave type {raw[0]}") from err

        has_pwm = wave_type in _PWM_TYPES
        return cls(
            type=wave_type,
            identity=0 if has_pwm else raw[1],
            min_power=raw[2],
            max_power=raw[3],
            freq=int.from_bytes(raw[4:6], "big"),
            pwm=raw[1] - _PWM_BIAS if has_pwm else 0,
            start_time=int.from_bytes(raw[6:9], "big"),
            end_time=int.from_bytes(raw[9:12], "big"),
        )

    @classmethod
    def all_day(cls, wave_type: WaveType, **kwargs: int) -> WaveSegment:
        """A segment covering the whole day -- "just run this pattern"."""
        return cls(type=wave_type, start_time=0, end_time=SECONDS_PER_DAY, **kwargs)


def encode_program(segments: list[WaveSegment]) -> str:
    """DP 101 payload for a program."""
    if not segments:
        raise ZksjProtocolError("a program needs at least one segment")
    return b"".join(segment.encode() for segment in segments).hex()


def decode_program(payload: str) -> list[WaveSegment]:
    """Parse a DP 101 payload."""
    raw = _unhex(payload, DP_CUR_MODE)
    if not raw or len(raw) % SEGMENT_SIZE:
        raise ZksjProtocolError(
            f"DP {DP_CUR_MODE} payload of {len(raw)} bytes is not a whole "
            f"number of {SEGMENT_SIZE}-byte segments"
        )
    return [
        WaveSegment.decode(raw[offset : offset + SEGMENT_SIZE])
        for offset in range(0, len(raw), SEGMENT_SIZE)
    ]


def decode_power(payload: str) -> int:
    """Parse a DP 102 payload: the pump's current output, as a percentage."""
    raw = _unhex(payload, DP_CUR_POWER)
    if len(raw) != 1:
        raise ZksjProtocolError(f"DP {DP_CUR_POWER} should be 1 byte, got {len(raw)}")
    return raw[0]


@dataclass(frozen=True, slots=True)
class FeedState:
    """DP 103 as the pump reports it."""

    active: bool
    duration: int
    countdown: int
    power: int


def encode_feed(*, start: bool, duration: int, power: int = 0) -> str:
    """DP 103 payload.

    The app writes six bytes and the pump answers with ten -- the extra four
    being the countdown it is now running.
    """
    return bytes(
        [1 if start else 0, *duration.to_bytes(4, "big"), power & 0xFF]
    ).hex()


def decode_feed(payload: str) -> FeedState:
    """Parse a DP 103 report."""
    raw = _unhex(payload, DP_FEED)
    if len(raw) < 10:
        raise ZksjProtocolError(
            f"DP {DP_FEED} report should be 10 bytes, got {len(raw)}"
        )
    return FeedState(
        active=raw[0] == 1,
        duration=int.from_bytes(raw[1:5], "big"),
        countdown=int.from_bytes(raw[5:9], "big"),
        power=raw[9],
    )


def encode_preview(segment: WaveSegment, *, start: bool, duration: int) -> str:
    """DP 104 payload: run one waveform briefly without changing the program."""
    raw = bytearray(11)
    raw[0] = 1 if start else 0
    raw[1] = segment.type & 0xFF
    raw[2] = (
        (segment.pwm + _PWM_BIAS) & 0xFF if segment.has_pwm else segment.identity & 0xFF
    )
    raw[3] = segment.min_power & 0xFF
    raw[4] = segment.max_power & 0xFF
    raw[5:7] = segment.freq.to_bytes(2, "big")
    raw[7:11] = duration.to_bytes(4, "big")
    return bytes(raw).hex()


def encode_get_mode(query_dp: int, identity: int = 0) -> str:
    """DP 106 payload: ask the pump to report ``query_dp``.

    The pump does not volunteer the program on connect, so this is how a
    fresh coordinator gets a first picture of it.
    """
    return bytes([identity & 0xFF, *query_dp.to_bytes(2, "big")]).hex()


def encode_sync_time(seconds_since_midnight: int, milliseconds: int = 0) -> str:
    """DP 107 payload.

    The pump keeps its own clock to run time-based programs, and nothing
    resets it across a power cut -- so a program written yesterday plays at
    the wrong time until something syncs it.
    """
    value = seconds_since_midnight * 1000 + milliseconds
    return value.to_bytes(4, "big").hex()


def _unhex(payload: str, dp: str) -> bytes:
    try:
        return bytes.fromhex(payload)
    except (ValueError, TypeError) as err:
        raise ZksjProtocolError(f"DP {dp} payload is not hex: {payload!r}") from err


def clamp(value: int, bounds: tuple[int, int]) -> int:
    """Pull a value inside a wave type's permitted range."""
    low, high = bounds
    return max(low, min(high, value))


def active_segment_index(segments: list[WaveSegment], seconds_of_day: int) -> int:
    """Which segment of a program is running right now.

    Segments may be listed out of order and need not tile the day, so this
    falls back to the first one rather than reporting nothing -- a pump with
    a gap in its program is still running *something*.
    """
    for index, segment in enumerate(segments):
        start, end = segment.start_time, segment.end_time
        if start <= end:
            if start <= seconds_of_day < end:
                return index
        # A segment that wraps past midnight is two ranges, not one.
        elif seconds_of_day >= start or seconds_of_day < end:
            return index
    return 0


def retyped(segment: WaveSegment, new_type: WaveType) -> WaveSegment:
    """Recast a segment as another wave type, keeping what still applies.

    Each type has its own accepted ranges, so a value that was legal for the
    old type is clamped rather than carried over blindly -- switching from
    constant at 0 % to nutrient transport, whose floor is 50 %, must not
    leave the pump asking for an out-of-range 0.
    """
    limits = LIMITS[new_type]
    return WaveSegment(
        type=new_type,
        identity=segment.identity,
        min_power=clamp(segment.min_power, limits.power),
        max_power=clamp(segment.max_power, limits.power),
        freq=clamp(segment.freq, limits.freq) if limits.has_freq else 0,
        pwm=clamp(segment.pwm, limits.pwm) if limits.has_pwm else 0,
        start_time=segment.start_time,
        end_time=segment.end_time,
    )


def replace_segment(
    segments: list[WaveSegment], index: int, replacement: WaveSegment
) -> list[WaveSegment]:
    """A program with one segment swapped out.

    Editing in place rather than replacing the whole program is what keeps a
    multi-segment daily schedule intact when someone nudges the flow slider.
    """
    updated = list(segments)
    updated[index] = replacement
    return updated


# --- wire encoding -------------------------------------------------------
#
# Everything above works in hex, matching how the vendor app's own DP beans
# represent a raw payload (Hex.toHexString / Hex.decode). That is an
# app-level convention, though, not the wire format: Tuya's SDK re-encodes
# it before the payload ever reaches the socket. tinytuya talks the socket
# directly, and a real pump settles the question -- a hex payload for DP 106
# is silently dropped; the identical bytes sent as base64 get answered.

RAW_DPS: Final = frozenset(
    {DP_CUR_MODE, DP_CUR_POWER, DP_FEED, DP_PREVIEW, DP_WAVE_ACTION, DP_GET_MODE, DP_SYNC_TIME}
)


def to_wire(dp: str, value: Any) -> Any:
    """This module's hex representation of a raw DP, as the wire wants it."""
    if dp in RAW_DPS and isinstance(value, str):
        return base64.b64encode(bytes.fromhex(value)).decode()
    return value


def from_wire(dp: str, value: Any) -> Any:
    """The wire's base64 for a raw DP, as this module's codec expects it.

    A malformed payload is passed through rather than raised here: decode_*
    already rejects bad hex with a clear ZksjProtocolError, and that is a
    better place for a corrupt payload to surface than a silent crash in
    the transport layer.
    """
    if dp in RAW_DPS and isinstance(value, str):
        try:
            return base64.b64decode(value).hex()
        except (ValueError, binascii.Error):
            return value
    return value
