# ZKSJ AQUA wave pump — Home Assistant integration

Control a **ZKSJ / Zhongke DC wave pump** from Home Assistant, without the
vendor's `ZKSJ AQUA` Android app running.

Reverse engineered from that app (`com.zhongkesz.smartaquariumpro` 1.7.0).
The pump turns out to be a **Tuya device**: it pairs over BLE, is handed
Wi-Fi credentials, and from then on speaks Tuya's protocol and nothing else.

## Why the Tuya integration doesn't work on this pump

Nearly all of the pump's behaviour lives in **raw hex data points**
(`101`–`108`). Generic Tuya integrations map booleans, enums and integers and
pass raw DPs through untouched, so the pump shows up as an inert switch — or
not at all. Decoding those blobs is what this component adds.

| Entity | Platform |
|---|---|
| Power | `switch` |
| Wave mode (constant / pulse / gyre / nutrient / tidal / random) | `select` |
| Minimum & maximum flow, frequency, duty cycle | `number` |
| Feed countdown | `sensor` |
| Feed mode, stop feed, sync clock | `button` |
| Whole-program editing | `zksj_aqua.set_program` service |

The wave and flow controls act on the segment running *right now*, so a daily
schedule survives being nudged from a dashboard.

## How it reaches the pump

Two transports, and for the wave pump the choice is already made for you.

**Cloud (MQTT)** — the one that works. The pump does not answer local control
at all: not with the wrong key, not on the wrong protocol version, at all. A
packet capture of the vendor app settled it — the app never opens a socket to
the pump on port 6668. Every command goes to Tuya's MQTT broker and every
state change comes back from it, so this integration speaks the same MQTT.

The catch, stated plainly: the broker credentials are minted by the app's own
login, cannot be renewed outside it, and are session-scoped. When the session
behind them ends, you take a fresh capture and paste the new values into
Reconfigure. [`docs/ZKSJ.md`](docs/ZKSJ.md) covers how, and how the frame
format was recovered.

**Local** — kept for a pump or firmware that does answer on the LAN, and for
monitor-only entries. It talks to the pump directly on your own network, with
no Tuya cloud at runtime.

## Install

Needs Home Assistant **2024.12 or newer**. Copy
`custom_components/zksj_aqua/` into your Home Assistant `config` directory,
restart, then **Settings → Devices & Services → Add Integration → ZKSJ
AQUA**. Installable through [HACS](https://hacs.xyz/) as a custom repository.

Full setup, credential extraction and troubleshooting:
[`docs/ZKSJ.md`](docs/ZKSJ.md).
**Wire format:** [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Tests

```bash
python -m pytest tests/
```

They need neither Home Assistant nor a pump: the protocol tests check the
codec against the layouts in the vendor app, and the cloud tests check the
MQTT framing against real captured frames — including rebuilding three of the
app's own writes byte for byte.

## Scope

Written against the wave pump only. The vendor app also covers a smart socket
and a temperature-control socket, which use different data points and are not
implemented here.
