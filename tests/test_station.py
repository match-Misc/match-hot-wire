import json
import os
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from nfc_station.api import APIError, DashboardAPI
from nfc_station.app import create_app
from nfc_station.config import Config
from nfc_station.game import Game
from nfc_station.levels import LEVELS, override_level
from nfc_station.storage import Store
from nfc_station.ur import Decoder, ProtocolError


def encoded(v):
    if type(v) is bool:
        return bytes([13, int(v)])
    if type(v) is int:
        return b'\x0f' + struct.pack('!i', v)
    if type(v) is float:
        return b'\x10' + struct.pack('!f', v)
    if isinstance(v, str):
        data = v.encode()
        return b'\x03' + struct.pack('!H', len(data)) + data
    if isinstance(v, list):
        return b'\x11' + struct.pack('!H', len(v)) + b''.join(encoded(x) for x in v)
    raise ValueError(v)


def packet(subtype, start, payload):
    body = b'\x19' + struct.pack('!QBH', 1234, subtype, start) + payload
    return struct.pack('!I', len(body) + 4) + body


def names(*items, start=0):
    return packet(0, start, ('\n'.join(items) + '\n').encode())


def update(*items, start=0):
    return packet(1, start, b''.join(encoded(x) + b'\n' for x in items))


def test_ur_framing_indexed_chunks_and_nested_values():
    d = Decoder()
    raw = names('Zeit', 'laeuft') + names('Gewinner', 'Liste', start=2)
    events = []
    for byte in raw:
        events += d.feed(bytes([byte]))
    assert events[-1][1]['names'] == ['Zeit', 'laeuft', 'Gewinner', 'Liste']
    assert not d.feed(update(69.25, False))
    snapshots = d.feed(update(True, ['a\nb', [10, False]], start=2))
    assert snapshots == [('snapshot', {'values': {'Zeit': 69.25, 'laeuft': False, 'Gewinner': True,
                                                'Liste': ['a\nb', [10, False]]}, 'generation': 1})]
    # The next cycle must not combine a new time with the old winner.
    assert not d.feed(update(71.0, True))
    assert d.feed(update(False, [], start=2))[0][1]['values']['Gewinner'] is False


def test_ur_never_guesses_names_or_reuses_values_after_restart():
    d = Decoder()
    assert d.feed(update(69.0, True)) == []
    d.feed(names('Zeit', 'Gewinner'))
    assert d.feed(update(69.0, True))[0][1]['values']['Zeit'] == 69
    d.feed(names('Gewinner', 'Zeit'))
    assert d.feed(update(False)) == []
    assert d.feed(update(22.0, start=1))[0][1]['values'] == {'Gewinner': False, 'Zeit': 22}
    with pytest.raises(ProtocolError):
        Decoder().feed(b'\x00\x00\x00\x01\x19')
    with pytest.raises(ProtocolError):
        Decoder().feed(packet(1, 0, b'\xff\n'))


@pytest.fixture
def game(tmp_path):
    cfg = Config('/dev/fake', 'secret', database=str(tmp_path/'station.sqlite3'),
                 winner_map={'true': 'win', 'false': 'loss'}, fixed_difficulty='leicht', difficulty_source='fixed')
    store = Store(cfg.database)
    g = Game(cfg, store)
    g.event('ur_connected', {}, 0)
    g.event('ur_names', {'names': cfg.variables}, 0)
    g.snapshot({'laeuft': False, 'Zeit': 0.0, 'Gewinner': False}, 0)
    yield g
    store.close()


def register(g, now=1):
    g.event('scan', {'uid': '53867A9F530001'}, now)
    action, data = g.actions.pop()
    assert action == 'lookup'
    g.event('lookup_done', {'token': data['token'], 'player': {'id': 3, 'name': 'RosMaster69'}}, now + .1)


def snapshot(g, running, duration, winner, now, **extra):
    g.snapshot({'laeuft': running, 'Zeit': duration, 'Gewinner': winner, **extra}, now)


@pytest.mark.parametrize('winner,outcome', [(True, 'win'), (False, 'loss')])
def test_complete_game_captures_player_and_returns_to_idle(game, winner, outcome):
    register(game)
    snapshot(game, True, 0.0, False, 2)
    assert game.phase == 'playing'
    game.event('scan', {'uid': '11223344'}, 3)
    assert game.actions == []
    snapshot(game, False, 69.0, winner, 4)
    assert game.store.pending() is None
    snapshot(game, False, 69.0, winner, 4.6)
    payload = game.store.pending()
    assert payload == {'submission_id': game.attempt['id'], 'player_id': 3, 'game': 'heisser_draht',
                       'difficulty': 'leicht', 'duration_ms': 69000, 'outcome': outcome}
    snapshot(game, False, 69.0, winner, 5)
    assert game.store.pending_count() == 1
    game.event('submission_done', {'id': game.attempt['id']}, 5)
    assert game.phase == 'result'
    game.tick(12)
    assert game.phase == 'idle' and game.player is None


def test_end_time_frozen_while_winner_must_stabilize(game):
    register(game)
    snapshot(game, True, 0.0, False, 2)
    snapshot(game, False, 69.0, 'unknown', 4)
    snapshot(game, False, 69.2, True, 4.2)
    snapshot(game, False, 70.0, False, 4.5)
    snapshot(game, False, 70.0, False, 4.9)
    assert game.store.pending() is None
    snapshot(game, False, 70.0, False, 5.1)
    assert game.store.pending()['duration_ms'] == 69000
    assert game.store.pending()['outcome'] == 'loss'
    assert json.loads(game.store.conn.execute('SELECT end_snapshot FROM attempts').fetchone()[0])['Zeit'] == 69.0


def test_real_ur_counter_continues_after_game_end(game):
    register(game)
    snapshot(game, True, 181.824, False, 2)
    snapshot(game, False, 182.824, False, 3)
    for offset in (.1, .2, .3, .4, .6):
        snapshot(game, False, 182.824 + offset, False, 3 + offset)
        game.tick(3 + offset)
    assert game.phase == 'sending'
    assert game.store.pending()['duration_ms'] == 182824
    assert game.store.pending()['outcome'] == 'loss'


def test_start_during_lookup_does_not_claim_running_game(game):
    game.event('scan', {'uid': '53867A9F530001'}, 1)
    token = game.lookup_token
    snapshot(game, True, 2.0, False, 2)
    game.event('lookup_done', {'token': token, 'player': {'id': 3, 'name': 'Late'}}, 3)
    assert game.phase == 'error'
    assert game.attempt is None
    snapshot(game, False, 69.0, True, 4)
    game.tick(12)
    assert game.phase == 'idle' and game.store.pending() is None


def test_greeting_waits_for_start_without_expiring(game):
    register(game)
    for t in range(2, 602):
        snapshot(game, False, 0.0, False, t)
        game.tick(t)
    assert game.phase == 'ready' and game.player['id'] == 3
    snapshot(game, True, 0.0, False, 603)
    assert game.phase == 'playing' and game.attempt['player']['id'] == 3


def test_player_can_change_until_start_and_score_belongs_to_replacement(game):
    register(game)
    game.event('scan', {'uid': '11223344'}, 2)
    assert game.phase == 'identifying' and game.player is None
    _, request = game.actions.pop()
    game.event('lookup_done', {'token': request['token'], 'player': {'id': 4, 'name': 'Next'}}, 2.1)
    assert game.status()['player'] == 'Next'
    snapshot(game, True, 0.0, False, 3)
    game.event('scan', {'uid': '55667788'}, 3.1)
    assert not game.actions and game.attempt['player']['id'] == 4
    snapshot(game, False, 12.0, True, 4)
    snapshot(game, False, 12.0, True, 4.6)
    assert game.store.pending()['player_id'] == 4


def test_latest_scan_wins_even_with_out_of_order_lookup_responses(game):
    register(game)
    game.event('scan', {'uid': '11223344'}, 2)
    old_token = game.lookup_token
    game.event('scan', {'uid': '55667788'}, 2.1)
    token = game.lookup_token
    game.event('lookup_done', {'token': old_token, 'player': {'id': 4, 'name': 'Old'}}, 2.2)
    assert game.phase == 'identifying' and game.player is None
    game.event('lookup_done', {'token': token, 'player': {'id': 5, 'name': 'Latest'}}, 2.3)
    game.event('lookup_done', {'token': old_token, 'error': 'Late failure'}, 2.4)
    assert game.phase == 'ready' and game.player['id'] == 5


@pytest.mark.parametrize('start_during_lookup', [True, False])
def test_unresolved_replacement_never_scores_for_previous_player(game, start_during_lookup):
    register(game)
    game.event('scan', {'uid': '11223344'}, 2)
    token = game.lookup_token
    if start_during_lookup:
        snapshot(game, True, 0.0, False, 2.1)
        game.event('lookup_done', {'token': token, 'player': {'id': 4, 'name': 'Late'}}, 2.2)
    else:
        game.event('lookup_done', {'token': token, 'error': 'Unknown tag'}, 2.1)
        snapshot(game, True, 0.0, False, 2.2)
    assert game.phase == 'error' and game.attempt is None and game.player is None
    snapshot(game, False, 12.0, False, 3)
    snapshot(game, False, 12.0, False, 3.6)
    assert game.store.pending() is None


def test_queued_start_before_lookup_completion_cannot_claim_player(game):
    game.event('scan', {'uid': '53867A9F530001'}, 1)
    token = game.lookup_token
    game.event('lookup_done', {'token': token, 'player': {'id': 3, 'name': 'Late'}}, 3)
    # This UR event was received before the HTTP lookup completed, but processed later.
    snapshot(game, True, 1.0, False, 2.9)
    assert game.phase == 'error' and game.attempt is None


def test_actual_ur_capitalization_and_unset_winner(game):
    game.snapshot({'Laeuft': False, 'Zeit': 0, 'Gewinner': None}, 1)
    assert game.ur_error is None and game.running is False
    register(game, 2)
    game.snapshot({'Laeuft': True, 'Zeit': 1.0, 'Gewinner': False}, 3)
    assert game.phase == 'playing'


@pytest.mark.parametrize('kind', ['ur_disconnected', 'ur_reset'])
def test_interrupted_game_never_becomes_a_score(game, kind):
    register(game)
    snapshot(game, True, 1.0, False, 2)
    game.event(kind, {}, 3)
    snapshot(game, False, 69.0, True, 4)
    snapshot(game, False, 69.0, True, 5)
    assert game.store.pending() is None
    assert game.store.conn.execute('SELECT state FROM attempts').fetchone()[0] == 'interrupted'


def test_stale_ur_and_invalid_winner_are_not_submitted(game):
    register(game)
    snapshot(game, True, 1.0, False, 2)
    game.tick(6)
    assert game.phase == 'error' and game.store.pending() is None
    game.tick(15)
    snapshot(game, False, 0.0, False, 15)
    register(game, 16)
    snapshot(game, True, 0.0, False, 17)
    snapshot(game, False, 69.0, '???', 18)
    snapshot(game, False, 69.0, '???', 22)
    game.tick(23.1)
    assert game.phase == 'error' and game.store.pending() is None


def test_difficulty_is_captured_at_start(game):
    game.config.difficulty_source = 'variable'
    game.config.difficulty_variable = 'Schwer'
    game.config.difficulty_map = {'false': 'leicht', 'true': 'schwer'}
    snapshot(game, False, 0.0, False, 0, Schwer=True)
    register(game)
    snapshot(game, True, 0.0, False, 2, Schwer=True)
    snapshot(game, False, 69.0, True, 3, Schwer=False)
    snapshot(game, False, 69.0, True, 3.6, Schwer=False)
    assert game.store.pending()['difficulty'] == 'schwer'


def mode(override, scaling=.123):
    payload = struct.pack('!Q7B2B3dB', 1234, 1, 1, 1, 0, 0, 1, 0, 7, 0, override, scaling, 1., 0)
    sub = struct.pack('!IB', len(payload) + 5, 0) + payload
    return struct.pack('!IB', len(sub) + 5, 16) + sub


def test_reads_slider_override_instead_of_safety_speed_scaling():
    assert Decoder().feed(mode(.65))[0] == ('mode', {'override': .65, 'program_running': True, 'program_paused': False})


@pytest.mark.parametrize('index', range(10))
def test_override_buckets(index):
    assert override_level(index / 10) == LEVELS[index]
    assert override_level((index + .99) / 10) == LEVELS[index]
    assert override_level(1.0) == LEVELS[-1]


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -.01, 1.01, True, '0.5'])
def test_invalid_override_not_accepted(invalid):
    with pytest.raises(ValueError): override_level(invalid)


def test_actual_override_level_frozen_during_game_and_final_program_stop(game):
    game.config.difficulty_source = 'override'
    game.event('ur_mode', {'override': .65}, 1)
    register(game)
    snapshot(game, True, 0.0, False, 2)
    game.event('ur_mode', {'override': .15}, 3)
    snapshot(game, False, 69.0, True, 4)
    # A UR program may stop immediately after reporting its final snapshot.
    game.tick(4.6)
    assert game.store.pending()['difficulty'] == 'Hochspannungsheld'
    assert game.store.conn.execute('SELECT override FROM attempts').fetchone()[0] == .65


def test_stale_override_blocks_start(game):
    game.config.difficulty_source = 'override'
    game.event('ur_mode', {'override': .65}, 0)
    register(game)
    snapshot(game, True, 0.0, False, 3)
    assert game.phase == 'error' and game.attempt is None


def test_outbox_survives_restart_with_identical_uuid(game):
    register(game)
    snapshot(game, True, 0.0, False, 2)
    snapshot(game, False, 69.0, True, 3)
    snapshot(game, False, 69.0, True, 3.6)
    before = game.store.pending()
    other = Store(game.config.database)
    assert other.pending() == before
    other.sent(before['submission_id'], {'id': 10, 'created': False, 'removed': False})
    assert other.pending() is None
    other.close()


def until(predicate, timeout=6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.025)
    assert predicate(), 'Bedingung nicht rechtzeitig erfüllt'


@pytest.mark.parametrize('winner,outcome', [(True, 'win'), (False, 'loss')])
def test_station_end_to_end_serial_ur_http_retry_and_display(tmp_path, winner, outcome):
    """Real local sockets + serial PTY, no physical robot commands or real results."""
    stop = threading.Event()
    received = []
    responses = []
    robot_values = [False, 0.0, False]
    robot_lock = threading.Lock()
    counter_after_end = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self, data):
            body = json.dumps(data).encode()
            self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            assert self.headers['X-API-Key'] == 'test-key'
            if self.path == '/api/games': self.respond({'games': [{'id': 'heisser_draht', 'difficulties': ['leicht']}]})
            else: self.respond({'id': 3, 'name': '<RosMaster69>'})
        def do_POST(self):
            assert self.path == '/api/results'
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if len(received) == 1:
                # Simulate a committed result whose response was lost.
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            else:
                result = {'id': 42, 'created': False, 'removed': False}
                responses.append(result)
                self.respond(result)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    http_thread = threading.Thread(target=server.serve_forever, daemon=True); http_thread.start()
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(); listener.settimeout(.2)

    def robot():
        while not stop.is_set():
            try: conn, _ = listener.accept()
            except socket.timeout: continue
            with conn:
                try:
                    conn.sendall(names('laeuft', 'Zeit', 'Gewinner'))
                    while not stop.wait(.05):
                        with robot_lock:
                            data = update(*robot_values)
                            if counter_after_end.is_set():
                                robot_values[1] += .05
                        conn.sendall(data)
                except OSError: pass
    robot_thread = threading.Thread(target=robot, daemon=True); robot_thread.start()
    master, slave = os.openpty()
    cfg = Config(os.ttyname(slave), 'test-key', dashboard_url=f'http://127.0.0.1:{server.server_port}',
                 ur_host='127.0.0.1', ur_port=listener.getsockname()[1], database=str(tmp_path/'station.sqlite3'),
                 winner_map={'true': 'win', 'false': 'loss'}, fixed_difficulty='leicht', difficulty_source='fixed', result_seconds=.4)
    app = create_app(cfg)
    try:
        with TestClient(app) as client:
            until(lambda: app.state.station.reader.connected)
            os.write(master, b'{"type":"hello","reader":"MFRC522","version":130}\n')
            until(lambda: client.get('/api/state').json()['robot']['ready'])
            until(lambda: client.get('/api/state').json()['reader']['ready'])
            os.write(master, b'{"type":"scan","uid":"53867A9F530001"}\n')
            until(lambda: client.get('/api/state').json()['phase'] == 'ready')
            assert client.get('/api/state').json()['player'] == '<RosMaster69>'
            with robot_lock: robot_values[:] = [True, 1.0, False]
            until(lambda: client.get('/api/state').json()['phase'] == 'playing')
            os.write(master, b'{"type":"scan","uid":"11223344"}\n')
            with robot_lock:
                robot_values[:] = [False, 69.0, winner]
                counter_after_end.set()
            until(lambda: len(received) == 2)
            assert received[0] == received[1]
            assert received[0]['player_id'] == 3 and received[0]['duration_ms'] == 69000
            assert received[0]['outcome'] == outcome
            until(lambda: client.get('/api/state').json()['phase'] == 'idle')
            until(lambda: client.get('/api/state').json()['pending_results'] == 0)
            assert client.get('/health').json() == {'ok': True}
            assert 'textContent' in client.get('/').text
            assert 'test-key' not in client.get('/api/state').text
    finally:
        stop.set()
        robot_thread.join(timeout=2)
        listener.close()
        server.shutdown(); server.server_close(); http_thread.join(timeout=2)
        os.close(master); os.close(slave)
