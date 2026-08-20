/*
 * Read a ZKSJ AQUA pump's device id and local key out of the running app.
 *
 * The app caches both -- it needs them for LAN control -- but writes them
 * through a native encrypted store, so pulling the app's files gets you
 * nothing readable. Reading them from memory while the app is running side-
 * steps the encryption entirely, and touches nothing: the pump stays paired
 * to your ZKSJ account and the app keeps working.
 *
 *   frida -U -f com.zhongkesz.smartaquariumpro -l tools/dump_local_keys.js
 *
 * Then open the device list in the app. Devices are printed as they load.
 *
 * Verified against ZKSJ AQUA 1.7.0 (versionCode 171).
 */

'use strict';

var DEVICE_BEAN = 'com.tuya.smart.sdk.bean.DeviceBean';
var RESCAN_INTERVAL_MS = 3000;

var seen = {};

function text(value) {
    // Frida hands back Java nulls as null; keep the output printable.
    return value === null || value === undefined ? '' : String(value);
}

function report(bean) {
    var devId, localKey;
    try {
        devId = text(bean.getDevId());
        localKey = text(bean.getLocalKey());
    } catch (err) {
        return; // half-constructed bean; it will come round again
    }

    // A device with no key yet is not worth reporting -- it will be filled
    // in once the app finishes loading, and we rescan.
    if (!devId || !localKey) {
        return;
    }
    if (seen[devId] === localKey) {
        return;
    }
    seen[devId] = localKey;

    var name = text(bean.getName());
    var productId = text(bean.getProductId());
    var ip = text(bean.getIp());

    console.log('');
    console.log('  ---------------------------------------------------');
    console.log('  name        : ' + name);
    console.log('  device id   : ' + devId);
    console.log('  local key   : ' + localKey);
    console.log('  ip address  : ' + (ip || '(not reported -- find it with: python -m tinytuya scan)'));
    console.log('  product id  : ' + productId);
    console.log('  firmware    : ' + text(bean.getVerSw()));
    console.log('  online      : ' + text(bean.getIsOnline()));

    // The three product ids the app treats as wave pumps
    // (ProductHelper.SMART_WAVE_PID).
    if (['icgtkgzy9gvaaixh', 'lwlbhsifgw7ec8nk', '2twbidw8gmdxup5c'].indexOf(productId) !== -1) {
        console.log('  >> this is the wave pump; use these values in Home Assistant');
    }
    console.log('  ---------------------------------------------------');
}

function scan() {
    Java.perform(function () {
        try {
            Java.choose(DEVICE_BEAN, {
                onMatch: report,
                onComplete: function () {}
            });
        } catch (err) {
            // The class is not loaded until the SDK initialises; that is
            // normal on the first passes, so stay quiet and try again.
        }
    });
}

Java.perform(function () {
    console.log('[*] Watching for ZKSJ devices. Open the device list in the app.');
    console.log('[*] Press Ctrl+C once your pump has been printed.');

    // Catch keys as the SDK populates them, so a device that loads between
    // scans is not missed.
    try {
        var DeviceBean = Java.use(DEVICE_BEAN);
        DeviceBean.setLocalKey.implementation = function (key) {
            this.setLocalKey(key);
            try {
                report(this);
            } catch (err) {}
        };
    } catch (err) {
        console.log('[!] Could not hook setLocalKey (' + err + '); falling back to scanning only.');
    }

    setInterval(scan, RESCAN_INTERVAL_MS);
    scan();
});
