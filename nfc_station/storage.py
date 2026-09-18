import json
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY, player_id INTEGER NOT NULL, player_name TEXT NOT NULL,
            difficulty TEXT NOT NULL, state TEXT NOT NULL, payload TEXT, response TEXT,
            error TEXT, created REAL NOT NULL, updated REAL NOT NULL, override REAL, end_snapshot TEXT)""")
        if 'override' not in {row[1] for row in self.conn.execute('PRAGMA table_info(attempts)')}:
            self.conn.execute('ALTER TABLE attempts ADD COLUMN override REAL')
        if 'end_snapshot' not in {row[1] for row in self.conn.execute('PRAGMA table_info(attempts)')}:
            self.conn.execute('ALTER TABLE attempts ADD COLUMN end_snapshot TEXT')
        with self.conn:
            self.conn.execute("UPDATE attempts SET state='interrupted', error='Station neu gestartet', updated=? WHERE state IN ('playing','finishing')", (time.time(),))

    def begin(self, attempt):
        with self.conn:
            self.conn.execute("INSERT INTO attempts (id,player_id,player_name,difficulty,state,created,updated,override) VALUES (?,?,?,?,'playing',?,?,?)",
                              (attempt['id'], attempt['player']['id'], attempt['player']['name'], attempt['difficulty'], time.time(), time.time(), attempt.get('override')))

    def state(self, attempt_id, state, error=None):
        with self.conn:
            self.conn.execute("UPDATE attempts SET state=?,error=?,updated=? WHERE id=?", (state, error, time.time(), attempt_id))

    def enqueue(self, payload):
        with self.conn:
            self.conn.execute("UPDATE attempts SET state='pending',payload=?,updated=? WHERE id=?",
                              (json.dumps(payload), time.time(), payload['submission_id']))

    def finish_snapshot(self, attempt_id, values):
        with self.conn:
            self.conn.execute("UPDATE attempts SET state='finishing',end_snapshot=?,updated=? WHERE id=?",
                              (json.dumps(values), time.time(), attempt_id))

    def pending(self):
        row = self.conn.execute("SELECT payload FROM attempts WHERE state='pending' ORDER BY created LIMIT 1").fetchone()
        return json.loads(row['payload']) if row else None

    def pending_count(self):
        return self.conn.execute("SELECT count(*) FROM attempts WHERE state IN ('pending','blocked')").fetchone()[0]

    def sent(self, attempt_id, response):
        with self.conn:
            self.conn.execute("UPDATE attempts SET state='sent',response=?,error=NULL,updated=? WHERE id=?",
                              (json.dumps(response), time.time(), attempt_id))

    def close(self):
        self.conn.close()
