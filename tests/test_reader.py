import os
import time

from nfc_station.reader import ScanReader


def until(predicate, timeout=6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.02)
    assert predicate()


def test_reader_requires_verified_identity_and_valid_uid():
    events = []
    reader = ScanReader('/unused', lambda *args: events.append(args))
    reader.handle_message({'type': 'scan', 'uid': '53867A9F530001'})
    reader.handle_message({'type': 'hello', 'reader': 'MFRC522', 'version': 0})
    reader.handle_message({'type': 'scan', 'uid': '53867A9F530001'})
    assert not events and not reader.verified
    reader.handle_message({'type': 'hello', 'reader': 'MFRC522', 'version': 130})
    for bad in (None, [], 12, {'type': 'scan', 'uid': 'xyz'}, {'type': 'scan', 'uid': 1234}):
        reader.handle_message(bad)
    assert not events
    reader.handle_message({'type': 'scan', 'uid': '53:86:7a:9f:53:00:01'})
    assert events == [('scan', {'uid': '53867A9F530001'})]
    assert reader.status()['scan_count'] == 1


def test_serial_fragmented_lines_boot_noise_and_reconnect(tmp_path):
    events = []
    master, slave = os.openpty()
    second_master, second_slave = os.openpty()
    port = tmp_path/'reader'
    port.symlink_to(os.ttyname(slave))
    reader = ScanReader(str(port), lambda *args: events.append(args))
    try:
        reader.start()
        until(lambda: reader.connected)
        os.write(master, b'\xffboot\n' + b'x'*600 + b'\n')
        os.write(master, b'{"type":"hello","reader":"MFRC522","version":130}\n')
        until(lambda: reader.verified)
        os.write(master, b'{"type":"scan","uid":"5386')
        time.sleep(.35)  # A read timeout must not discard an incomplete JSON line.
        os.write(master, b'7A9F530001"}\n')
        until(lambda: len(events) == 1)
        assert events[0] == ('scan', {'uid': '53867A9F530001'})
        os.close(master)
        master = None
        until(lambda: not reader.connected and not reader.verified)
        port.unlink()
        port.symlink_to(os.ttyname(second_slave))
        until(lambda: reader.connected)
        os.write(second_master, b'{"type":"scan","uid":"11223344"}\n')
        time.sleep(.1)
        assert len(events) == 1  # Reconnection needs a new hello.
        os.write(second_master, b'{"type":"hello","reader":"MFRC522","version":130}\n')
        until(lambda: reader.verified)
        os.write(second_master, b'{"type":"scan","uid":"11223344"}\n')
        until(lambda: len(events) == 2)
        assert events[-1] == ('scan', {'uid': '11223344'})
    finally:
        reader.stop()
        for fd in (master, slave, second_master, second_slave):
            if fd is not None:
                os.close(fd)
    assert not reader.thread.is_alive()
