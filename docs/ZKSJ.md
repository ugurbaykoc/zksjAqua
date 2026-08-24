# ZKSJ AQUA wave pump — Home Assistant setup

Control of a ZKSJ / Zhongke DC wave pump, without the vendor app running and
without the official Tuya integration.

## What this is, and what it is not

The pump is a Tuya device, and Tuya's protocol is the only language it
speaks — that part was never a choice. What *looked* like a choice was
whether to reach it locally or through Tuya's cloud. For this pump it turned
out not to be one either.

**The wave pump does not answer local control at all.** Not with the wrong
key, not on the wrong protocol version — at all. A packet capture of the
vendor app settled it: the app never opens a socket to the pump on port
6668. Every command it sends goes to Tuya's MQTT broker, and every state
change comes back from it. There is no local channel to prefer, so the
integration speaks the same MQTT the app does. See
[the cloud transport](#cloud-mqtt-the-transport-that-works) below.

The local transport is still here, for a pump or firmware that does answer
it, and for monitor-only entries. If you are setting up the wave pump this
document was written for, pick **Cloud (MQTT)**.

Either way, the integration decodes the pump's raw hex data points into real
entities, which is the part generic Tuya integrations skip — and the reason
the pump looks inert under them. See [PROTOCOL.md](PROTOCOL.md).

## What you get

| Entity | What it does |
|---|---|
| `switch` | Pump on/off |
| `select` — Wave mode | Constant, pulse, gyre, nutrient transport, tidal swell, random |
| `number` — Minimum / Maximum flow | Flow range, in percent |
| `number` — Wave frequency | Pulse and gyre only; the slider re-scales per waveform |
| `number` — Duty cycle | Pulse and gyre only |
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
`entry.runtime_data` and the reconfigure flow). `tinytuya` and `paho-mqtt`
are pulled in automatically.

Copy `custom_components/zksj_aqua/` into your Home Assistant `config`
directory and restart, then **Settings → Devices & Services → Add
Integration → ZKSJ AQUA**.

The first thing it asks is which transport to use. For the wave pump this
document was written for, pick **Cloud (MQTT)** — the next section covers
what it needs. **Local** asks instead for the pump's IP address, device id
and local key, and probes the protocol version itself.

## Cloud (MQTT): the transport that works

### How we know

The pump gave every appearance of a credentials problem: connections opened
on port 6668 and then went silent, on every protocol version, with a key
read straight out of the vendor app. Re-pairing produced a new key with the
same result. Tuya's own Cloud API was no better — `getstatus` returned an
empty list for days, `sendcommand` called DP 108 an illegal param, and the
device's function schema came back `2009: not support this device`.

What settled it was watching the app itself.
`tools/sniff_tls_plaintext.js` hooks Conscrypt's TLS streams, so it sees
what the app writes before encryption. Toggling the pump from the app
produced no local traffic at all — no connection to the pump's address, on
any port. What it produced was MQTT, to Tuya's China broker:

```
SUBSCRIBE  smart/mb/in/<device_id>
PUBLISH    smart/mb/out/<device_id>
```

Those topics are named from the app's point of view, so `out` is where
commands go and `in` is where state comes back.

### The envelope

Payloads on both topics are framed like this:

```
"2.2" ‖ crc32(seq ‖ src ‖ cipher) ‖ seq ‖ src ‖ AES-128-ECB(json)
```

Integers are big-endian, the CRC covers everything after itself, and the
cipher key is the device's **`local_key`** — the same credential local
control would have used. So the key still matters here; it just decrypts
payloads instead of opening a socket.

Inside is the data-point dict the rest of this integration already speaks:

```json
app  -> pump   {"data":{"dps":{"108":true}},"protocol":5,"t":1787603668}
pump -> app    {"protocol":4,"t":1787603669,"data":{"dps":{"108":true}}}
```

Note the protocol number differs by direction: the app stamps its writes as
5, the pump answers as 4.

`tests/test_cloud.py` checks the codec against captured frames, including
rebuilding three of the app's own writes byte for byte — CRC included. That
last part is the one worth having: a codec that decoded to plausible JSON
with the fields in the wrong place would pass any round-trip test and still
be refused by the broker.

### What you have to supply, and what expires

Setup asks for the device id and local key (below), plus four values that
come from the same capture:

| Field | Where it comes from |
|---|---|
| MQTT username | the MQTT CONNECT packet |
| MQTT password | the MQTT CONNECT packet |
| MQTT client id | the MQTT CONNECT packet |
| Source id | the `src` field of any frame the app published |

**These four are session-scoped.** They are minted by the app's own login,
which cannot be reproduced outside it (see [Why not just log in the way the
app does?](#why-not-just-log-in-the-way-the-app-does) — the same native
anti-tamper layer blocks both). When the session behind them ends, the pump
goes unavailable and the integration logs that the broker refused it.

The fix is not a repair, it is upkeep: take a fresh capture and paste the
new values into **Reconfigure**. Budget for doing this periodically. Nothing
in the integration can renew them on its own, and this document would rather
say so than imply otherwise.

### Taking the capture

With the emulator and `frida-server` from the section below already running:

```bash
frida -U -f com.zhongkesz.smartaquariumpro -l tools/sniff_tls_plaintext.js
```

Open the app and toggle the pump. In the output, look for:

- the `MQTT` CONNECT frame — it carries the client id, username and password
  in plain text, one after another
- any `smart/mb/out/...` publish — bytes 11-15 of its payload (just after
  `"2.2"`, the CRC and the sequence) are the source id

`tools/mqtt_probe.py` is a standalone client for checking a set of
credentials before putting them into Home Assistant.

## Getting the device id and local key

Both transports need these two. They are minted when the pump is paired.

The app already has both — it needs them for LAN control — so the shortest
route is to read them out of the app, which changes nothing about your
setup. The pump stays paired to your ZKSJ account and the app keeps working.

The pump speaks Tuya protocol 3.4, so there is no keyless shortcut: from
3.2 onward every data point read is encrypted. Monitor-only mode exists
because presence is the one thing left readable without the key.

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

Either way, a configured *local* entry never contacts Tuya again. A cloud
entry contacts it constantly — that is the whole transport.

**In practice, this did not work for the wave pump.** Two full cycles of
removing it from the ZKSJ app, factory-resetting it, and re-pairing it into
Smart Life (once through a generic Tuya IoT Platform cloud project, once
after power-cycling the pump to rule out a stale cloud sync) each produced a
`local_key` that the cloud reported as valid — pump shown online, key
16 bytes, matching what `tinytuya wizard` and the raw `/v1.0/devices/{id}`
endpoint agreed on — but that never worked locally. Every protocol version
(3.1-3.4) and both `dev_type` modes came back with either a decrypt failure
(`904`) or complete silence after the first packet (`914`), and the Cloud
API's own `getstatus()` stayed an empty list even a day later, `sendcommand`
rejected DP 108 as an illegal param, and `/functions` reported the product
outright unsupported (code `2009`). Meanwhile the pump kept working fine
from the ZKSJ app the entire time. Re-pairing it back into ZKSJ and reading
the key with the Frida method above worked on the first try.

The likely explanation: this product id is registered in Tuya's system
under ZKSJ's own OEM/private scope, not the generic Smart Home schema, and
Smart Life pairing does not provision a functional local session for it even
though the pairing itself "succeeds". If you hit the same wall, do not
spend a day on protocol versions and `dev_type` guesses the way this project
did — go straight to the Frida route.

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

**A cloud pump went unavailable, and the log says the broker refused the
session.** The MQTT credentials expired. This is expected upkeep rather than
a fault: take a fresh capture and paste the new values into Reconfigure. See
[what expires](#what-you-have-to-supply-and-what-expires).

**"No reply from the pump on any Tuya protocol version."** On a *local*
entry: the IP, device id or local key is wrong, or something else is holding
the pump's local connection — close the vendor app, and do not point two
integrations at the same pump.

If that is the wave pump and the key is definitely right, this is what its
particular failure looks like, and no amount of re-pairing will fix it: it
does not answer local control. Set it up as **Cloud (MQTT)** instead.

**The wave mode and flow controls are greyed out.** The pump has not reported
its program yet. Press **Sync clock**, or wait a moment; the integration asks
for DP 101 explicitly on connect (the pump does not volunteer it), but a pump
that is powered down cannot answer.

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

Everything above removes Tuya's *app* from your setup, and gives you real
entities instead of an inert device. What it does not remove, for this pump,
is Tuya's **cloud**: commands go to a broker in China and come back, because
that is the only channel the pump's firmware offers. Nor does it remove
Tuya's **protocol**, which is the only language its radio speaks.

That is a real cost, and worth naming plainly: the pump depends on someone
else's server staying up, and on credentials that expire.

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
