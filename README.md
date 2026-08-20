# EcoTech Marine Radion XR15w G5 Pro — Home Assistant BLE Integration

Control your **EcoTech Marine Radion XR15w G5 Pro** aquarium light via Bluetooth Low Energy from Home Assistant — with no official API, no cloud, no Mobius app required.

This project was built by **reverse-engineering the undocumented Mobius BLE protocol** (C2 protocol over GATT).

---

## How it works

```
Home Assistant ──REST──► BLE Bridge (Raspberry Pi) ──BLE──► XR15w G5 Pro
  (any machine)            xr15_server.py               Mobius C2 protocol
```

Since HA doesn't have direct BLE access to the light, a small HTTP server runs on a Raspberry Pi near the aquarium. HA calls it via `rest_command`.

**Turn off** = write an all-zero schedule to the device → schedule plays silence  
**Turn on** = restore the original schedule → light resumes normal program

---

## Protocol notes (reverse engineered)

| Field | Value |
|---|---|
| BLE Address | your device's MAC |
| GATT Service prefix | `01ff01XX-ba5e-f4ee-5ca1-eb1e5e4b1ce0` |
| TX characteristic | `01ff0104` (write commands here) |
| RX characteristics | `01ff0101`, `01ff0102` (notify) |
| C2 magic (request) | `0xDE` |
| C2 magic (response) | `0xDF` |
| GET opcode | `0x17` |
| SET opcode | `0x18` (simple) / `0x1C` (sub-attr chunked) |
| Schedule attribute | `500` (Schedule1) |
| Intensity attribute | `511` (Schedule1Intensity) |
| Playback attribute | `510` (SchedulePlayback) |
| Slot count | 25 |
| Bytes per slot | 42 = `time(2B LE) + flags(1B) + 13×[vid(1B)+val(2B LE)]` |
| Channel IDs | `[21,23,18,17,19,20,31,32,22,16,1,101,100]` |
| MTU | 517 (auto-negotiated) |
| Batch size | 8 slots per write (≤ 352 bytes, safe within MTU) |

### Channel mapping (XR15w G5 Pro)

| ID | Channel |
|---|---|
| 21 | UV |
| 23 | Violet |
| 18 | Royal Blue |
| 17 | Blue |
| 19 | Green |
| 20 | Red |
| 31 | Unknown |
| 32 | Moonlight Blue |
| 22 | Warm White |
| 16 | Cool White |
| 1  | Brightness |
| 101 | Unknown |
| 100 | Unknown |

Values are 0–1000 (1000 = 100%).

---

## Requirements

- Raspberry Pi (tested on Pi 5) with Bluetooth
- Python 3.11+
- `bleak` and `aiohttp` libraries
- Home Assistant (any install type)

```bash
pip install bleak aiohttp
```

---

## Setup

### 1. Find your light's BLE address

```bash
sudo bluetoothctl
scan on
# look for "Mobius" or your device name
```

### 2. Decode your original schedule

The light's schedule is hardcoded in `xr15_server.py`. Before you can use **turn on**, you need to capture your device's current schedule and replace the `build_original_schedule()` function.

> **If you have the same schedule as mine**, the hardcoded values will work directly.  
> Otherwise, run a GET (opcode 0x17) against your device and decode the 25×42-byte response.

### 3. Configure the server

Edit `xr15_server.py` and set your device's MAC address:

```python
ADDRESS = "AA:BB:CC:DD:EE:FF"   # ← your XR15w MAC
PORT    = 8765
```

### 4. Run the server on the BLE Pi

```bash
python3 xr15_server.py

# Test it
curl http://localhost:8765/off
curl http://localhost:8765/on
curl http://localhost:8765/status
```

### 5. Install as a systemd service (auto-start)

```bash
sudo cp xr15.service /etc/systemd/system/
sudo systemctl enable xr15
sudo systemctl start xr15
```

Edit `xr15.service` to match your username and path if needed.

### 6. Add to Home Assistant

Add the contents of `ha_config.yaml` to your `/config/configuration.yaml`, replacing the IP with your BLE Pi's IP:

```yaml
rest_command:
  xr15_turn_on:
    url: "http://192.168.x.x:8765/on"
    ...
```

Restart HA, then add a button card:

```yaml
type: button
entity: switch.radion_xr15w
name: Aquarium
icon: mdi:fishbowl
tap_action:
  action: toggle
```

### 7. (Optional) HA Custom Component

For a cleaner integration without `rest_command`, copy `custom_components/mobius_xr15/` to your HA config directory and add via **Settings → Integrations**.

> ⚠️ The custom component requires HA to run on the same machine as the BLE adapter, or on a machine with direct BLE access to the light.

---

## Files

```
.
├── xr15_server.py              # BLE HTTP bridge (main file)
├── xr15.service                # systemd service
├── ha_config.yaml              # HA configuration.yaml snippet
└── custom_components/
    └── mobius_xr15/
        ├── manifest.json
        ├── __init__.py
        ├── config_flow.py
        └── light.py
```

---

## Tested on

- EcoTech Marine Radion XR15w **G5 Pro**
- Firmware: (check your Mobius app)
- Raspberry Pi 5, Ubuntu/Raspberry Pi OS, BlueZ
- Home Assistant 2024.x (Docker)

> May work on other Radion models (XR30, XR15 G4, etc.) — channel count and slot format may differ.

---

## Also in this repo: ZKSJ AQUA wave pump (`custom_components/zksj_aqua`)

A second, unrelated device: the **ZKSJ / Zhongke DC wave pump** (Slim Pro
series), normally driven by the vendor's `ZKSJ AQUA` Android app over BLE.

Unlike the Radion bridge above, this one is a proper Home Assistant custom
component — HA talks to the pump directly, so it needs a Bluetooth adapter
within range of the tank.

| Entity | Platform |
|---|---|
| Power | `switch` |
| Flow level (1–10) | `number` |
| Wave mode | `select` |
| Feed mode | `button` |
| Pump speed / feed countdown | `sensor` |

**Status: the Home Assistant layer is complete; the BLE protocol profile is
not yet extracted.** `protocol.PROFILES` is empty, so adding a pump aborts
with `no_protocol` instead of writing guessed bytes to a pump running a live
tank. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for how a profile is derived
from the vendor APK and what still has to be filled in.

---

## Contributing

If you have a different Radion model and want to add support, open an issue with:
- Your model name
- Number of channels
- Slot byte count (from a GET opcode 0x17 response)

---

## License

MIT

---

## Author

[@ugurbaykoc](https://github.com/ugurbaykoc)
