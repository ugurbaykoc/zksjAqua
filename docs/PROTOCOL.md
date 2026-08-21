# ZKSJ AQUA wave pump protocol

Reverse engineered from the vendor Android app,
`com.zhongkesz.smartaquariumpro` ("ZKSJ AQUA") version 1.7.0 (versionCode 171),
package path `com.zhongkesz.smartaquariumpro.zhongke.smart_wave`.

## What the pump actually is

The pump is a **Tuya device**. It is paired over BLE and handed Wi-Fi
credentials (`MultiModeActivatorBean` with `ssid`/`pwd`), after which it runs
on Wi-Fi and speaks Tuya's protocol. Every one of the app's 73 control call
sites goes through `ITuyaDevice.publishDps()`; there is no direct socket, no
local HTTP API, and no direct GATT write anywhere in the vendor code. The
pump has no second language.

Tuya product ids the app recognises as wave pumps
(`ProductHelper.SMART_WAVE_PID`):

```
icgtkgzy9gvaaixh
lwlbhsifgw7ec8nk
2twbidw8gmdxup5c
```

### Why generic Tuya integrations show nothing useful

Almost all of this pump's behaviour lives in **raw hex data points**. Generic
Tuya integrations map booleans, enums and integers; they hand a raw DP
through as an opaque string. So the pump appears as a lone on/off switch, or
as nothing at all, and none of the wave control is reachable. Decoding those
blobs is the entire job of `custom_components/zksj_aqua/protocol.py`.

## Data points

All multi-byte fields are **big-endian**. Raw DPs are carried as lowercase
hex strings.

| DP | Name | R/W | Payload |
|---|---|---|---|
| `101` | `cur_mode` | R/W | N × 12-byte wave segment — the active program |
| `102` | `cur_power` | R | 1 byte, current output 0–100 % |
| `103` | `feed` | R/W | write 6 bytes; the pump reports 10 |
| `104` | `preview` | W | 11 bytes — run one waveform briefly without saving it |
| `105` | `wave_action` | R/W | `action(1) + identity(1)` + N × 12-byte segments |
| `106` | `get_mode` | W | `identity(1) + query_dp(2)` — asks the pump to report a DP |
| `107` | `sync_time` | W | 4 bytes, milliseconds since local midnight |
| `108` | `switch` | R/W | bool — pump on/off |

DP `111` exists but the app strips it for every product outside a hardcoded
allow-list (`CommandManager.FILTER_PRODUCT`), none of which are wave pumps.

### Wave segment (12 bytes)

A program is a list of these. Times are seconds since local midnight, so a
segment spanning `0 … 86400` is how the app says "just run this pattern".

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | wave type |
| 1 | 1 | **identity**, or **pwm + 128** for pulse and gyre |
| 2 | 1 | min power % |
| 3 | 1 | max power % |
| 4 | 2 | frequency |
| 6 | 3 | start time (seconds of day) |
| 9 | 3 | end time (seconds of day) |

Byte 1 is overloaded. `WaveOptionBean.encode()` writes `pwm + 128` there for
the two waveforms with a duty cycle and the segment identity for every other
type — so a decoder that always reads it as identity gets nonsense for pulse
and gyre, and vice versa.

### Wave types and their accepted ranges

From `WaveOptionType`. The app enforces these in its own UI; the pump is not
known to range-check them.

| Id | Type | Power % | Frequency | Duty cycle % |
|---|---|---|---|---|
| 0 | Constant | 0–100 | — | — |
| 1 | Pulse | 10–100 | 3–100 | 30–70 |
| 2 | Gyre | 10–100 | 20–300 | 30–70 |
| 3 | Nutrient transport | 50–100 | — | — |
| 4 | Tidal swell | 50–100 | — | — |
| 5 | Random | — | — | — |

### Feed (DP 103)

Write `state(1) + duration(4)` — and the app allocates a sixth byte for
power. The pump's report is longer:

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | state: 1 running, 0 stopped |
| 1 | 4 | duration (s) |
| 5 | 4 | countdown remaining (s) |
| 9 | 1 | power % |

The countdown runs on the pump; it returns to its program by itself.

### Preview (DP 104, 11 bytes)

`state(1) | type(1) | identity-or-pwm+128(1) | min_power(1) | max_power(1) |
freq(2) | duration(4)`. Same byte-1 overloading as a segment.

## Notes and gotchas

- **The pump does not volunteer its program.** A plain status read often
  comes back without DP 101. DP 106 is the vendor's own way of asking for a
  specific DP, and is what the app sends on opening a pump's screen.
- **The pump keeps its own clock**, and nothing restores it across a power
  cut, so a time-based program plays at the wrong hour until something writes
  DP 107.
- **Decompiler artifact:** `WaveOptionBean.decode()` and `DpSyncTime.decode()`
  mask bytes with `& 65535` and `& 16777215` where `& 255` is meant. It is
  harmless for the value ranges involved (a start time never exceeds 86400,
  whose high byte is at most `0x01`), and the correct masking is used here.

## Why the local key is unavoidable

Confirmed against a real pump (product id `icgtkgzy9gvaaixh`, firmware 1.0.6):
it speaks **Tuya protocol 3.4**.

That closes the one loophole worth checking. Tuya 3.1 answers a `DP_QUERY`
without credentials — tinytuya's own scanner notes it: *"v3.1 does not
require a key for polling, but v3.2+ do"*. From 3.2 onward every data point
read is AES-encrypted with the device's local key, so on a 3.4 pump there is
no unencrypted status channel at all.

What that leaves reachable without a key is the device's presence: it
broadcasts its id, address and protocol version over UDP, and it accepts a
TCP connection on port 6668. That is enough to tell whether the pump has
power and is on the network, and it is the whole of the monitor-only mode.
Run state, wave mode and flow all live behind the key.

## DP 101 takes effect immediately

Confirmed live: writing DP 101 (`cur_mode`) is not a "save for later"
schedule edit -- the pump acted on it right away. DP 105 (`wave_action`),
which the app also writes from its live-control screens, was never needed.

## Hex versus base64: what's on the wire

`protocol.py` works in hex throughout, matching the vendor app's own DP
beans (`Hex.toHexString` / `Hex.decode`). That is the app-level convention,
not the wire format -- confirmed against a real pump.

A DP 106 query sent with a hex payload is silently dropped; the identical
bytes sent as base64 get answered immediately, and the pump's own DP 101
push comes back base64-encoded too. Tuya's SDK re-encodes hex to base64
before anything reaches the socket; tinytuya talks the socket directly, so
that translation has to happen on our side. `to_wire()` / `from_wire()` in
`protocol.py` are the only two functions that know this -- everything else,
including all the encode/decode functions above and their tests, stays in
hex. `device.py` calls them at the tinytuya boundary and nowhere else.

Also confirmed live: a freshly re-paired pump reports DP 101 as 120 bytes
(10 segments) of all zero. Re-pairing clears the program; a segment count
or a duty cycle is not owed to us by the pump until something writes one.

DP 102 (`cur_power`) has not answered a DP 106 request in testing, hex or
base64, while the identical mechanism works for DP 101. Whether it simply
is not queryable on demand -- only pushed when the pump's output actually
changes -- is not yet confirmed either way.

## The app's own login chain

Worth recording, since it looks like a shortcut and is not one.

`LoginP.java` signs in to ZKSJ's backend, not to Tuya:

```
POST https://api.szzksj.com/zhongke-aquarium-auth/cgi/authentication/login
     <user>=..., psw=md5(password)
  -> data.user.countryCode, data.iotJson.iot_username, data.iotJson.iot_password

TuyaHomeSdk.init(ctx, appKey, appSecret)      # from AndroidManifest meta-data
TuyaHomeSdk.getUserInstance()
           .loginWithUid(countryCode, iot_username, iot_password)
```

So ZKSJ is the identity provider and Tuya is federated behind it. The first
request is plain form-encoded HTTP with an MD5 password and reproduces
easily.

The Tuya half does not, and it is worth being precise about why, because
`EncryptApiParams` looks at first like an opt-in subclass used by a handful
of sensitive endpoints.

It is not the only path. `TuyaApiParams.getEncryptPostDataString()` -- on the
*base* class, so on every request that carries a body -- AES-encrypts the
post data with a key from
`TuyaNetworkSecurity.getEncryptoKey(requestId, ecode)`, which lands in
`JNICLibrary` and `libjnimain.so`. That native call needs the app `Context`;
`TuyaNetworkSecurity.getContext()` goes as far as reflecting into
`ActivityThread.currentActivityThread()` to find the Application when one was
not handed to it. An `ICheckCallback` reports tamper status alongside.

So the key material is derived inside native code from the running app's own
identity. Holding the app key and secret is not sufficient: the request
encryption is bound to the process, not to the credentials.

One escape hatch exists in the code -- `TuyaSmartNetWork.mPacketCaptureEnabled`
makes `getEncryptPostDataString()` return plaintext -- but flipping it means
instrumenting the running app, at which point reading `DeviceBean.getLocalKey()`
directly is simpler. See `tools/dump_local_keys.js`.

The device id and local key are therefore obtained through Tuya's
third-party-facing interfaces instead; see [ZKSJ.md](ZKSJ.md).

## Re-deriving this

```bash
jadx --show-bad-code -d out ZKSJ_AQUA.apk
ls out/sources/com/zhongkesz/smartaquariumpro/zhongke/smart_wave/beans/dp/
```

Each `Dp*.java` carries its DP number as a `DP` constant and its layout in
`encode()`/`decode()`. `tests/test_protocol.py` pins those layouts; run it
after any change to the codec.
