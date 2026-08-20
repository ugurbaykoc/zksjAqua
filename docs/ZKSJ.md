# ZKSJ AQUA wave pump — Home Assistant setup

Local control of a ZKSJ / Zhongke DC wave pump. No Tuya cloud at runtime, no
Tuya app running, no official Tuya integration involved.

## What this is, and what it is not

The pump is a Tuya device and Tuya's local protocol is the only language it
speaks — that part is not a choice. What *is* a choice is whether you go
through Tuya's cloud to reach it. This integration does not: it opens a
socket to the pump on your own network, on port 6668, and talks to it
directly.

It also decodes the pump's raw hex data points into real entities, which is
the part generic Tuya integrations skip — and the reason the pump looks
inert under them. See [PROTOCOL.md](PROTOCOL.md).

## What you get

| Entity | What it does |
|---|---|
| `switch` | Pump on/off |
| `select` — Wave mode | Constant, pulse, gyre, nutrient transport, tidal swell, random |
| `number` — Minimum / Maximum flow | Flow range, in percent |
| `number` — Wave frequency | Pulse and gyre only; the slider re-scales per waveform |
| `number` — Duty cycle | Pulse and gyre only |
| `sensor` — Current flow | What the pump reports it is doing right now |
| `sensor` — Feed mode remaining | Counts down while feed mode runs |
| `button` — Feed mode / Stop feed mode | |
| `button` — Sync clock | Pushes local time so timed programs run on time |

The wave-mode and flow controls act on the **segment currently running**, not
on the whole program. If you have a daily schedule set up in the vendor app,
nudging the flow slider changes today's live stretch and leaves the rest of
the schedule intact.

For the whole program at once, use the `zksj_aqua.set_program` service.

## Installation

Needs Home Assistant **2024.12 or newer** (the integration uses
`entry.runtime_data` and the reconfigure flow). `tinytuya` is pulled in
automatically.

Copy `custom_components/zksj_aqua/` into your Home Assistant `config`
directory and restart, then **Settings → Devices & Services → Add
Integration → ZKSJ AQUA**.

You will be asked for three things: the pump's IP address, its device id, and
its local key. The protocol version is probed automatically.

## Getting the device id and local key

These two values are what let Home Assistant open a local connection to the
pump. They are minted when the pump is paired and they do not change unless
you pair it again — if you do, use the integration's **Reconfigure** step and
paste the new key.

You do **not** need a Tuya IoT developer account, a cloud project, or an
Access ID and Secret.

### Recommended: QR login, no Tuya account

[`tuya-local-key`](https://github.com/vineetchoudhary/tuya-local-key) logs in
the same way a phone does — you scan a QR code from the app you already have
— and prints every device with its id and local key.

1. In the ZKSJ AQUA (or Smart Life) app: **Me → Settings → Account and
   Security → User Code**. Note the code.
2. Run the tool and give it that user code. It prints a QR code in the
   terminal and writes `tuya-login-qr.png` as a fallback.
3. In the app, tap **+ → Scan**, point it at the QR code, and confirm the
   login.
4. It lists your devices. Copy the pump's `id` and `key`.

The session is cached, so a later re-run does not repeat the scan.

### Fallback: Tuya IoT portal

If the QR login does not work for your region or app build, the older route
still does: make a free Cloud project at [iot.tuya.com](https://iot.tuya.com/)
in the data centre your pump was paired in, use **Devices → Link App Account**
to link the app by QR, then run `python -m tinytuya wizard` with the project's
API key and secret. It writes a `devices.json` holding each device's `id`,
`key` and `ip`.

Either way, once the integration is configured nothing contacts Tuya again.

### Finding the IP address

`python -m tinytuya scan` lists Tuya devices broadcasting on your LAN. Give
the pump a DHCP reservation — the integration finds it by address, and a
lease change otherwise looks like the pump went offline.

## Automation examples

Drop the flow at night:

```yaml
automation:
  - alias: "Reef: quiet flow overnight"
    triggers:
      - trigger: time
        at: "23:00:00"
    actions:
      - action: number.set_value
        target:
          entity_id: number.wave_pump_maximum_flow
        data:
          value: 40
```

Feeding, with the pump running its own countdown:

```yaml
script:
  feed_the_tank:
    sequence:
      - action: zksj_aqua.feed
        target:
          device_id: !input pump
        data:
          duration: 600
          power: 10
```

Replace the whole daily program:

```yaml
action: zksj_aqua.set_program
target:
  device_id: abc123
data:
  segments:
    - type: constant
      start: "00:00:00"
      end: "08:00:00"
      max_power: 40
    - type: gyre
      start: "08:00:00"
      end: "20:00:00"
      min_power: 20
      max_power: 80
      freq: 120
      pwm: 50
    - type: constant
      start: "20:00:00"
      end: "00:00:00"
      max_power: 30
```

Values outside a waveform's accepted range are clamped rather than rejected,
so you can retype a segment without memorising every range. An end time of
midnight means the end of the day.

## Troubleshooting

**"No reply from the pump on any Tuya protocol version."** The IP, device id
or local key is wrong, or something else is holding the pump's local
connection. Tuya devices accept only a small number of concurrent local
connections — close the vendor app, and do not point two integrations at the
same pump.

**The wave mode and flow controls are greyed out.** The pump has not reported
its program yet. Press **Sync clock**, or wait for the next poll; the
integration asks for DP 101 explicitly (the pump does not volunteer it), but
a pump that is powered down cannot answer.

**Timed programs run at the wrong hour.** The pump's clock drifts and resets
on power loss. Press **Sync clock**, or automate it:

```yaml
automation:
  - alias: "Wave pump: keep the clock honest"
    triggers:
      - trigger: time_pattern
        hours: "/6"
    actions:
      - action: button.press
        target:
          entity_id: button.wave_pump_sync_clock
```

## Going further: removing Tuya's firmware

Everything above removes Tuya's *cloud*, its *app*, and its *account* from
your setup. What it cannot remove is Tuya's **protocol** — that is the only
language the pump's radio speaks, and no amount of software on the Home
Assistant side changes it.

Removing it for real means replacing the firmware on the pump's Wi-Fi module,
with [tuya-cloudcutter](https://github.com/tuya-cloudcutter/tuya-cloudcutter)
flashing ESPHome or OpenBeken over the air. Before considering it, three
things are worth knowing:

- **It probably does not buy you what it sounds like.** A pump like this is
  almost certainly a *TuyaMCU* design: the Wi-Fi module is only a radio, and
  the wave logic lives in a separate MCU that the radio talks to over UART —
  using these same data points. Reflashing moves the protocol from Wi-Fi to a
  serial link; it does not retire it. `protocol.py` stays just as necessary.
- **It is one-way.** Cloudcutter replaces the device's keys. Tuya's app and
  servers stop working for that pump, and the project documents no way back.
- **It is narrow.** Only certain BK7231T/N and ESP modules are supported, only
  on firmware versions with a known profile, and the tool needs a real Linux
  machine with its own Wi-Fi adapter — not a VM.

If you want to go this way, the first step is finding out what module is
inside, which means opening the pump's controller. Worth doing only if
cutting the last Tuya-shaped thing out matters more than the risk of ending
up with a pump that neither Home Assistant nor the vendor app can reach.

## Scope

Written against the wave pump only. The vendor app also covers a smart
socket and a temperature-control socket, which use different data points and
are not implemented here.
