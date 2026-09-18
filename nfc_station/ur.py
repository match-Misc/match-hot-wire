"""UR Primary read-only protocol (PolyScope 5): never sends robot commands.

Reference: https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/primary-secondary-guide.html
Names are deliberately not reused across connections or program starts.
"""
import socket
import struct
import threading
import time


class ProtocolError(ValueError):
    pass


def decode_value(data, offset=0, depth=0):
    if depth > 8:
        raise ProtocolError("Verschachtelung zu tief")
    kind = data[offset]
    offset += 1
    if kind == 0:
        return None, offset
    if kind in (3, 4):
        length = struct.unpack_from("!H", data, offset)[0]
        offset += 2
        if offset + length > len(data):
            raise ProtocolError("Unvollständiger String")
        return data[offset:offset + length].decode("utf-8"), offset + length
    if kind == 12:
        return list(struct.unpack_from("!6f", data, offset)), offset + 24
    if kind == 13:
        if data[offset] not in (0, 1):
            raise ProtocolError("Ungültiger boolescher Wert")
        return bool(data[offset]), offset + 1
    if kind in (14, 16):
        return struct.unpack_from("!f", data, offset)[0], offset + 4
    if kind == 15:
        return struct.unpack_from("!i", data, offset)[0], offset + 4
    if kind in (17, 18):
        length = struct.unpack_from("!H", data, offset)[0]
        offset += 2
        if kind == 18:
            length *= struct.unpack_from("!H", data, offset)[0]
            offset += 2
        if length > 10000:
            raise ProtocolError("Liste zu groß")
        result = []
        for _ in range(length):
            item, offset = decode_value(data, offset, depth + 1)
            result.append(item)
        return result, offset
    raise ProtocolError(f"Unbekannter UR-Datentyp {kind}")


class Decoder:
    """Frame TCP data and join indexed variable chunks into complete update cycles."""
    def __init__(self):
        self.buffer = bytearray()
        self.names = {}
        self.values = {}
        self.seen = set()
        self.have_setup = False
        self.generation = 0

    def feed(self, chunk):
        self.buffer.extend(chunk)
        events = []
        while len(self.buffer) >= 5:
            length = struct.unpack_from("!I", self.buffer)[0]
            if not 5 <= length <= 1_000_000:
                raise ProtocolError("Ungültige UR-Paketlänge")
            if len(self.buffer) < length:
                break
            msg = bytes(self.buffer[:length])
            del self.buffer[:length]
            if msg[4] == 16:
                offset = 5
                while offset + 5 <= len(msg):
                    size, kind = struct.unpack_from('!IB', msg, offset)
                    if size < 5 or offset + size > len(msg):
                        raise ProtocolError('Ungültiges UR-Statuspaket')
                    if kind == 0 and size >= 46:
                        events.append(('mode', {
                            'override': struct.unpack_from('!d', msg, offset + 22)[0],
                            'program_running': bool(msg[offset + 18]),
                            'program_paused': bool(msg[offset + 19]),
                        }))
                    offset += size
                if offset != len(msg):
                    raise ProtocolError('Unvollständiges UR-Statuspaket')
            if msg[4] != 25:
                continue
            if len(msg) < 16:
                raise ProtocolError("Unvollständige Variablenmeldung")
            subtype = msg[13]
            start = struct.unpack_from("!H", msg, 14)[0]
            if subtype == 0:
                if start == 0:
                    self.names.clear()
                    self.values.clear()
                    self.seen.clear()
                    self.have_setup = True
                    self.generation += 1
                    events.append(("reset", {}))
                if not self.have_setup:
                    continue
                for index, name in enumerate(msg[16:].decode("utf-8").splitlines(), start):
                    self.names[index] = name
                events.append(("names", {"names": list(self.names.values())}))
            elif subtype == 1:
                if start == 0:
                    self.seen.clear()
                    self.values.clear()
                offset, index = 16, start
                while offset < len(msg):
                    try:
                        value, offset = decode_value(msg, offset)
                        if offset >= len(msg) or msg[offset] != 10:
                            raise ProtocolError("Fehlender UR-Werttrenner")
                    except (IndexError, struct.error, UnicodeError) as exc:
                        raise ProtocolError("Unvollständiger UR-Wert") from exc
                    offset += 1
                    self.values[index] = value
                    self.seen.add(index)
                    index += 1
                if self.names and self.seen.issuperset(self.names):
                    events.append(("snapshot", {"values": {name: self.values[i] for i, name in self.names.items()},
                                                "generation": self.generation}))
                    self.seen.clear()
        return events


class URReader:
    def __init__(self, host, emit, port=30011):
        self.host, self.port, self.emit = host, port, emit
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name="ur-observer", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=6)

    def run(self):
        while not self.stop_event.is_set():
            decoder = Decoder()
            try:
                with socket.create_connection((self.host, self.port), timeout=3) as sock:
                    sock.settimeout(1)
                    self.emit("ur_connected", {})
                    last_data = time.monotonic()
                    while not self.stop_event.is_set():
                        try:
                            chunk = sock.recv(65536)
                        except socket.timeout:
                            if time.monotonic() - last_data > 3:
                                raise TimeoutError("Keine UR-Daten")
                            continue
                        if not chunk:
                            raise ConnectionError("UR-Verbindung getrennt")
                        last_data = time.monotonic()
                        for kind, data in decoder.feed(chunk):
                            self.emit("ur_" + kind, data)
            except (OSError, ValueError) as exc:
                self.emit("ur_disconnected", {"error": str(exc)})
            self.stop_event.wait(1)
