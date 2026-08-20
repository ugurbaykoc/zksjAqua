# ZKSJ AQUA BLE protocol

The pumps have no published API. Everything the `zksj_aqua` integration knows
comes from the vendor Android app, `com.zhongkesz.smartaquariumpro` ("ZKSJ
AQUA"). This file records how that knowledge is extracted and where it lands
in the code, so the next person — or the next pump generation — does not have
to start over.

## Status

| Piece | State |
|---|---|
| Home Assistant layer (config flow, coordinator, entities) | done |
| BLE transport (`device.py`) | done |
| Protocol profile (UUIDs, framing, mode codes) | **not yet extracted** |

`protocol.PROFILES` is empty. Until it is filled, adding a pump aborts with
`no_protocol` rather than guessing. Guessing is not an option here: a wrong
checksum or mode byte gets written to a pump that is circulating someone's
reef tank.

## Extracting a profile

### 1. Decompile

```bash
jadx --show-bad-code -d out ZKSJ_AQUA.apk
```

### 2. Find the GATT plumbing

The UUIDs are usually string constants near the BLE manager class:

```bash
grep -rEn '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}' out/sources | sort -u
grep -rln 'BluetoothGattCharacteristic\|writeCharacteristic\|setCharacteristicNotification' out/sources
```

Three of them matter: the service, the characteristic the app writes commands
to, and the one it subscribes to for state. Anything vendor-specific (`fff0`,
`ffe0`, `0000ff…`) is a better candidate than a standard SIG UUID.

### 3. Find the framing

Follow whatever the write path calls to build its byte array. Look for a
fixed header byte, a length field, a command id, and a trailing checksum
(these pumps typically use a sum-of-bytes or XOR over everything after the
header). Record:

- header/magic bytes and byte order
- where the command id sits
- how the checksum is computed and over which span
- whether the app waits for an echo/ack before sending the next frame

### 4. Find the command ids and enums

The wave modes shown in the UI map to integers somewhere — often an enum, a
`switch`, or a resource array in `res/values/arrays.xml`. Map each one to the
stable key used in `protocol.py`, keeping the key even if the vendor's label
changes:

`constant`, `pulse`, `gyre`, `nutrient`, `tidal`, `random`

Also note the flow-level range (the app exposes 1–10) and whether feed mode is
a toggle or a one-shot with a pump-side timer.

### 5. Fill in the profile

Add a `Codec` subclass and a `DeviceProfile` to `protocol.py`, and register it
in `PROFILES`. Put the advertised BLE name pattern in `local_names` and mirror
it into the `bluetooth` matchers in `manifest.json` so Home Assistant offers
the pump on its own.

### 6. Confirm against the real pump

Decompiled code shows intent, not behaviour. Before trusting a profile:

```bash
# Watch what the app actually sends, with the phone's HCI snoop log enabled,
# or sniff from the Home Assistant host:
sudo btmon -w zksj.snoop
```

Compare the captured writes with what `Codec.encode_*` produces for the same
UI action. They should be byte-identical.

## Notes on the hardware

- The pump only accepts BLE connections in app mode. Long-press the mode
  button until the Bluetooth LED flashes.
- It serves one central at a time: while Home Assistant holds the link, the
  phone app cannot connect. `device.py` releases the link after
  `DISCONNECT_DELAY` seconds of idleness for exactly this reason.
