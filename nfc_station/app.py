import fcntl
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from fastapi.responses import FileResponse, JSONResponse

from .config import Config
from .runtime import Runtime
from .speed import SpeedError


class LevelSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    level: int = Field(strict=True, ge=1, le=10)


def create_app(config=None, runtime=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    config = config or Config.load()

    @asynccontextmanager
    async def lifespan(app):
        lock_path = Path(config.database).with_suffix('.lock')
        with lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('Diese Spielstation läuft bereits') from exc
            app.state.station = runtime or Runtime(config)
            app.state.station.start()
            try:
                yield
            finally:
                app.state.station.stop()

    app = FastAPI(title='Heißer Draht · Spielstation', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get('/')
    def display():
        return FileResponse(Path(__file__).parent / 'display.html', headers={'Cache-Control': 'no-store'})

    @app.get('/api/state')
    def state():
        return JSONResponse(app.state.station.status(), headers={'Cache-Control': 'no-store'})

    @app.get('/health')
    def health():
        station = app.state.station
        alive = not station.stop_event.is_set()
        return JSONResponse({'ok': alive}, status_code=200 if alive else 503)

    @app.post('/api/level')
    def select_level(selection: LevelSelection, request: Request):
        origin = request.headers.get('origin')
        if (request.headers.get('x-station-control') != 'level'
                or (origin and origin != str(request.base_url).rstrip('/'))
                or request.headers.get('sec-fetch-site') == 'cross-site'):
            raise HTTPException(403, 'Levelwahl bitte direkt in der Stationsanzeige durchführen.')
        try:
            app.state.station.select_level(selection.level)
        except SpeedError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'ok': True}

    return app
