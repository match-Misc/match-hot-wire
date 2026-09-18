"""USB UID reader for the D1 mini/MFRC522 JSON-lines firmware.

Uses the handshake, framing and reconnect behavior validated with ndm-nfc.
No dashboard database, tag registration or tag-writing code is needed here.
"""
import json
import re
import threading
import time

import serial

VERIFIED_VERSIONS = {0x82, 0x88, 0x90, 0x91, 0x92}


class ScanReader:
    def __init__(self, port, emit):
        self.port, self.emit = port, emit
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.connected = False
        self.verified = False
        self.version = None
        self.error = 'Verbinde mit dem Reader …'
        self.scan_count = 0
        self.last_scan = None

    def status(self):
        with self.lock:
            return {'connected': self.connected, 'verified': self.verified,
                    'version': self.version, 'error': self.error,
                    'scan_count': self.scan_count, 'last_scan': self.last_scan}

    def handle_message(self, message):
        if not isinstance(message, dict):
            return
        if message.get('type') == 'hello':
            version = message.get('version')
            valid = message.get('reader') == 'MFRC522' and type(version) is int and version in VERIFIED_VERSIONS
            with self.lock:
                self.version, self.verified = version, valid
                self.error = None if valid else 'RC522 nicht erkannt'
            return
        if message.get('type') != 'scan' or not isinstance(message.get('uid'), str):
            return
        uid = message['uid'].strip().replace(':', '').replace('-', '').upper()
        if not re.fullmatch(r'(?:[0-9A-F]{2}){4,10}', uid):
            return
        with self.lock:
            if not self.verified:
                return
            self.scan_count += 1
            self.last_scan = uid
        self.emit('scan', {'uid': uid})

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, daemon=True, name='nfc-reader')
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def run(self):
        while not self.stop_event.is_set():
            try:
                with serial.Serial(self.port, 115200, timeout=.25, exclusive=True) as port:
                    port.reset_input_buffer()
                    port.write(b'{"command":"hello"}\n')
                    next_hello = time.monotonic() + 3
                    with self.lock:
                        self.connected, self.verified = True, False
                        self.error = 'Warte auf Reader-Identität'
                    pending = bytearray()
                    discarding = False
                    while not self.stop_event.is_set():
                        if time.monotonic() >= next_hello:
                            if not self.verified:
                                port.write(b'{"command":"hello"}\n')
                            next_hello = time.monotonic() + 3
                        line = port.readline(512)
                        if not line:
                            continue
                        if discarding:
                            discarding = not line.endswith(b'\n')
                            continue
                        pending.extend(line)
                        if len(pending) > 512:
                            pending.clear()
                            discarding = not line.endswith(b'\n')
                            continue
                        if not pending.endswith(b'\n'):
                            continue
                        complete = bytes(pending)
                        pending.clear()
                        try:
                            self.handle_message(json.loads(complete))
                        except (ValueError, UnicodeError):
                            continue
            except (serial.SerialException, OSError) as exc:
                with self.lock:
                    self.error = str(exc)
            finally:
                with self.lock:
                    self.connected, self.verified, self.version = False, False, None
            self.stop_event.wait(1)
