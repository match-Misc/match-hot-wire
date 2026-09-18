import concurrent.futures
import logging
import queue
import threading
import time

from .api import APIError, DashboardAPI
from .game import Game
from .reader import ScanReader
from .storage import Store
from .ur import URReader

LOG = logging.getLogger(__name__)


class Runtime:
    def __init__(self, config):
        self.config = config
        self.events = queue.Queue(maxsize=1000)
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.store = Store(config.database)
        self.game = Game(config, self.store)
        self.api = DashboardAPI(config.dashboard_url, config.station_key)
        self.reader = ScanReader(config.serial_port, self.emit)
        self.ur = URReader(config.ur_host, self.emit, config.ur_port)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='station-http')
        self.lookup = None
        self.submission = None
        self.next_retry = 0
        self.retry_delay = 1
        self.thread = None

    def emit(self, kind, data):
        while not self.stop_event.is_set():
            try:
                self.events.put((kind, data, time.monotonic()), timeout=.2)
                return
            except queue.Full:
                continue

    def start(self):
        self.thread = threading.Thread(target=self.run, name='game-station', daemon=True)
        self.thread.start()
        self.reader.start()
        self.ur.start()

    def stop(self):
        self.stop_event.set()
        self.reader.stop()
        self.ur.stop()
        if self.thread:
            self.thread.join(timeout=3)
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.store.close()

    def status(self):
        with self.lock:
            return self.game.status()

    def run(self):
        while not self.stop_event.is_set():
            try:
                event = self.events.get(timeout=.05)
            except queue.Empty:
                event = None
            try:
                with self.lock:
                    if event:
                        kind, data, when = event
                        # If the observer was delayed, it cannot safely reconstruct a live game.
                        if time.monotonic() - when > 2:
                            self.game.robot_lost('Spielbeobachtung verzögert', time.monotonic())
                        else:
                            before = self.game.phase
                            self.game.event(kind, data, when)
                            if kind == 'ur_names':
                                LOG.info('UR variable names: %s', ', '.join(data['names']))
                            if before != self.game.phase:
                                LOG.info('Station: %s -> %s', before, self.game.phase)
                    now = time.monotonic()
                    self.game.tick(now)
                    state = self.reader.status()
                    self.game.reader_ready = state['connected'] and state['verified']
                    self.game.reader_error = state['error']
                    self.complete_requests(now)
                    while self.game.actions:
                        kind, data = self.game.actions.pop(0)
                        if kind == 'lookup':
                            if self.lookup:
                                self.lookup[1].cancel()
                            self.lookup = (data['token'], self.pool.submit(self.api.player, data['uid']))
                    if self.submission is None and now >= self.next_retry:
                        payload = self.store.pending()
                        if payload:
                            self.submission = (payload['submission_id'], self.pool.submit(self.api.submit, payload))
            except Exception:
                LOG.exception('Station processing failed')
                with self.lock:
                    self.game.phase = 'error'
                    self.game.message = 'Die Station braucht Hilfe. Bitte das Betreuungsteam informieren.'
                    self.game.deadline = None
                # Do not continue assigning players after a failed durable state transition.
                self.stop_event.set()

    def complete_requests(self, now):
        if self.lookup and self.lookup[1].done():
            token, future = self.lookup
            self.lookup = None
            try:
                data = {'token': token, 'player': future.result()}
            except Exception as exc:
                data = {'token': token, 'error': str(exc) if isinstance(exc, APIError) else 'Spielerabfrage fehlgeschlagen'}
            self.game.event('lookup_done', data, now)
        if self.submission and self.submission[1].done():
            attempt_id, future = self.submission
            self.submission = None
            data = {'id': attempt_id}
            try:
                result = future.result()
            except Exception as exc:
                retryable = not isinstance(exc, APIError) or exc.retryable
                message = str(exc) if isinstance(exc, APIError) else 'Ergebnisübertragung fehlgeschlagen'
                self.store.state(attempt_id, 'pending' if retryable else 'blocked', message)
                data.update(error=message, retryable=retryable)
                self.next_retry = now + self.retry_delay
                self.retry_delay = min(self.retry_delay * 2, 30)
                LOG.warning('Result %s: %s (retry=%s)', attempt_id, message, retryable)
            else:
                self.store.sent(attempt_id, result)
                self.next_retry, self.retry_delay = now, 1
                LOG.info('Result %s accepted as %s (created=%s)', attempt_id, result['id'], result['created'])
            self.game.event('submission_done', data, now)
