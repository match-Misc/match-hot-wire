import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    serial_port: str
    station_key: str
    dashboard_url: str = "http://10.145.8.70:8001"
    ur_host: str = "ur10_r"
    ur_port: int = 30011
    running_variable: str = "laeuft"
    time_variable: str = "Zeit"
    winner_variable: str = "Gewinner"
    difficulty_source: str = "override"
    difficulty_variable: str = ""
    fixed_difficulty: str = ""
    time_unit: str = "seconds"
    winner_map: dict = field(default_factory=dict)
    difficulty_map: dict = field(default_factory=dict)
    database: str = str(ROOT / "station.sqlite3")
    result_seconds: float = 6
    settle_seconds: float = 0.5

    @property
    def variables(self):
        return [self.running_variable, self.time_variable, self.winner_variable] + (
            [self.difficulty_variable] if self.difficulty_variable else [])

    @property
    def mapping_ready(self):
        return bool(self.winner_map and (self.difficulty_source == "override" or self.difficulty_variable or self.fixed_difficulty))

    @classmethod
    def load(cls):
        env = {**dotenv_values(ROOT / ".env"), **dotenv_values(ROOT / ".station.env"), **os.environ}
        def get(name, default=""):
            return env.get(name) or default
        cfg = cls(
            serial_port=get("NFC_SERIAL_PORT"), station_key=get("NFC_STATION_KEY"),
            dashboard_url=get("STATION_DASHBOARD_URL", "http://10.145.8.70:8001").rstrip("/"),
            ur_host=get("STATION_UR_HOST", "ur10_r"),
            running_variable=get("STATION_RUNNING_VARIABLE", "laeuft"),
            time_variable=get("STATION_TIME_VARIABLE", "Zeit"),
            winner_variable=get("STATION_WINNER_VARIABLE", "Gewinner"),
            difficulty_source=get("STATION_DIFFICULTY_SOURCE", "override"),
            difficulty_variable=get("STATION_DIFFICULTY_VARIABLE"),
            fixed_difficulty=get("STATION_FIXED_DIFFICULTY"),
            time_unit=get("STATION_TIME_UNIT", "seconds"),
            winner_map=json.loads(get("STATION_WINNER_MAP", "{}")),
            difficulty_map=json.loads(get("STATION_DIFFICULTY_MAP", "{}")),
            database=get("STATION_DB", str(ROOT / "station.sqlite3")),
        )
        url = urlsplit(cfg.dashboard_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError("STATION_DASHBOARD_URL muss eine HTTP(S)-Adresse sein")
        if not cfg.serial_port or not cfg.station_key:
            raise ValueError("NFC_SERIAL_PORT und NFC_STATION_KEY fehlen")
        if cfg.time_unit not in ("seconds", "milliseconds"):
            raise ValueError("STATION_TIME_UNIT: seconds oder milliseconds")
        if cfg.difficulty_source not in ("override", "variable", "fixed"):
            raise ValueError("STATION_DIFFICULTY_SOURCE: override, variable oder fixed")
        if not isinstance(cfg.winner_map, dict) or any(v not in ("win", "loss", "draw") for v in cfg.winner_map.values()):
            raise ValueError("STATION_WINNER_MAP muss UR-Werte auf win/loss/draw abbilden")
        if not isinstance(cfg.difficulty_map, dict):
            raise ValueError("STATION_DIFFICULTY_MAP muss ein JSON-Objekt sein")
        return cfg


def value_key(value):
    """Keep bools distinct from integers; normalize strings only for configuration."""
    if isinstance(value, str):
        return value.strip().casefold()
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float):
        return str(int(value)) if value == int(value) else str(value)
    raise ValueError("Nicht unterstützter Variablenwert")
