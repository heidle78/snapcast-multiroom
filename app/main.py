"""Snapcast Multiroom Controller.

A small FastAPI app that manages one `snapclient` process per configured
audio output (USB DAC, HDMI, built-in card, ...) on this host, so a single
container can feed many rooms - the same idea as running one virtual
Sendspin player per output, but on top of the more mature Snapcast
protocol/ecosystem, and with a directly tunable per-player buffer/latency.
"""
import json
import logging
import os
import platform
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import pulse
from config_store import ConfigStore
from devices import list_output_targets
from manager import PlayerManager
from models import CustomSinkConfig, PlayerConfig, PlayerUpdate, Settings

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

manager = PlayerManager(
    LOG_PATH, settings.default_snapserver_host, settings.default_snapserver_port, backend=settings.backend
)


def _persist_players() -> None:
    store.save_players([rt.config for rt in manager.players.values()])


def _init_pulse_if_needed() -> None:
    """Start PulseAudio and (re)sync hardware + custom sinks if backend=pulse."""
    if settings.backend != "pulse":
        return
    if not pulse.start_pulseaudio(log_path=LOG_PATH):
        logger.error("backend=pulse but PulseAudio failed to start - falling back is NOT automatic, fix and restart")
        return
    pulse.sync_hardware_sinks()
    pulse.replay_custom_sinks(store.load_sinks())


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_pulse_if_needed()
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
    return list_output_targets(settings.backend)


@app.get("/api/settings")
async def get_settings():
    return {**settings.model_dump(), "pulse_available": pulse.pulse_available()}


@app.put("/api/settings")
async def update_settings(new_settings: Settings):
    global settings
    backend_changed = new_settings.backend != settings.backend
    settings = new_settings
    manager.default_host = settings.default_snapserver_host
    manager.default_port = settings.default_snapserver_port
    manager.backend = settings.backend
    store.save_settings(settings)
    if backend_changed and settings.backend == "pulse":
        _init_pulse_if_needed()
    return {**settings.model_dump(), "pulse_available": pulse.pulse_available()}


# -- custom sinks (PulseAudio only) --------------------------------------

@app.get("/api/sinks")
async def get_sinks():
    if settings.backend != "pulse":
        return {"backend": settings.backend, "sinks": [], "custom": []}
    return {
        "backend": "pulse",
        "sinks": pulse.list_sinks(),
        "custom": [s.model_dump() for s in store.load_sinks()],
    }


@app.post("/api/sinks", status_code=201)
async def create_sink(cfg: CustomSinkConfig):
    if settings.backend != "pulse":
        raise HTTPException(status_code=400, detail="Custom sinks require backend=pulse (see System Settings)")
    try:
        pulse.create_sink(cfg)
    except pulse.PulseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    sinks = store.load_sinks()
    sinks = [s for s in sinks if s.name != cfg.name] + [cfg]
    store.save_sinks(sinks)
    return {"ok": True}


@app.delete("/api/sinks/{name}")
async def delete_sink(name: str):
    if settings.backend != "pulse":
        raise HTTPException(status_code=400, detail="Custom sinks require backend=pulse")
    try:
        pulse.remove_sink(name)
    except pulse.PulseError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    sinks = [s for s in store.load_sinks() if s.name != name]
    store.save_sinks(sinks)
    return {"ok": True}


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


# -- aggregated logs view --------------------------------------------------

@app.get("/api/logs")
async def get_all_logs(player: str = "", search: str = "", lines: int = 500):
    """Merged, time-sorted log lines across all players, for the Logs view."""
    entries: list[dict] = []
    names = [player] if player and player in manager.players else list(manager.players)
    for name in names:
        for line in manager.logs(name, lines):
            entries.append({"player": name, "line": line})
    if search:
        needle = search.lower()
        entries = [e for e in entries if needle in e["line"].lower()]
    # log lines are already timestamp-prefixed per player and roughly
    # chronological within a player; sort by that prefix for a merged view
    entries.sort(key=lambda e: e["line"][:21])
    return entries[-lines:]


@app.get("/api/players/{name}/volume")
async def get_player_volume(name: str):
    """Get current PA sink volume (0-100) for the player's device."""
    if name not in manager.players:
        raise HTTPException(status_code=404, detail="Unknown player")
    rt = manager.players[name]
    if settings.backend != "pulse":
        raise HTTPException(status_code=400, detail="Volume control requires PulseAudio backend")
    vol = pulse.get_sink_volume(rt.config.device)
    return {"name": name, "device": rt.config.device, "volume": vol}


@app.put("/api/players/{name}/volume")
async def set_player_volume(name: str, body: dict):
    """Set PA sink volume (0-100) for the player's device."""
    if name not in manager.players:
        raise HTTPException(status_code=404, detail="Unknown player")
    rt = manager.players[name]
    if settings.backend != "pulse":
        raise HTTPException(status_code=400, detail="Volume control requires PulseAudio backend")
    volume = max(0, min(100, int(body.get("volume", 100))))
    ok = pulse.set_sink_volume(rt.config.device, volume)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to set volume")
    return {"name": name, "device": rt.config.device, "volume": volume}


@app.get("/api/sinks/{name}/volume")
async def get_sink_volume(name: str):
    """Get current PA sink volume (0-100)."""
    vol = pulse.get_sink_volume(name)
    return {"name": name, "volume": vol}


@app.put("/api/sinks/{name}/volume")
async def set_sink_volume(name: str, body: dict):
    """Set PA sink volume (0-100)."""
    volume = max(0, min(100, int(body.get("volume", 100))))
    ok = pulse.set_sink_volume(name, volume)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to set volume")
    return {"name": name, "volume": volume}


@app.post("/api/sinks/{name}/test")
async def test_sink(name: str):
    """Play a short test tone on the given PA sink."""
    if settings.backend != "pulse":
        raise HTTPException(status_code=400, detail="Requires PulseAudio backend")
    import asyncio
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, pulse.play_test_tone, name)
    if not ok:
        raise HTTPException(status_code=500, detail="paplay failed — check sink name and PA status")
    return {"ok": True, "sink": name}


@app.get("/api/diagnostics")
async def get_diagnostics():
    """Downloadable text bundle: config (redacted), sinks, devices, recent logs."""
    lines_out = []
    lines_out.append("=== Snapcast Multiroom Controller diagnostics ===")
    lines_out.append(f"generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines_out.append(f"python: {platform.python_version()}  platform: {platform.platform()}")
    lines_out.append("")
    lines_out.append("--- settings ---")
    lines_out.append(json.dumps(settings.model_dump(), indent=2))
    lines_out.append("")
    lines_out.append("--- players ---")
    for status in manager.all_status():
        lines_out.append(json.dumps(status, indent=2))
    lines_out.append("")
    lines_out.append("--- devices ---")
    lines_out.append(json.dumps(list_output_targets(settings.backend), indent=2))
    if settings.backend == "pulse":
        lines_out.append("")
        lines_out.append("--- pulseaudio sinks ---")
        lines_out.append(json.dumps(pulse.list_sinks(), indent=2))
    lines_out.append("")
    lines_out.append("--- recent logs (last 300 lines, all players) ---")
    for entry in await get_all_logs(lines=300):
        lines_out.append(f"[{entry['player']}] {entry['line']}")
    return PlainTextResponse("\n".join(lines_out), media_type="text/plain")


static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")
