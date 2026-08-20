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

These live in Tuya's cloud and have to be read out once. They do not change
unless you re-pair the pump — if you do, run the flow's **Reconfigure** step
and paste the new key.

1. Create a free account at [iot.tuya.com](https://iot.tuya.com/) and make a
   Cloud project (data centre: whichever region your pump was paired in).
2. In the project, open **Devices → Link App Account** and link the account
   you use in the ZKSJ AQUA app, by scanning the QR code from the app's
   profile screen.
3. Your pump now appears under **Devices**, with its device id.
4. Read the local key with `tinytuya`:

   ```bash
   pip install tinytuya
   python -m tinytuya wizard
   ```

   The wizard asks for the project's API key and secret and writes
   `devices.json`, which contains each device's `id`, `key` (the local key)
   and `ip`.

Once the integration is set up, nothing contacts Tuya again.

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

## Scope

Written against the wave pump only. The vendor app also covers a smart
socket and a temperature-control socket, which use different data points and
are not implemented here.
