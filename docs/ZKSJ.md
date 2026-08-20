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
pump. They are minted when the pump is paired.

The app already has both — it needs them for LAN control — so the shortest
route is to read them out of the app, which changes nothing about your
setup. The pump stays paired to your ZKSJ account and the app keeps working.

Note that you cannot get them by pulling the app's files. `allowBackup` is
on, but the cache is written through `SecurityFile` and `libtuyammkv.so`,
which encrypt it natively; the files come out unreadable. Reading the values
from memory while the app runs avoids the encryption entirely.

### Recommended: read them from the running app

Needs [Frida](https://frida.re/) and either a rooted Android phone or an
emulator running the ZKSJ app. Nothing is written and nothing is re-paired.

1. Install `frida-tools` on your computer and `frida-server` on the phone,
   per Frida's own setup guide.
2. Log into the ZKSJ app as usual, so your pump is on the account.
3. Run:

   ```bash
   frida -U -f com.zhongkesz.smartaquariumpro -l tools/dump_local_keys.js
   ```

4. Open the device list in the app. Each device is printed with its id, local
   key, IP and product id, and the wave pump is flagged as such.

> The APK ships ARM libraries only. A real ARM phone is the easy path; on an
> emulator you need an image with ARM translation (Android 11+ x86\_64
> images have it), or Waydroid on ARM hardware.

### Without rooting: patch the APK instead

If you would rather not root, [objection](https://github.com/sensepost/objection)
can inject Frida's gadget into the APK itself, so the same script runs on an
ordinary phone:

```bash
objection patchapk -s ZKSJ_AQUA.apk
# install the patched APK, open it, log in, then:
frida -U Gadget -l tools/dump_local_keys.js
```

The patched app is signed with your own key rather than the vendor's, and
Tuya's SDK carries a tamper-check path (`ICheckCallback`), so it may refuse
to log in. Worth trying before rooting anything — it costs nothing but a few
minutes, and if the login goes through, the dump works exactly the same.

### Alternative: move the pump to Smart Life

If rooting or an emulator is not worth it, pair the pump into **Smart Life**
instead — it is a stock Tuya Wi-Fi device and pairs normally. Then the usual
extraction tools work, because Smart Life has the account-linking screen that
the ZKSJ app lacks: read the key with
[`tuya-local-key`](https://github.com/vineetchoudhary/tuya-local-key) (Me →
Settings → Account and Security → User Code), or via a Cloud project at
[iot.tuya.com](https://iot.tuya.com/) and `python -m tinytuya wizard`.

The trade-off: a Tuya device belongs to one home at a time, so the pump
leaves the ZKSJ app. Your other ZKSJ devices stay where they are, and Home
Assistant covers everything the app did for the pump — but vendor firmware
updates for it go with it.

Either way, once the integration is configured nothing contacts Tuya again.

### Why not just log in the way the app does?

The app does have its own login, and the chain is fully visible in the
decompiled code: it signs in to ZKSJ's own backend
(`api.szzksj.com/zhongke-aquarium-auth/cgi/authentication/login`, password
MD5-hashed), gets back a Tuya `iot_username` and `iot_password`, and calls
Tuya's `loginWithUid` with the OEM app key from the manifest.

Reproducing the first half is trivial. The second half is not. Tuya's SDK
does not merely sign its API calls — `JNICLibrary` shows request bodies being
**encrypted in native code** (`libjnimain.so`), with keys derived from the
package name, an encrypted asset shipped in the APK, and the app `Context`,
plus a tamper-check callback. Following that path would mean shipping a
reimplementation of Tuya's anti-tamper layer, tied to one app build and
broken by the next SDK update. Reading the key from the app's own memory
gets the same value with none of that.

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
