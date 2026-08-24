/*
 * Dump raw bytes the ZKSJ AQUA app sends/receives over any TCP socket, so we
 * can see exactly what a real local Tuya control exchange looks like on the
 * wire -- and compare it against what tinytuya sends.
 *
 * No MITM needed: this hooks the app's own socket read/write calls directly.
 *
 *   frida -U -f com.zhongkesz.smartaquariumpro -l tools/sniff_local_socket.js
 *
 * Then use the app to control the wave pump. Every SEND/RECV line that
 * starts with 000055aa is a Tuya protocol frame.
 */

'use strict';

function bytesToHex(bytes) {
    var hex = '';
    for (var i = 0; i < bytes.length; i++) {
        var b = bytes[i] & 0xff;
        hex += (b < 16 ? '0' : '') + b.toString(16);
    }
    return hex;
}

Java.perform(function () {
    console.log('[*] Hooking socket I/O. Control the wave pump from the app now.');

    try {
        var Socket = Java.use('java.net.Socket');
        Socket.connect.overload('java.net.SocketAddress', 'int').implementation = function (addr, timeout) {
            console.log('[CONNECT] ' + addr.toString());
            return this.connect(addr, timeout);
        };
    } catch (err) {
        console.log('[!] Could not hook Socket.connect: ' + err);
    }

    try {
        var SocketOutputStream = Java.use('java.net.SocketOutputStream');
        SocketOutputStream.write.overload('[B', 'int', 'int').implementation = function (buf, off, len) {
            try {
                var bytes = Java.array('byte', buf);
                var slice = [];
                for (var i = 0; i < len; i++) {
                    slice.push(bytes[off + i]);
                }
                var hex = bytesToHex(slice);
                if (hex.indexOf('000055aa') === 0) {
                    console.log('[SEND len=' + len + '] ' + hex);
                }
            } catch (err) {}
            return this.write(buf, off, len);
        };
    } catch (err) {
        console.log('[!] Could not hook SocketOutputStream.write: ' + err);
    }

    try {
        var SocketInputStream = Java.use('java.net.SocketInputStream');
        SocketInputStream.read.overload('[B', 'int', 'int').implementation = function (buf, off, len) {
            var n = this.read(buf, off, len);
            if (n > 0) {
                try {
                    var bytes = Java.array('byte', buf);
                    var slice = [];
                    for (var i = 0; i < n; i++) {
                        slice.push(bytes[off + i]);
                    }
                    var hex = bytesToHex(slice);
                    if (hex.indexOf('000055aa') === 0) {
                        console.log('[RECV len=' + n + '] ' + hex);
                    }
                } catch (err) {}
            }
            return n;
        };
    } catch (err) {
        console.log('[!] Could not hook SocketInputStream.read: ' + err);
    }
});
