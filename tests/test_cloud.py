"""Tests for the cloud (MQTT) frame codec.

Every frame here is a real one, lifted verbatim from a capture of the vendor
app's own MQTT session (``tools/sniff_tls_plaintext.js``).  That capture is
the only specification for this envelope, so checking against the exact
bytes is the only way to know the codec is right: a frame that decodes to
plausible-looking JSON but with the fields in the wrong place would pass any
round-trip test and still be rejected by the broker.

The module is loaded directly, with Home Assistant stubbed, so the suite
keeps running without HA installed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent / "custom_components" / "zksj_aqua"


def _load_cloud():
    """Import cloud.py with its Home Assistant and package imports stubbed."""
    # homeassistant.core.HomeAssistant is only used as a type here.
    ha = types.ModuleType("homeassistant")
    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = object
    ha.core = ha_core
    sys.modules.setdefault("homeassistant", ha)
    sys.modules.setdefault("homeassistant.core", ha_core)

    # Stand in for the package so `from .const import ...` resolves.
    pkg = types.ModuleType("zksj_pkg")
    pkg.__path__ = [str(_ROOT)]
    sys.modules["zksj_pkg"] = pkg

    const_spec = importlib.util.spec_from_file_location(
        "zksj_pkg.const", _ROOT / "const.py"
    )
    const = importlib.util.module_from_spec(const_spec)
    sys.modules["zksj_pkg.const"] = const
    const_spec.loader.exec_module(const)

    spec = importlib.util.spec_from_file_location("zksj_pkg.cloud", _ROOT / "cloud.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["zksj_pkg.cloud"] = module
    spec.loader.exec_module(module)
    return module, const


cloud, const = _load_cloud()

# The pump this capture came from.
KEY = b"ROx@v=OE[b_YNcst"
SOURCE_ID = 0x0004E492

# Frames the pump sent (topic smart/mb/in/<device_id>), with the data points
# each one carries. These cover a raw hex DP, a bool, and both switch states.
DEVICE_FRAMES = [
    (
        "322e32b4db43c40000e8c600000001"
        "2354978520dd94859c29a0e8f1a30c2fc048971a3d7debba4f6673c294a91e80"
        "218b38e4e42bf9699112eee9973ae7477754158bfe244c4fce0df6ade7d1855f"
        "a179a9d6349340f728d0aa3278bfd727",
        {"101": "AAABAQAAAAAAAVGA"},
    ),
    (
        "322e327778981a0000e8c800000001"
        "2354978520dd94859c29a0e8f1a30c2fc195673291a074c73b7b235089ae151a"
        "2091cd41eedd13da018cfdcdb0aab6181bb5184590d4ee63f932ba4ee5ba0314"
        "682ba826da328c50ce539b7cfad6257b",
        {"104": "AAAAHh4AAAABSjM="},
    ),
    (
        "322e327d3f87130000e8c900000001"
        "2354978520dd94859c29a0e8f1a30c2fc195673291a074c73b7b235089ae151a"
        "44cc4da02b9b3fab8d92d31367fa2f96fd2f0e20c1cb4ae26eebb84978e33344"
        "dac53e4112b9582ae339eb026f84adeb",
        {"105": "AAAAAAEBAAAAAAABUYA="},
    ),
    (
        "322e3280f23ca20000e8ca00000001"
        "2354978520dd94859c29a0e8f1a30c2fc195673291a074c73b7b235089ae151a"
        "70ae95d87c7bba1ff6b43ee24cd5eaa64c8ed5c4db62595fb79fc5e7cfe6b7bf",
        {"107": "AAFLfw=="},
    ),
    (
        "322e3200ba91b00000e8cb00000001"
        "2354978520dd94859c29a0e8f1a30c2fe2acf3516a3504c7cf536e3b26ce9efd"
        "557e0990d4fa824032f5dfb146d4bb467ddc631170c4a68b83ffb2a3e40e31e7",
        {"108": False},
    ),
    (
        "322e320ff0484f0000e8cc00000001"
        "2354978520dd94859c29a0e8f1a30c2f593733d3269edbb1eb66e16e96e3000a"
        "557e0990d4fa824032f5dfb146d4bb466193866ba799c774ef1732ecc3abf25f",
        {"108": True},
    ),
]

# Frames the app sent (topic smart/mb/out/<device_id>): sequence, data points.
APP_FRAMES = [
    (
        "322e3255fcb31e000000040004e492"
        "d130601966c7a426c110a3befe9462b2c0b07b4581c877c9fc77c0f1a02497bd"
        "1607c6eeb8e8d7769c02880dec86309b44f6f8b9b3176a1d15746c02216a8059",
        4,
        {"106": "AAAA"},
        1787603663,
    ),
    (
        "322e32a80db247000000060004e492"
        "d130601966c7a426c110a3befe9462b2ea0ab2aa2fe8e156cd0d6b333b88531a"
        "03ca4369c31e5d3ff80d6f2d2612eecc7a2656f4c7e41b5d11b0bfea4bab2b7e",
        6,
        {"108": False},
        1787603667,
    ),
    (
        "322e3244f0f5c3000000070004e492"
        "d130601966c7a426c110a3befe9462b25ba0798d3c462ac3aecacddfcf95b272"
        "9852c91b2d95a61180f7c3d9f724f67c70798feffe1330413c0ab0b649d147d1",
        7,
        {"108": True},
        1787603668,
    ),
]


class TestDecode:
    @pytest.mark.parametrize(("frame", "dps"), DEVICE_FRAMES)
    def test_reads_the_pumps_own_frames(self, frame, dps):
        payload, crc_ok = cloud.decode_frame(KEY, bytes.fromhex(frame))
        assert crc_ok
        assert payload["data"]["dps"] == dps

    def test_pump_frames_are_stamped_protocol_4(self):
        # The pump answers as protocol 4 while the app writes as 5; getting
        # this backwards would look fine locally and be rejected upstream.
        for frame, _ in DEVICE_FRAMES:
            payload, _ = cloud.decode_frame(KEY, bytes.fromhex(frame))
            assert payload["protocol"] == 4

    @pytest.mark.parametrize(("frame", "_seq", "dps", "_t"), APP_FRAMES)
    def test_reads_the_apps_own_frames(self, frame, _seq, dps, _t):
        payload, crc_ok = cloud.decode_frame(KEY, bytes.fromhex(frame))
        assert crc_ok
        assert payload["data"]["dps"] == dps

    def test_rejects_a_foreign_prefix(self):
        with pytest.raises(cloud.ZksjCloudError, match="prefix"):
            cloud.decode_frame(KEY, b"3.3" + bytes(24))

    def test_rejects_a_truncated_ciphertext(self):
        frame = bytes.fromhex(DEVICE_FRAMES[0][0])
        with pytest.raises(cloud.ZksjCloudError, match="block aligned"):
            cloud.decode_frame(KEY, frame[:-3])

    def test_reports_a_bad_checksum_without_dropping_the_frame(self):
        frame = bytearray(bytes.fromhex(DEVICE_FRAMES[0][0]))
        frame[3] ^= 0xFF  # corrupt the stored CRC
        payload, crc_ok = cloud.decode_frame(KEY, bytes(frame))
        assert not crc_ok
        assert payload["data"]["dps"] == DEVICE_FRAMES[0][1]

    def test_a_wrong_key_does_not_yield_a_payload(self):
        with pytest.raises(cloud.ZksjCloudError):
            cloud.decode_frame(b"0" * 16, bytes.fromhex(DEVICE_FRAMES[0][0]))


class TestEncode:
    @pytest.mark.parametrize(("frame", "sequence", "dps", "timestamp"), APP_FRAMES)
    def test_rebuilds_the_apps_frames_byte_for_byte(
        self, frame, sequence, dps, timestamp
    ):
        # The strongest check available: given the same inputs, produce the
        # same bytes the app put on the wire, CRC and all.
        built = cloud.encode_frame(
            KEY,
            sequence=sequence,
            source_id=SOURCE_ID,
            payload={
                "data": {"dps": dps},
                "protocol": const.CLOUD_PROTOCOL_PUBLISH,
                "t": timestamp,
            },
        )
        assert built.hex() == frame

    def test_round_trips(self):
        payload = {"data": {"dps": {"108": True}}, "protocol": 5, "t": 1}
        frame = cloud.encode_frame(
            KEY, sequence=9, source_id=SOURCE_ID, payload=payload
        )
        decoded, crc_ok = cloud.decode_frame(KEY, frame)
        assert crc_ok
        assert decoded == payload

    def test_sequence_and_source_land_where_the_pump_looks_for_them(self):
        frame = cloud.encode_frame(
            KEY, sequence=0x1234, source_id=SOURCE_ID, payload={"a": 1}
        )
        assert frame[:3] == b"2.2"
        assert int.from_bytes(frame[7:11], "big") == 0x1234
        assert int.from_bytes(frame[11:15], "big") == SOURCE_ID


class TestTopics:
    def test_named_from_the_apps_point_of_view(self):
        # "out" is what we publish to and "in" is what we listen on -- the
        # reverse of what the names suggest from our side, and an easy thing
        # to invert by accident.
        device_id = "6cb3a81db75bdb02d54lh9"
        assert const.CLOUD_TOPIC_OUT.format(device_id=device_id) == (
            f"smart/mb/out/{device_id}"
        )
        assert const.CLOUD_TOPIC_IN.format(device_id=device_id) == (
            f"smart/mb/in/{device_id}"
        )
