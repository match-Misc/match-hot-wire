"""Explicit game state machine. No robot commands; the physical button starts play."""
import math
import uuid

from .config import value_key
from .levels import override_level


class Game:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.phase = 'idle'
        self.player = None
        self.attempt = None
        self.lookup_token = None
        self.ready_since = None
        self.message = ''
        self.deadline = None
        self.ur_connected = False
        self.ur_error = 'Verbinde mit dem Roboter …'
        self.names = []
        self.values = {}
        self.running = None
        self.last_snapshot = None
        self.last_candidate = None
        self.candidate_since = None
        self.end_time = None
        self.result = None
        self.actions = []
        self.reader_ready = False
        self.reader_error = 'Verbinde mit dem Reader …'
        self.server_error = None
        self.override = None
        self.override_at = None

    def idle(self):
        self.phase, self.player, self.attempt = 'idle', None, None
        self.lookup_token, self.deadline, self.result = None, None, None
        self.ready_since = None
        self.message = ''

    def fail(self, message, now):
        if self.attempt and self.phase in ('playing', 'finishing'):
            self.store.state(self.attempt['id'], 'interrupted', message)
        self.phase, self.message, self.deadline = 'error', message, now + 8
        self.lookup_token = None

    def robot_lost(self, message, now):
        self.ur_error, self.running, self.last_snapshot = message, None, None
        if self.phase in ('ready', 'playing', 'finishing', 'identifying'):
            self.fail('Verbindung zum Spiel unterbrochen. Bitte den Tag erneut auflegen.', now)

    def event(self, kind, data, now):
        if kind == 'ur_connected':
            self.ur_connected = True
            self.names, self.values, self.running = [], {}, None
            self.ur_error = 'Warte auf Programmstart am Roboter'
        elif kind == 'ur_disconnected':
            self.ur_connected = False
            self.names, self.values = [], {}
            self.override, self.override_at = None, None
            self.robot_lost('Roboter nicht erreichbar', now)
        elif kind == 'ur_reset':
            self.names, self.values = [], {}
            self.ur_error, self.running, self.last_snapshot = 'UR-Programm wird neu gestartet', None, None
            if self.phase in ('playing', 'finishing'):
                self.fail('Das UR-Programm wurde während des Spiels neu gestartet. Bitte erneut anmelden.', now)
        elif kind == 'ur_names':
            self.names = data['names']
        elif kind == 'ur_snapshot':
            self.snapshot(data['values'], now)
        elif kind == 'ur_mode':
            try:
                override_level(data['override'])
            except (ValueError, KeyError):
                self.override, self.override_at = None, None
            else:
                self.override, self.override_at = data['override'], now
        elif kind == 'scan':
            if self.phase not in ('idle', 'identifying', 'ready') or self.running is True:
                return
            # The latest presented tag owns registration until the start edge.
            # Never fall back to the previous player while its replacement is unresolved.
            self.player, self.ready_since = None, None
            self.phase, self.message = 'identifying', 'Dein Tag wird gelesen …'
            self.lookup_token = uuid.uuid4().hex
            self.actions.append(('lookup', {'uid': data['uid'], 'token': self.lookup_token}))
        elif kind == 'lookup_done':
            if self.phase != 'identifying' or data['token'] != self.lookup_token:
                return
            if data.get('error'):
                self.server_error = data['error']
                self.fail(data['error'] + '. Bitte den Tag später erneut auflegen.', now)
            else:
                self.server_error = None
                self.player = dict(data['player'])
                self.phase, self.deadline = 'ready', None
                self.ready_since = now
                self.message = 'Drücke den Knopf am Roboter, wenn du bereit bist.'
        elif kind == 'submission_done':
            if data.get('error'):
                self.server_error = data['error']
            else:
                self.server_error = None
            if self.attempt and data['id'] == self.attempt['id'] and self.phase in ('sending', 'result'):
                self.phase, self.deadline = 'result', now + self.config.result_seconds
                self.message = ('Ergebnis gespeichert – Übertragung folgt.' if data.get('retryable') else
                                'Ergebnis lokal gesichert. Bitte das Betreuungsteam informieren.' if data.get('error') else
                                'Dein Ergebnis ist auf der Bestenliste angekommen.' if self.result['outcome'] == 'win' else
                                'Dein Ergebnis wurde gespeichert.')

    def snapshot(self, values, now):
        self.last_snapshot = now
        # PolyScope labels may differ in capitalization or show spaces for underscores.
        # Only use a normalized spelling when it identifies exactly one variable.
        resolved = dict(values)
        for name in self.config.variables:
            if name not in resolved:
                normalized = name.casefold().replace('_', '').replace(' ', '')
                matches = [key for key in values if key.casefold().replace('_', '').replace(' ', '') == normalized]
                if len(matches) == 1:
                    resolved[name] = values[matches[0]]
        values = resolved
        missing = [name for name in self.config.variables if name not in values]
        if missing:
            self.robot_lost('UR-Variablen fehlen: ' + ', '.join(missing), now)
            return
        running = values[self.config.running_variable]
        if type(running) is not bool:
            self.robot_lost('Die UR-Variable laeuft muss boolesch sein', now)
            return
        previous = self.running
        self.running, self.values, self.ur_error = running, values, None
        if running and previous is not True:
            if self.phase == 'ready' and previous is False and self.ready_since is not None and now >= self.ready_since:
                if not self.config.mapping_ready:
                    self.fail('Die Spielauswertung ist noch nicht eingerichtet.', now)
                    return
                try:
                    difficulty = self.difficulty(values, now)
                except ValueError as exc:
                    self.fail(str(exc), now)
                    return
                self.attempt = {'id': str(uuid.uuid4()), 'player': dict(self.player), 'difficulty': difficulty,
                                'override': self.override}
                self.store.begin(self.attempt)
                self.phase, self.message, self.deadline = 'playing', 'Viel Erfolg!', None
            elif self.phase in ('identifying', 'ready', 'finishing'):
                self.fail('Das Spiel hat ohne gültige Anmeldung begonnen. Bitte nach dem Spiel erneut scannen.', now)
        elif not running and previous is True and self.phase == 'playing':
            self.phase, self.message, self.deadline = 'finishing', 'Dein Ergebnis wird ausgewertet …', now + 5
            self.end_time = values[self.config.time_variable]
            self.store.finish_snapshot(self.attempt['id'], {key: values[key] for key in self.config.variables})
            self.last_candidate, self.candidate_since = None, None
        if self.phase == 'finishing' and not running:
            try:
                # The real UR's Zeit counter continues after Laeuft becomes false.
                # Freeze the observed edge time, not a later stable or delivery time.
                candidate = self.outcome({**values, self.config.time_variable: self.end_time})
            except ValueError:
                self.last_candidate, self.candidate_since = None, None
            else:
                if candidate != self.last_candidate:
                    self.last_candidate, self.candidate_since = candidate, now
                elif now - self.candidate_since >= self.config.settle_seconds:
                    self.finish(now)

    def finish(self, now):
        duration, outcome = self.last_candidate
        payload = {'submission_id': self.attempt['id'], 'player_id': self.attempt['player']['id'],
                   'game': 'heisser_draht', 'difficulty': self.attempt['difficulty'],
                   'duration_ms': duration, 'outcome': outcome}
        self.store.enqueue(payload)
        self.result = {'duration_ms': duration, 'outcome': outcome, 'difficulty': self.attempt['difficulty']}
        self.phase, self.message, self.deadline = 'sending', 'Dein Ergebnis wird gespeichert …', now + 5

    def difficulty(self, values, now):
        if self.config.difficulty_source == 'override':
            if self.override_at is None or now - self.override_at > 2:
                raise ValueError('Kein aktueller Geschwindigkeits-Override empfangen')
            return override_level(self.override)
        if not self.config.difficulty_variable:
            return self.config.fixed_difficulty
        key = value_key(values[self.config.difficulty_variable])
        mapping = {k.casefold(): v for k, v in self.config.difficulty_map.items()}
        result = mapping.get(key, key if not mapping else None)
        if not isinstance(result, str) or not result:
            raise ValueError('Unbekannte Schwierigkeit am Roboter')
        return result

    def outcome(self, values):
        duration = values[self.config.time_variable]
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError('Ungültige Zeit')
        ms = round(duration * (1000 if self.config.time_unit == 'seconds' else 1))
        if not 1 <= ms <= 2**63 - 1:
            raise ValueError('Ungültige Zeit')
        mapping = {k.casefold(): v for k, v in self.config.winner_map.items()}
        outcome = mapping.get(value_key(values[self.config.winner_variable]))
        if outcome not in ('win', 'loss', 'draw'):
            raise ValueError('Unbekannter Gewinnerwert')
        return ms, outcome

    def tick(self, now):
        if self.last_snapshot is not None and now - self.last_snapshot > 3:
            self.robot_lost('Warte auf laufendes UR-Programm', now)
        if (self.phase == 'finishing' and self.last_candidate is not None
                and self.running is False and self.last_snapshot is not None
                and now - self.candidate_since >= self.config.settle_seconds):
            self.finish(now)
        if self.deadline is not None and now >= self.deadline:
            if self.phase == 'finishing':
                self.fail('Das Ergebnis konnte nicht eindeutig ausgelesen werden. Bitte das Betreuungsteam informieren.', now)
            elif self.phase == 'sending':
                self.phase, self.message = 'result', 'Ergebnis gespeichert – Übertragung folgt.'
                self.deadline = now + self.config.result_seconds
            else:
                self.idle()

    def status(self):
        live_time = self.values.get(self.config.time_variable)
        if type(live_time) not in (int, float) or not math.isfinite(live_time):
            live_time = None
        if live_time is not None and self.config.time_unit == 'milliseconds':
            live_time /= 1000
        difficulty = self.attempt['difficulty'] if self.attempt else (
            override_level(self.override) if self.override is not None and self.config.difficulty_source == 'override'
            else self.config.fixed_difficulty)
        return {'phase': self.phase, 'player': self.player['name'] if self.player else None,
                'message': self.message, 'result': self.result, 'live_seconds': live_time,
                'robot': {'connected': self.ur_connected, 'ready': self.ur_error is None,
                          'running': self.running, 'message': self.ur_error,
                          'variables': {key: (self.values[key] if type(self.values[key]) in (bool, str, int)
                                              or type(self.values[key]) is float and math.isfinite(self.values[key]) else None)
                                        for key in self.config.variables if key in self.values}},
                'reader': {'ready': self.reader_ready, 'message': self.reader_error},
                'configured': self.config.mapping_ready, 'server_error': self.server_error,
                'difficulty': difficulty, 'override': self.attempt['override'] if self.attempt else self.override,
                'pending_results': self.store.pending_count()}
