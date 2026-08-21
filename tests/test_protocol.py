"""Tests for the ZKSJ wire format.

These check the codec against the layouts in the vendor app
(``com.zhongkesz.smartaquariumpro`` 1.7.0), which is the only specification
that exists.  They import the protocol module directly so the suite runs
without Home Assistant installed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).parent.parent / "custom_components" / "zksj_aqua" / "protocol.py"
_spec = importlib.util.spec_from_file_location("zksj_protocol", _PATH)
protocol = importlib.util.module_from_spec(_spec)
sys.modules["zksj_protocol"] = protocol
_spec.loader.exec_module(protocol)

WaveSegment = protocol.WaveSegment
WaveType = protocol.WaveType
ZksjProtocolError = protocol.ZksjProtocolError


class TestWaveSegment:
    def test_layout_matches_the_app(self):
        segment = WaveSegment(
            type=WaveType.GYRE,
            min_power=20,
            max_power=80,
            freq=120,
            pwm=50,
            start_time=8 * 3600,
            end_time=20 * 3600 + 30 * 60,
        )
        raw = segment.encode()

        assert len(raw) == 12
        assert raw[0] == WaveType.GYRE
        assert raw[1] == 50 + 128  # pwm biased, per WaveOptionBean.encode
        assert raw[2] == 20
        assert raw[3] == 80
        assert int.from_bytes(raw[4:6], "big") == 120
        assert int.from_bytes(raw[6:9], "big") == 8 * 3600
        assert int.from_bytes(raw[9:12], "big") == 20 * 3600 + 30 * 60

    @pytest.mark.parametrize(
        "wave_type", [WaveType.PULSE, WaveType.GYRE], ids=["pulse", "gyre"]
    )
    def test_modulated_types_carry_pwm_in_byte_one(self, wave_type):
        segment = WaveSegment(type=wave_type, pwm=42, identity=9)
        assert segment.encode()[1] == 42 + 128
        assert WaveSegment.decode(segment.encode()).pwm == 42

    @pytest.mark.parametrize(
        "wave_type",
        [WaveType.CONSTANT, WaveType.NUTRIENT_TRANSPORT, WaveType.TIDAL_SWELL],
    )
    def test_other_types_carry_identity_in_byte_one(self, wave_type):
        segment = WaveSegment(type=wave_type, identity=9)
        assert segment.encode()[1] == 9
        decoded = WaveSegment.decode(segment.encode())
        assert decoded.identity == 9
        assert decoded.pwm == 0

    def test_round_trip(self):
        segment = WaveSegment(
            type=WaveType.PULSE, min_power=15, max_power=95, freq=60, pwm=35
        )
        assert WaveSegment.decode(segment.encode()) == segment

    def test_wrong_length_is_rejected(self):
        with pytest.raises(ZksjProtocolError, match="12 bytes"):
            WaveSegment.decode(b"\x00" * 11)

    def test_unknown_wave_type_is_rejected(self):
        with pytest.raises(ZksjProtocolError, match="unknown wave type"):
            WaveSegment.decode(bytes([99]) + b"\x00" * 11)


class TestProgram:
    def test_round_trip_preserves_order(self):
        segments = [
            WaveSegment(type=WaveType.CONSTANT, max_power=40, end_time=8 * 3600),
            WaveSegment(
                type=WaveType.GYRE,
                min_power=20,
                max_power=80,
                freq=120,
                pwm=50,
                start_time=8 * 3600,
                end_time=20 * 3600,
            ),
        ]
        assert protocol.decode_program(protocol.encode_program(segments)) == segments

    def test_empty_program_is_rejected(self):
        with pytest.raises(ZksjProtocolError, match="at least one segment"):
            protocol.encode_program([])

    def test_partial_segment_is_rejected(self):
        with pytest.raises(ZksjProtocolError, match="whole number"):
            protocol.decode_program("0011")

    def test_non_hex_is_rejected(self):
        with pytest.raises(ZksjProtocolError, match="not hex"):
            protocol.decode_program("nothex")


class TestActiveSegment:
    @pytest.fixture
    def day(self):
        return [
            WaveSegment(type=WaveType.CONSTANT, end_time=8 * 3600),
            WaveSegment(
                type=WaveType.GYRE, start_time=8 * 3600, end_time=20 * 3600, pwm=50
            ),
            WaveSegment(type=WaveType.CONSTANT, start_time=20 * 3600, end_time=86400),
        ]

    @pytest.mark.parametrize(
        ("hour", "expected"), [(6, 0), (8, 1), (12, 1), (20, 2), (23, 2)]
    )
    def test_finds_the_running_segment(self, day, hour, expected):
        assert protocol.active_segment_index(day, hour * 3600) == expected

    def test_segment_wrapping_midnight(self):
        night = [WaveSegment(start_time=22 * 3600, end_time=6 * 3600)]
        assert protocol.active_segment_index(night, 23 * 3600) == 0
        assert protocol.active_segment_index(night, 2 * 3600) == 0

    def test_gap_in_the_program_falls_back_rather_than_failing(self):
        sparse = [WaveSegment(start_time=8 * 3600, end_time=9 * 3600)]
        assert protocol.active_segment_index(sparse, 15 * 3600) == 0


class TestRetyping:
    def test_clamps_into_the_new_types_range(self):
        # Constant allows 0 %, nutrient transport does not go below 50 %.
        recast = protocol.retyped(
            WaveSegment(type=WaveType.CONSTANT, min_power=0, max_power=100),
            WaveType.NUTRIENT_TRANSPORT,
        )
        assert recast.min_power == 50
        assert recast.max_power == 100

    def test_drops_fields_the_new_type_has_no_use_for(self):
        recast = protocol.retyped(
            WaveSegment(type=WaveType.GYRE, freq=120, pwm=50), WaveType.CONSTANT
        )
        assert recast.freq == 0
        assert recast.pwm == 0

    def test_keeps_the_segments_window(self):
        segment = WaveSegment(
            type=WaveType.CONSTANT, start_time=3600, end_time=7200
        )
        recast = protocol.retyped(segment, WaveType.PULSE)
        assert (recast.start_time, recast.end_time) == (3600, 7200)


class TestReplaceSegment:
    def test_leaves_the_rest_of_the_program_alone(self):
        program = [
            WaveSegment(type=WaveType.CONSTANT, end_time=8 * 3600),
            WaveSegment(type=WaveType.CONSTANT, start_time=8 * 3600, end_time=86400),
        ]
        edited = protocol.replace_segment(
            program, 1, protocol.retyped(program[1], WaveType.TIDAL_SWELL)
        )
        assert edited[0] == program[0]
        assert edited[1].type is WaveType.TIDAL_SWELL
        assert len(protocol.decode_program(protocol.encode_program(edited))) == 2

    def test_does_not_mutate_the_original(self):
        program = [WaveSegment(type=WaveType.CONSTANT)]
        protocol.replace_segment(program, 0, WaveSegment(type=WaveType.PULSE))
        assert program[0].type is WaveType.CONSTANT


class TestSimpleDataPoints:
    def test_current_power(self):
        assert protocol.decode_power("64") == 100
        assert protocol.decode_power("00") == 0

    def test_current_power_rejects_wrong_length(self):
        with pytest.raises(ZksjProtocolError, match="1 byte"):
            protocol.decode_power("0064")

    def test_feed_write_is_six_bytes(self):
        raw = bytes.fromhex(protocol.encode_feed(start=True, duration=600, power=30))
        assert raw == bytes([1, 0, 0, 2, 88, 30])

    def test_feed_report_carries_the_countdown(self):
        payload = (
            "01"
            + (600).to_bytes(4, "big").hex()
            + (540).to_bytes(4, "big").hex()
            + "1e"
        )
        state = protocol.decode_feed(payload)
        assert state.active is True
        assert state.duration == 600
        assert state.countdown == 540
        assert state.power == 30

    def test_feed_report_rejects_a_short_payload(self):
        with pytest.raises(ZksjProtocolError, match="10 bytes"):
            protocol.decode_feed("0100")

    def test_get_mode_encodes_the_queried_dp_big_endian(self):
        assert protocol.encode_get_mode(101) == "000065"
        assert protocol.encode_get_mode(105, identity=2) == "020069"

    def test_sync_time_is_milliseconds_since_midnight(self):
        assert protocol.encode_sync_time(3600) == (3_600_000).to_bytes(4, "big").hex()

    def test_preview_is_eleven_bytes(self):
        segment = WaveSegment(type=WaveType.GYRE, pwm=50, freq=120)
        raw = bytes.fromhex(protocol.encode_preview(segment, start=True, duration=15))
        assert len(raw) == 11
        assert raw[0] == 1
        assert raw[2] == 50 + 128
        assert int.from_bytes(raw[7:11], "big") == 15


class TestWireEncoding:
    """Confirmed against a real pump: DP 106 sent as hex is silently dropped;
    the identical bytes sent as base64 get answered, and DP 101 comes back
    base64-encoded too. protocol.py's own contract stays hex throughout --
    these two functions are the only place that knows about base64."""

    def test_raw_dp_hex_becomes_base64_for_the_wire(self):
        # The exact query this integration sends for DP 101, and the exact
        # base64 tinytuya must put on the wire for the pump to answer it.
        assert protocol.to_wire(protocol.DP_GET_MODE, "000065") == "AABl"

    def test_non_raw_dp_passes_through_untouched(self):
        assert protocol.to_wire(protocol.DP_SWITCH, True) is True


    def test_malformed_wire_value_passes_through_for_the_decoder_to_reject(self):
        garbage = "not valid base64!!"
        assert protocol.from_wire(protocol.DP_CUR_MODE, garbage) == garbage
        with pytest.raises(ZksjProtocolError):
            protocol.decode_program(protocol.from_wire(protocol.DP_CUR_MODE, garbage))
