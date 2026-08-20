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

## Re-deriving this

```bash
jadx --show-bad-code -d out ZKSJ_AQUA.apk
ls out/sources/com/zhongkesz/smartaquariumpro/zhongke/smart_wave/beans/dp/
```

Each `Dp*.java` carries its DP number as a `DP` constant and its layout in
`encode()`/`decode()`. `tests/test_protocol.py` pins those layouts; run it
after any change to the codec.
