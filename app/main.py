"""Snapcast Multiroom Controller.

A small FastAPI app that manages one `snapclient` process per configured
audio output (USB DAC, HDMI, built-in card, ...) on this host, so a single
container can feed many rooms - the same idea as running one virtual
Sendspin player per output, but on top of the more mature Snapcast
protocol/ecosystem, and with a directly tunable per-player buffer/latency.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config_store import ConfigStore
from devices import list_alsa_devices
from manager import PlayerManager
from models import PlayerConfig, PlayerUpdate, Settings

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "info").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("multiroom.main")

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config"))
LOG_PATH = Path(os.environ.get("LOG_PATH", "/app/logs"))
LOG_PATH.mkdir(parents=True, exist_ok=True)

store = ConfigStore(CONFIG_PATH)
settings = store.load_settings()

# Environment variables take precedence over a persisted default, so a
# docker-compose redeploy always reflects the configured Snapserver host.
if os.environ.get("SNAPSERVER_HOST"):
    settings.default_snapserver_host = os.environ["SNAPSERVER_HOST"]
if os.environ.get("SNAPSERVER_PORT"):
    settings.default_snapserver_port = int(os.environ["SNAPSERVER_PORT"])

manager = PlayerManager(LOG_PATH, settings.default_snapserver_host, settings.default_snapserver_port)


def _persist_players() -> None:
    store.save_players([rt.config for rt in manager.players.values()])


@asynccontextmanager
async def lifespan(app: FastAPI):
    for cfg in store.load_players():
        manager.register(cfg)
    for name, rt in list(manager.players.items()):
        if rt.config.enabled:
            await manager.start(name)
    logger.info("Snapcast Multiroom Controller started with %d configured player(s)", len(manager.players))
    yield
    await manager.stop_all()


app = FastAPI(title="Snapcast Multiroom Controller", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"status": "ok", "players": len(manager.players)}


@app.get("/api/devices")
async def get_devices():
    return list_alsa_devices()


@app.get("/api/settings")
async def get_settings():
    return settings.model_dump()


@app.put("/api/settings")
async def update_settings(new_settings: Settings):
    global settings
    settings = new_settings
    manager.default_host = settings.default_snapserver_host
    manager.default_port = settings.default_snapserver_port
    store.save_settings(settings)
    return settings.model_dump()


@app.get("/api/players")
async def get_players():
    return manager.all_status()


@app.post("/api/players", status_code=201)
async def create_player(config: PlayerConfig):
    if config.name in manager.players:
        raise HTTPException(status_code=409, detail="A player with this name already exists")
    manager.register(config)
    _persist_players()
    if config.enabled:
        await manager.start(config.name)
    return manager.status(config.name)


@app.put("/api/players/{name}")
async def update_player(name: str, update: PlayerUpdate):
    if name not in manager.players:
        raise HTTPException(status_code=404, detail="Unknown player")
    rt = manager.players[name]
    data = rt.config.model_dump()
    data.update({k: v for k, v in update.model_dump().items() if v is not None})
    rt.config = PlayerConfig(**data)
    _persist_players()

    was_running = rt.status in ("running", "starting")
    if was_running and rt.config.enabled:
        await manager.restart(name)
    elif not rt.config.enabled and was_running:
        await manager.stop(name)
    return manager.status(name)


@app.delete("/api/players/{name}")
async def delete_player(name: str):
    if name not in manager.players:
        raise HTTPException(status_code=404, detail="Unknown player")
    await manager.stop(name)
    manager.unregister(name)
    _persist_players()
    return {"ok": True}


@app.post("/api/players/{name}/start")
async def start_player(name: str):
    if name not in manager.players:
        raise HTTPException(status_code=404, detail="Unknown player")
    await manager.start(name)
    return manager.status(name)


@app.post("/api/players/{name}/stop")
async def stop_player(name: str):
    if name not in manager.players:
        raise HTTPException(status_code=404, detail="Unknown player")
    await manager.stop(name)
    return manager.status(name)


@app.post("/api/players/{name}/restart")
async def restart_player(name: str):
    if name not in manager.players:
        raise HTTPException(status_code=404, detail="Unknown player")
    await manager.restart(name)
    return manager.status(name)


@app.get("/api/players/{name}/logs")
async def get_logs(name: str, lines: int = 100):
    if name not in manager.players:
        raise HTTPException(status_code=404, detail="Unknown player")
    return manager.logs(name, lines)


static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")
