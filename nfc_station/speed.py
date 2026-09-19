"""One-shot RTDE speed-slider write, with controller readback and no retries.

Protocol: https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/rtde-guide.html
"""
import socket
import struct
import time


class SpeedError(ValueError):
    pass


def set_speed(host, fraction, guarded_send, port=30004):
    """Prepare the session first; caller rechecks game state at the actual write."""
    with socket.create_connection((host, port), timeout=2) as sock:
        deadline = time.monotonic() + 4

        def send(kind, payload=b''):
            sock.sendall(struct.pack('!HB', len(payload) + 3, kind) + payload)

        def exact(size):
            data = b''
            while len(data) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SpeedError('Keine rechtzeitige Bestätigung vom Roboter.')
                sock.settimeout(min(remaining, 2))
                chunk = sock.recv(size - len(data))
                if not chunk:
                    raise SpeedError('RTDE-Verbindung unterbrochen.')
                data += chunk
            return data

        def receive(wanted):
            while True:
                size, kind = struct.unpack('!HB', exact(3))
                if not 3 <= size <= 4096:
                    raise SpeedError('Ungültige RTDE-Antwort.')
                payload = exact(size - 3)
                if kind == wanted:
                    return payload
                if kind != 77 and kind != 85:
                    raise SpeedError('Unerwartete RTDE-Antwort.')

        send(86, struct.pack('!H', 2))
        if receive(86) != b'\x01':
            raise SpeedError('RTDE-Protokoll wird nicht unterstützt.')
        send(79, struct.pack('!d', 20.) + b'target_speed_fraction')
        output = receive(79)
        if len(output) < 2 or not output[0] or output[1:] != b'DOUBLE':
            raise SpeedError('Override-Rückmeldung nicht verfügbar.')
        send(73, b'speed_slider_mask,speed_slider_fraction')
        inputs = receive(73)
        if len(inputs) < 2 or not inputs[0] or inputs[1:] != b'UINT32,DOUBLE':
            raise SpeedError('Override-Steuerung belegt oder nicht verfügbar (RTDE).')
        send(83)
        if receive(83) != b'\x01':
            raise SpeedError('RTDE konnte nicht gestartet werden.')
        guarded_send(lambda: send(85, struct.pack('!BId', inputs[0], 1, fraction)))
        while True:
            data = receive(85)
            if len(data) != 9 or data[0] != output[0]:
                raise SpeedError('Ungültige Override-Rückmeldung.')
            actual = struct.unpack('!d', data[1:])[0]
            if abs(actual - fraction) < .001:
                # Clear the write mask and release the recipe when closing the socket.
                send(85, struct.pack('!BId', inputs[0], 0, fraction))
                return actual
