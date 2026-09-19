import socket
import struct
import threading
import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nfc_station.app import create_app
from nfc_station.config import Config
from nfc_station.levels import LEVEL_OPTIONS, override_level
from nfc_station.runtime import Runtime
from nfc_station.speed import SpeedError, set_speed


@pytest.fixture
def runtime(tmp_path):
    r = Runtime(Config('/dev/fake', 'test', database=str(tmp_path/'speed.sqlite3')))
    now = time.monotonic()
    r.game.ur_connected = True
    r.game.ur_error = None
    r.game.running = False
    r.game.last_snapshot = r.game.override_at = now
    r.game.override = .35
    yield r
    r.stop()


def test_targets_match_existing_levels():
    assert len(LEVEL_OPTIONS) == 10
    for option in LEVEL_OPTIONS:
        assert override_level(option['override']) == option['name']
        assert 0 < option['override'] < 1


@pytest.mark.parametrize('phase', ['idle', 'ready'])
def test_selection_waiting_and_authenticated(runtime, monkeypatch, phase):
    runtime.game.phase = phase
    sent = []
    def setter(host, fraction, guard):
        guard(lambda: sent.append(fraction))
        assert runtime.status()['level_control']['pending']
    monkeypatch.setattr('nfc_station.runtime.set_speed', setter)
    runtime.select_level(4)
    assert sent == [.35]
    assert runtime.game.override == .35
    assert not runtime.speed_pending


@pytest.mark.parametrize('condition', ['playing','identifying','finishing','sending','result','error','stale','unknown','disconnected','pending'])
def test_no_write_when_not_available(runtime, monkeypatch, condition):
    setter = Mock()
    monkeypatch.setattr('nfc_station.runtime.set_speed', setter)
    if condition == 'stale': runtime.game.last_snapshot -= 2
    elif condition == 'unknown': runtime.game.running = None
    elif condition == 'disconnected': runtime.game.ur_connected = False
    elif condition == 'pending': runtime.speed_pending = True
    else: runtime.game.phase = condition
    with pytest.raises(SpeedError): runtime.select_level(3)
    setter.assert_not_called()


@pytest.mark.parametrize('change', ['start','stale','queued'])
def test_recheck_after_rtde_handshake(runtime, monkeypatch, change):
    sent = Mock()
    def setter(host, fraction, guard):
        if change == 'start': runtime.game.running = True
        elif change == 'stale': runtime.game.last_snapshot -= 2
        else: runtime.emit('ur_snapshot', {})
        guard(sent)
    monkeypatch.setattr('nfc_station.runtime.set_speed', setter)
    with pytest.raises(SpeedError): runtime.select_level(8)
    sent.assert_not_called()
    assert not runtime.speed_pending


def test_failure_not_reported_as_selected(runtime, monkeypatch):
    monkeypatch.setattr('nfc_station.runtime.set_speed', Mock(side_effect=TimeoutError()))
    with pytest.raises(SpeedError): runtime.select_level(9)
    assert runtime.game.override == .35
    assert not runtime.speed_pending
    assert 'nicht bestätigt' in runtime.speed_message


def test_api_validates_and_rejects_cross_origin(runtime):
    runtime.start = lambda: None
    runtime.select_level = Mock()
    with TestClient(create_app(runtime.config, runtime)) as client:
        assert client.post('/api/level', json={'level': 1}).status_code == 403
        headers = {'X-Station-Control': 'level'}
        assert client.post('/api/level', headers={**headers,'Origin':'http://evil.test'}, json={'level':1}).status_code == 403
        for value in [0, 11, True, '1', 1.5]:
            assert client.post('/api/level', headers=headers, json={'level':value}).status_code == 422
        runtime.select_level.assert_not_called()
        assert client.post('/api/level', headers={**headers,'Origin':'http://testserver'}, json={'level':2}).status_code == 200
        runtime.select_level.assert_called_once_with(2)


@pytest.mark.parametrize('occupied', [False, True])
def test_real_rtde_socket_protocol_and_confirmation(occupied):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(); listener.settimeout(3)
    received = []; errors = []
    def server():
        try:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(3)
                def read_exact(n):
                    data = b''
                    while len(data) < n:
                        chunk = conn.recv(n-len(data))
                        if not chunk: raise EOFError()
                        data += chunk
                    return data
                def receive():
                    size, kind = struct.unpack('!HB',read_exact(3))
                    return kind, read_exact(size-3)
                def send(kind, data):
                    packet = struct.pack('!HB',len(data)+3,kind)+data
                    for byte in packet: conn.sendall(bytes([byte]))
                assert receive() == (86,b'\x00\x02'); send(86,b'\x01')
                assert receive() == (79,struct.pack('!d',20.)+b'target_speed_fraction'); send(79,b'\x07DOUBLE')
                assert receive() == (73,b'speed_slider_mask,speed_slider_fraction')
                send(73,b'\x00IN_USE,IN_USE' if occupied else b'\x03UINT32,DOUBLE')
                if occupied:
                    assert conn.recv(1) == b''
                    return
                assert receive() == (83,b''); send(83,b'\x01')
                kind,data=receive(); assert kind==85
                received.append(struct.unpack('!BId',data))
                send(85,struct.pack('!Bd',7,.35))
                send(85,struct.pack('!Bd',7,.55))
                assert receive()==(85,struct.pack('!BId',3,0,.55))
        except Exception as exc: errors.append(exc)
    thread=threading.Thread(target=server);thread.start()
    try:
        if occupied:
            with pytest.raises(SpeedError,match='belegt'):
                set_speed('127.0.0.1',.55,lambda send: send(),port=listener.getsockname()[1])
            assert received==[]
        else:
            assert set_speed('127.0.0.1',.55,lambda send: send(),port=listener.getsockname()[1])==.55
            assert received==[(3,1,.55)]
    finally:
        thread.join(timeout=4);listener.close()
    assert not thread.is_alive()
    assert not errors
