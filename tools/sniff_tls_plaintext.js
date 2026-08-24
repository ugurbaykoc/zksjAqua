/*
 * Dump plaintext bytes at the TLS boundary: what the app writes to an
 * SSLSocket before Conscrypt encrypts it, and what it reads after Conscrypt
 * decrypts it. This is one layer below sniff_local_socket.js -- that one
 * only sees ciphertext for any HTTPS/MQTTS connection, this one sees what's
 * underneath.
 *
 * Tuya's own SDK may add a second, application-level encryption on top of
 * TLS (see docs/ZKSJ.md's note on libjnimain.so), so this may still show
 * encrypted JSON rather than a readable MQTT payload -- but it narrows down
 * where the opacity actually is.
 *
 *   frida -U -f com.zhongkesz.smartaquariumpro -l tools/sniff_tls_plaintext.js
 *
 * Then control the wave pump from the app.
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

function bytesToAscii(bytes) {
    var out = '';
    for (var i = 0; i < bytes.length; i++) {
        var b = bytes[i] & 0xff;
        out += (b >= 32 && b < 127) ? String.fromCharCode(b) : '.';
    }
    return out;
}

function sliceJavaArray(buf, off, len) {
    var bytes = Java.array('byte', buf);
    var out = [];
    for (var i = 0; i < len; i++) {
        out.push(bytes[off + i]);
    }
    return out;
}

function hookStream(className, methodOwnerLabel) {
    try {
        var Cls = Java.use(className);
        Cls.write.overload('[B', 'int', 'int').implementation = function (buf, off, len) {
            try {
                var slice = sliceJavaArray(buf, off, len);
                console.log('[TLS SEND ' + methodOwnerLabel + ' len=' + len + ']');
                console.log('  hex: ' + bytesToHex(slice));
                console.log('  txt: ' + bytesToAscii(slice));
            } catch (err) {}
            return this.write(buf, off, len);
        };
        console.log('[*] Hooked ' + className + '.write');
    } catch (err) {
        console.log('[!] Could not hook ' + className + '.write: ' + err);
    }
}

function hookInputStream(className, methodOwnerLabel) {
    try {
        var Cls = Java.use(className);
        Cls.read.overload('[B', 'int', 'int').implementation = function (buf, off, len) {
            var n = this.read(buf, off, len);
            if (n > 0) {
                try {
                    var slice = sliceJavaArray(buf, off, n);
                    console.log('[TLS RECV ' + methodOwnerLabel + ' len=' + n + ']');
                    console.log('  hex: ' + bytesToHex(slice));
                    console.log('  txt: ' + bytesToAscii(slice));
                } catch (err) {}
            }
            return n;
        };
        console.log('[*] Hooked ' + className + '.read');
    } catch (err) {
        console.log('[!] Could not hook ' + className + '.read: ' + err);
    }
}

Java.perform(function () {
    console.log('[*] Hooking TLS-layer plaintext. Control the wave pump from the app now.');

    // The concrete classes backing SSLSocket on stock Android (Conscrypt).
    hookStream('com.android.org.conscrypt.ConscryptFileDescriptorSocket$SSLOutputStream', 'Conscrypt');
    hookInputStream('com.android.org.conscrypt.ConscryptFileDescriptorSocket$SSLInputStream', 'Conscrypt');

    // Some OEM builds/older Android versions use the engine-socket variant.
    hookStream('com.android.org.conscrypt.ConscryptEngineSocket$SSLOutputStream', 'ConscryptEngine');
    hookInputStream('com.android.org.conscrypt.ConscryptEngineSocket$SSLInputStream', 'ConscryptEngine');
});
