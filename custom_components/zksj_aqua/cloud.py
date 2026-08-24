"""Cloud transport: the pump over Tuya's own MQTT broker.

The pump does not answer local control at all.  A packet capture of the
vendor app (see ``tools/sniff_tls_plaintext.js``) settled it: the app never
opens a socket to the pump on port 6668, it publishes every command to
Tuya's MQTT broker and receives every state change back from it.  So for
this product, cloud MQTT is not the fallback -- it is the only channel that
carries data points, and this module speaks it.

Wire format, recovered from that capture::

    "2.2" ‖ crc32(seq ‖ src ‖ cipher) ‖ seq ‖ src ‖ AES-128-ECB(json)

All three integers are big-endian, the CRC covers everything after itself,
and the cipher key is the device's own ``local_key`` -- the same 16-byte
credential local control would have used, which is why it is still needed
even though nothing here is local.

Payloads are the data-point dicts ``protocol.py`` already speaks::

    app  -> pump:  {"data": {"dps": {"108": true}}, "protocol": 5, "t": ...}
    pump -> app:   {"protocol": 4, "t": ..., "data": {"dps": {"108": true}}}

Note on credentials: the MQTT username, password and source id are minted by
the app's own login, which cannot be reproduced outside it (docs/ZKSJ.md
covers why).  They are session-scoped, so when they expire they have to be
re-read with the sniff script and updated via the integration's Reconfigure
step.  Nothing here can renew them on its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
import zlib
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CLOUD_PROTOCOL_PUBLISH,
    CLOUD_TOPIC_IN,
    CLOUD_TOPIC_OUT,
    CONNECTION_TIMEOUT,
    MQTT_FRAME_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

_AES_BLOCK = 16


class ZksjCloudError(Exception):
    """The broker refused us, or a frame did not decode."""


def _pad(data: bytes) -> bytes:
    """PKCS#7, as the app's frames are padded."""
    fill = _AES_BLOCK - (len(data) % _AES_BLOCK)
    return data + bytes([fill]) * fill


def _unpad(data: bytes) -> bytes:
    if not data:
        return data
    fill = data[-1]
    if 1 <= fill <= _AES_BLOCK and data[-fill:] == bytes([fill]) * fill:
        return data[:-fill]
    # Not padded the way we expect; hand back what we have rather than
    # discarding a frame that may still parse.
    return data


def _aes(key: bytes):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    return Cipher(algorithms.AES(key), modes.ECB())


def encode_frame(
    key: bytes, *, sequence: int, source_id: int, payload: dict[str, Any]
) -> bytes:
    """Wrap a payload in the envelope the broker and pump expect.

    Kept a plain function so the framing can be checked against captured
    frames without a Home Assistant instance behind it.
    """
    plaintext = json.dumps(payload, separators=(",", ":")).encode()
    encryptor = _aes(key).encryptor()
    cipher = encryptor.update(_pad(plaintext)) + encryptor.finalize()

    body = sequence.to_bytes(4, "big") + source_id.to_bytes(4, "big") + cipher
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return MQTT_FRAME_PREFIX + crc.to_bytes(4, "big") + body


def decode_frame(key: bytes, raw: bytes) -> tuple[dict[str, Any], bool]:
    """Unwrap a frame, returning its payload and whether the CRC matched.

    A bad checksum is reported rather than raised on: the payload almost
    always still decrypts, and dropping a state report over it would leave
    entities stale for no gain.
    """
    if not raw.startswith(MQTT_FRAME_PREFIX):
        raise ZksjCloudError(f"unexpected frame prefix: {raw[:4]!r}")

    claimed = int.from_bytes(raw[3:7], "big")
    body = raw[7:]
    crc_ok = (zlib.crc32(body) & 0xFFFFFFFF) == claimed

    cipher = body[8:]  # body is seq(4) + src(4) + ciphertext
    if not cipher or len(cipher) % _AES_BLOCK:
        raise ZksjCloudError(f"ciphertext not block aligned: {len(cipher)}")

    decryptor = _aes(key).decryptor()
    plaintext = _unpad(decryptor.update(cipher) + decryptor.finalize())
    try:
        return json.loads(plaintext), crc_ok
    except ValueError as err:
        raise ZksjCloudError(f"frame did not contain JSON: {err}") from err


class ZksjCloudTransport:
    """One MQTT session, and the pump's two topics on it.

    Commands go out on ``smart/mb/out/<device_id>`` and state arrives on
    ``smart/mb/in/<device_id>`` -- named from the app's point of view, which
    is why "out" is the one we publish to.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        device_id: str,
        local_key: str,
        broker: str,
        port: int,
        client_id: str,
        username: str,
        password: str,
        source_id: int,
    ) -> None:
        self._hass = hass
        self._device_id = device_id
        self._key = local_key.encode()
        self._broker = broker
        self._port = port
        self._client_id = client_id
        self._username = username
        self._password = password
        self._source_id = source_id

        self._client: Any = None
        self._sequence = 0
        self._connected = asyncio.Event()
        self._listener: Callable[[dict[str, Any]], None] | None = None

    # -- framing ----------------------------------------------------------

    def encode(self, dps: dict[str, Any]) -> bytes:
        """Wrap a data-point mapping into a frame the pump will accept."""
        self._sequence += 1
        return encode_frame(
            self._key,
            sequence=self._sequence,
            source_id=self._source_id,
            payload={
                "data": {"dps": dps},
                "protocol": CLOUD_PROTOCOL_PUBLISH,
                "t": int(time.time()),
            },
        )

    def decode(self, raw: bytes) -> dict[str, Any]:
        """Unwrap a frame from the pump. Raises on anything unreadable."""
        payload, crc_ok = decode_frame(self._key, raw)
        if not crc_ok:
            _LOGGER.debug("%s: frame checksum did not match", self._device_id)
        return payload

    # -- connection -------------------------------------------------------

    @property
    def topic_publish(self) -> str:
        return CLOUD_TOPIC_OUT.format(device_id=self._device_id)

    @property
    def topic_subscribe(self) -> str:
        return CLOUD_TOPIC_IN.format(device_id=self._device_id)

    def set_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        """Register what to call with each data-point mapping that arrives."""
        self._listener = listener

    def _build_client(self):
        import paho.mqtt.client as mqtt

        client = mqtt.Client(client_id=self._client_id, protocol=mqtt.MQTTv311)
        client.username_pw_set(self._username, self._password)
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        return client

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc != 0:
            _LOGGER.error(
                "%s: broker refused the session (rc=%s) -- the captured "
                "credentials have most likely expired",
                self._device_id,
                rc,
            )
            return
        client.subscribe(self.topic_subscribe, qos=1)
        _LOGGER.debug("%s: subscribed to %s", self._device_id, self.topic_subscribe)
        self._hass.loop.call_soon_threadsafe(self._connected.set)

    def _on_disconnect(self, client, userdata, rc, properties=None) -> None:
        _LOGGER.debug("%s: broker disconnected (rc=%s)", self._device_id, rc)
        self._hass.loop.call_soon_threadsafe(self._connected.clear)

    def _on_message(self, client, userdata, message) -> None:
        try:
            payload = self.decode(message.payload)
        except ZksjCloudError as err:
            _LOGGER.debug("%s: %s", self._device_id, err)
            return

        dps = payload.get("data", {}).get("dps")
        if not isinstance(dps, dict) or self._listener is None:
            return
        # paho calls us on its own thread; hop back to the event loop.
        self._hass.loop.call_soon_threadsafe(self._listener, dps)

    async def async_connect(self) -> None:
        """Open the session, and wait until the topic is subscribed."""
        if self._client is not None:
            return

        client = await self._hass.async_add_executor_job(self._build_client)
        self._client = client

        def _connect() -> None:
            client.connect(self._broker, self._port, keepalive=60)
            client.loop_start()

        try:
            await self._hass.async_add_executor_job(_connect)
        except OSError as err:
            self._client = None
            raise ZksjCloudError(f"could not reach {self._broker}: {err}") from err

        try:
            async with asyncio.timeout(CONNECTION_TIMEOUT):
                await self._connected.wait()
        except TimeoutError as err:
            await self.async_disconnect()
            raise ZksjCloudError(
                f"{self._broker} accepted no session within "
                f"{CONNECTION_TIMEOUT:g}s -- check the MQTT credentials"
            ) from err

    async def async_disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return

        def _stop() -> None:
            client.loop_stop()
            client.disconnect()

        await self._hass.async_add_executor_job(_stop)
        self._connected.clear()

    async def async_publish(self, dps: dict[str, Any]) -> None:
        """Send one data-point write."""
        if self._client is None:
            raise ZksjCloudError("not connected")
        frame = self.encode(dps)
        _LOGGER.debug("%s: publishing %s", self._device_id, dps)

        def _publish() -> None:
            info = self._client.publish(self.topic_publish, frame, qos=1)
            info.wait_for_publish(timeout=CONNECTION_TIMEOUT)

        await self._hass.async_add_executor_job(_publish)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()
