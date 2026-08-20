"""Process supervisor for virtual snapclient players.

Each configured player is spawned as its own `snapclient` process bound to
one ALSA output device. The manager starts/stops/restarts them, tails their
output into an in-memory ring buffer (+ a log file per player), and
auto-restarts a player that crashes, with capped exponential backoff.
"""
import asyncio
import logging
import shlex
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from models import PlayerConfig

logger = logging.getLogger("multiroom.manager")

LOG_LINES_KEPT = 300
RESTART_BACKOFF_START = 2
RESTART_BACKOFF_MAX = 30


@dataclass
class PlayerRuntime:
    config: PlayerConfig
    process: Optional[asyncio.subprocess.Process] = None
    status: str = "stopped"  # stopped | starting | running | stopping | crashed | error
    pid: Optional[int] = None
    started_at: Optional[float] = None
    restart_count: int = 0
    backoff: int = RESTART_BACKOFF_START
    manual_stop: bool = True
    logs: deque = field(default_factory=lambda: deque(maxlen=LOG_LINES_KEPT))
    monitor_task: Optional[asyncio.Task] = None


class PlayerManager:
    def __init__(self, log_path: Path, default_host: str, default_port: int, backend: str = "alsa"):
        self.log_path = log_path
        self.default_host = default_host
        self.default_port = default_port
        self.backend = backend  # "alsa" or "pulse"
        self.players: dict[str, PlayerRuntime] = {}

    # -- registration -----------------------------------------------------
    def register(self, config: PlayerConfig) -> PlayerRuntime:
        rt = PlayerRuntime(config=config)
        self.players[config.name] = rt
        return rt

    def unregister(self, name: str) -> None:
        self.players.pop(name, None)

    # -- command building ---------------------------------------------------
    def build_command(self, config: PlayerConfig) -> list[str]:
        host = config.snapserver_host or self.default_host
        port = config.snapserver_port or self.default_port
        if not host:
            raise ValueError(
                "No Snapserver host configured - set SNAPSERVER_HOST or a per-player host"
            )
        if self.backend == "pulse":
            # PulseAudio backend: device selection is still via --soundcard
            # (there it names a sink instead of an hw:X,Y string);
            # fragments is an ALSA-only concept and doesn't apply here.
            player_opt = f"pulse:buffer_time={config.buffer_time_ms}"
        else:
            player_opt = f"alsa:buffer_time={config.buffer_time_ms},fragments={config.fragments}"
        cmd = [
            "snapclient",
            "-h", host,
            "-p", str(port),
            "--hostID", config.name,
            "--soundcard", config.device,
            "--player", player_opt,
            "--logsink", "stdout",
        ]
        if config.latency_ms:
            cmd += ["--latency", str(config.latency_ms)]
        if config.sampleformat:
            cmd += ["--sampleformat", config.sampleformat]
        if config.extra_args:
            cmd += shlex.split(config.extra_args)
        return cmd

    # -- lifecycle ------------------------------------------------------
    async def start(self, name: str) -> None:
        rt = self.players[name]
        if rt.status in ("running", "starting"):
            return
        rt.manual_stop = False
        rt.backoff = RESTART_BACKOFF_START
        await self._spawn(rt)

    async def _spawn(self, rt: PlayerRuntime) -> None:
        try:
            cmd = self.build_command(rt.config)
        except ValueError as exc:
            rt.status = "error"
            rt.logs.append(f"ERROR: {exc}")
            return

        logger.info("Starting player '%s': %s", rt.config.name, " ".join(cmd))
        rt.status = "starting"
        # Pass PULSE_SERVER so snapclient finds the PA socket in pulse backend mode.
        # os.environ already contains PULSE_SERVER (set in Dockerfile), but we
        # pass it explicitly so it survives any future env-stripping.
        import os as _os
        spawn_env = _os.environ.copy()
        if self.backend == "pulse":
            pulse_runtime = _os.environ.get("PULSE_RUNTIME_PATH", "/run/pulse")
            spawn_env.setdefault("PULSE_SERVER", f"unix:{pulse_runtime}/native")
        try:
            rt.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=spawn_env,
            )
        except FileNotFoundError:
            rt.status = "error"
            rt.logs.append("ERROR: snapclient binary not found in the container image")
            return

        rt.pid = rt.process.pid
        rt.started_at = time.time()
        rt.status = "running"
        rt.monitor_task = asyncio.create_task(self._monitor(rt))

    async def _monitor(self, rt: PlayerRuntime) -> None:
        proc = rt.process
        assert proc is not None
        log_file = self.log_path / f"{rt.config.name}.log"

        try:
            with open(log_file, "a", buffering=1) as fh:
                assert proc.stdout is not None
                async for raw_line in proc.stdout:
                    line = raw_line.decode(errors="replace").rstrip()
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    entry = f"[{ts}] {line}"
                    rt.logs.append(entry)
                    fh.write(entry + "\n")
        except Exception as exc:  # pragma: no cover - defensive logging only
            logger.warning("Log reader for '%s' stopped early: %s", rt.config.name, exc)

        returncode = await proc.wait()
        rt.pid = None

        if rt.manual_stop:
            rt.status = "stopped"
            rt.backoff = RESTART_BACKOFF_START
            return

        rt.status = "crashed"
        wait_s = rt.backoff
        rt.logs.append(
            f"--- snapclient exited (code {returncode}); restarting in {wait_s}s ---"
        )
        rt.backoff = min(rt.backoff * 2, RESTART_BACKOFF_MAX)
        await asyncio.sleep(wait_s)

        if rt.manual_stop or not rt.config.enabled:
            rt.status = "stopped"
            return

        rt.restart_count += 1
        await self._spawn(rt)

    async def stop(self, name: str) -> None:
        rt = self.players[name]
        rt.manual_stop = True
        if rt.process and rt.process.returncode is None:
            rt.status = "stopping"
            rt.process.terminate()
            try:
                await asyncio.wait_for(rt.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                rt.process.kill()
                await rt.process.wait()

        # The background _monitor task for this process is watching the same
        # process independently (draining logs, then awaiting its exit). If
        # we returned right away, a following start()/restart() could flip
        # manual_stop back to False and spawn a new process *before* that
        # old monitor task has observed the exit - it would then see
        # manual_stop == False, misread its own process's death as a crash,
        # and spawn a second, orphaned snapclient on top of the new one.
        # Waiting for it here (it's already exiting, so this is fast)
        # closes that race.
        if rt.monitor_task and not rt.monitor_task.done():
            try:
                await asyncio.wait_for(rt.monitor_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        rt.status = "stopped"
        rt.pid = None

    async def restart(self, name: str) -> None:
        await self.stop(name)
        rt = self.players[name]
        rt.manual_stop = False
        rt.backoff = RESTART_BACKOFF_START
        await self._spawn(rt)

    async def stop_all(self) -> None:
        await asyncio.gather(*(self.stop(n) for n in list(self.players)), return_exceptions=True)

    # -- status / logs ----------------------------------------------------
    def status(self, name: str) -> dict:
        rt = self.players[name]
        uptime = int(time.time() - rt.started_at) if rt.started_at and rt.status == "running" else 0
        return {
            "name": rt.config.name,
            "status": rt.status,
            "pid": rt.pid,
            "uptime_seconds": uptime,
            "restart_count": rt.restart_count,
            "config": rt.config.model_dump(),
        }

    def all_status(self) -> list[dict]:
        return [self.status(name) for name in self.players]

    def logs(self, name: str, lines: int = 100) -> list[str]:
        rt = self.players[name]
        entries = list(rt.logs)
        return entries[-lines:]
