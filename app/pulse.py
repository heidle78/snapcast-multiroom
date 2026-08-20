"""PulseAudio orchestration: hardware sink sync + custom (combine/remap) sinks.

Only relevant when Settings.backend == "pulse". Talks to a local PulseAudio
daemon running inside the container via `pactl`. We deliberately use the
plain-text `pactl list short ...` output (not `-f json`) since that has been
stable across PulseAudio versions for decades, whereas JSON output support
is comparatively recent and not guaranteed to be present in every distro's
pulseaudio-utils package.

Design notes:
- Hardware sinks are named deterministically (hw_<card>_<device>, see
  devices.sanitize_sink_name) and (re)created on every startup by
  sync_hardware_sinks() from whatever ALSA devices are currently detected.
- Custom sinks (combine/remap) are user-defined and persisted to
  sinks.yaml via config_store.ConfigStore; module IDs are NOT persisted
  (they change every time PulseAudio restarts) - instead we look them up
  live by matching `sink_name=<name>` in `pactl list short modules`, and
  replay creation from the persisted definitions on startup.
"""
import logging
import re
import subprocess
import time

from devices import list_alsa_devices, sanitize_sink_name
from models import CustomSinkConfig

logger = logging.getLogger("multiroom.pulse")

PACTL_TIMEOUT = 5


class PulseError(Exception):
    pass


# PulseAudio --system mode uses a fixed socket path. When running as root
# inside the container pactl can't auto-discover it via the session bus, so
# we point it there explicitly. PULSE_RUNTIME_PATH is set in the Dockerfile
# and start_pulseaudio() starts the daemon with the same path, so all three
# (daemon, pactl, snapclient) agree on the socket location.
import os as _os
_PA_SERVER = f"unix:{_os.environ.get('PULSE_RUNTIME_PATH', '/run/pulse')}/native"


def _run(args: list[str], timeout: int = PACTL_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a pactl/pulseaudio command, never raising.

    Every read path (list_sinks, sync_hardware_sinks, ...) ultimately calls
    this, and several of those are reachable from GET endpoints
    (/api/devices, /api/sinks) that must keep working even when
    backend=pulse is configured but PulseAudio isn't actually up yet (e.g.
    it failed to start, or hasn't started on this particular boot). Letting
    FileNotFoundError/TimeoutExpired propagate here previously turned that
    into a 500 on the whole dashboard instead of an empty sink list, so we
    fold both into a synthetic failed CompletedProcess here once instead of
    requiring every caller to guard against it individually.
    """
    # Inject --server for every pactl call so root inside the container can
    # reach the PulseAudio --system daemon regardless of session-bus state.
    if args and args[0] == "pactl":
        args = ["pactl", "--server", _PA_SERVER] + args[1:]
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, returncode=127, stdout="", stderr=f"{args[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, returncode=124, stdout="", stderr=f"{args[0]}: timed out")


def pulse_available() -> bool:
    return _run(["pactl", "info"]).returncode == 0


def start_pulseaudio(log_path=None, wait_s: int = 10) -> bool:
    """Start (or confirm) a local PulseAudio daemon. Idempotent.

    Runs in --system mode. The container has no USER directive (matches the
    existing alsa backend, which already runs snapclient as root to reach
    /dev/snd without fighting host/container audio-group GID mismatches), so
    PulseAudio would otherwise refuse to start: "This program is not
    intended to be run as root (unless --system is specified)". --system is
    exactly the mode meant for this - a single-tenant, headless daemon,
    not a multi-user desktop session - and it sidesteps that GID problem
    entirely since a root process can already read/write the bind-mounted
    ALSA device nodes.
    """
    if pulse_available():
        return True

    logsink = f"--log-target=file:{log_path}/pulseaudio.log" if log_path else "--log-target=stderr"
    runtime_path = _os.environ.get("PULSE_RUNTIME_PATH", "/run/pulse")
    try:
        subprocess.run(
            [
                "pulseaudio",
                "--daemonize=yes",
                "--exit-idle-time=-1",
                "--disallow-module-loading=0",
                # Do NOT use --system: system mode restricts socket access to
                # the pulse-access group, so root cannot connect via pactl.
                # Instead run as a regular (root) daemon with an explicit
                # runtime dir — pactl finds it via _PA_SERVER which points to
                # the same path, giving us full access without group juggling.
                f"--runtime-path={runtime_path}",
                logsink,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError:
        logger.error("pulseaudio binary not found in image")
        return False

    for _ in range(wait_s * 2):
        if pulse_available():
            return True
        time.sleep(0.5)
    logger.error("PulseAudio did not become available within %ss", wait_s)
    return False


# -- sink discovery -------------------------------------------------------

def _list_short_sinks() -> list[dict]:
    """Parse `pactl list short sinks`: index<TAB>name<TAB>driver<TAB>spec<TAB>state."""
    result = _run(["pactl", "list", "short", "sinks"])
    sinks = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            sinks.append({"index": parts[0], "name": parts[1]})
    return sinks


def _list_short_modules() -> list[dict]:
    """Parse `pactl list short modules`: index<TAB>name<TAB>argument."""
    result = _run(["pactl", "list", "short", "modules"])
    modules = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            modules.append(
                {
                    "index": parts[0],
                    "name": parts[1],
                    "argument": parts[2] if len(parts) > 2 else "",
                }
            )
    return modules


def _module_id_for_sink(sink_name: str, module_name: str, modules: list[dict] | None = None) -> str | None:
    """Find the module index that owns a given sink_name, by scanning module arguments."""
    modules = modules if modules is not None else _list_short_modules()
    needle = f"sink_name={sink_name}"
    for m in modules:
        if m["name"] == module_name and needle in m["argument"]:
            return m["index"]
    return None


def _sink_descriptions() -> dict[str, str]:
    """Best-effort sink_name -> Description lookup from `pactl list sinks` (long form)."""
    result = _run(["pactl", "list", "sinks"])
    desc_map: dict[str, str] = {}
    current_name = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Name:"):
            current_name = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Description:") and current_name:
            desc_map[current_name] = stripped.split(":", 1)[1].strip()
            current_name = None
    return desc_map


def list_sinks() -> list[dict]:
    """All currently loaded sinks, tagged with a kind: hardware | combine | remap | other."""
    short_sinks = _list_short_sinks()
    modules = _list_short_modules()
    descriptions = _sink_descriptions()

    hw_prefix = "hw_"
    combine_names = {
        re.search(r"sink_name=(\S+)", m["argument"]).group(1)
        for m in modules
        if m["name"] == "module-combine-sink" and "sink_name=" in m["argument"]
    }
    remap_names = {
        re.search(r"sink_name=(\S+)", m["argument"]).group(1)
        for m in modules
        if m["name"] == "module-remap-sink" and "sink_name=" in m["argument"]
    }

    out = []
    for s in short_sinks:
        name = s["name"]
        if name in combine_names:
            kind = "combine"
        elif name in remap_names:
            kind = "remap"
        elif name.startswith(hw_prefix):
            kind = "hardware"
        else:
            kind = "other"
        out.append(
            {
                "name": name,
                "kind": kind,
                "description": descriptions.get(name, name),
            }
        )
    return out


# -- hardware sink sync -----------------------------------------------------

def sync_hardware_sinks() -> None:
    """Ensure every detected ALSA playback device has a matching PulseAudio sink."""
    if not pulse_available():
        logger.warning("sync_hardware_sinks: PulseAudio not available, skipping")
        return

    existing = {s["name"] for s in _list_short_sinks()}
    for dev in list_alsa_devices():
        if dev.get("card") is None:  # skip the generic "default" fallback entry
            continue
        sink_name = sanitize_sink_name(dev["card"], dev["device"])
        if sink_name in existing:
            continue
        result = _run(
            [
                "pactl",
                "load-module",
                "module-alsa-sink",
                f"device={dev['hw']}",
                f"sink_name={sink_name}",
                f"sink_properties=device.description={_pulse_prop_safe(dev['label'])}",
            ]
        )
        if result.returncode != 0:
            logger.warning("Failed to create hardware sink %s: %s", sink_name, result.stderr.strip())
        else:
            logger.info("Created PulseAudio hardware sink %s (%s)", sink_name, dev["hw"])


# -- custom sinks -------------------------------------------------------

NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

# `pactl` joins argv[3:] with plain spaces into a single module-argument
# string for the daemon (no shell involved), and that string is then
# re-tokenized on whitespace. Any whitespace or quote character inside a
# sink_properties value - e.g. device.description='Living Room' - breaks
# that re-tokenization and module load fails with a generic "Module
# initialization failed" (confirmed live: identical call with no spaces in
# the description succeeds, with spaces it doesn't, regardless of how the
# value is quoted). So we sanitize any property value we embed in a
# load-module call down to something whitespace/quote-free; the
# human-readable description the user typed is preserved separately in
# sinks.yaml (see config_store.save_sinks) and used for display.
_PROP_UNSAFE_RE = re.compile(r"[\s'\"]+")


def _pulse_prop_safe(text: str) -> str:
    return _PROP_UNSAFE_RE.sub("_", text).strip("_")


def _validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise PulseError("Sink name may only contain letters, numbers and underscores")


def create_sink(cfg: CustomSinkConfig) -> None:
    _validate_name(cfg.name)
    if not pulse_available():
        raise PulseError("PulseAudio is not running - enable it in System Settings first")

    existing = {s["name"] for s in _list_short_sinks()}
    if cfg.name in existing:
        raise PulseError(f"A sink named '{cfg.name}' already exists")

    if cfg.kind == "combine":
        if len(cfg.slaves) < 2:
            raise PulseError("Combine sinks need at least 2 source sinks")
        args = [
            "pactl", "load-module", "module-combine-sink",
            f"sink_name={cfg.name}",
            f"slaves={','.join(cfg.slaves)}",
        ]
        if cfg.description:
            args.append(f"sink_properties=device.description={_pulse_prop_safe(cfg.description)}")
    elif cfg.kind == "remap":
        if not cfg.master or not cfg.channels:
            raise PulseError("Remap sinks need a master sink and a channel count")
        args = [
            "pactl", "load-module", "module-remap-sink",
            f"sink_name={cfg.name}",
            f"master={cfg.master}",
            f"channels={cfg.channels}",
        ]
        if cfg.channel_map:
            args.append(f"channel_map={cfg.channel_map}")
        if cfg.master_channel_map:
            args.append(f"master_channel_map={cfg.master_channel_map}")
        if cfg.description:
            args.append(f"sink_properties=device.description={_pulse_prop_safe(cfg.description)}")
    else:
        raise PulseError(f"Unknown sink kind '{cfg.kind}' (expected 'combine' or 'remap')")

    result = _run(args)
    if result.returncode != 0 or not result.stdout.strip():
        raise PulseError(result.stderr.strip() or "pactl load-module failed")


def remove_sink(name: str) -> None:
    module_name = None
    module_id = None
    for kind_module in ("module-combine-sink", "module-remap-sink"):
        module_id = _module_id_for_sink(name, kind_module)
        if module_id is not None:
            module_name = kind_module
            break
    if module_id is None:
        raise PulseError(f"No custom sink module found for '{name}' (already removed?)")
    result = _run(["pactl", "unload-module", module_id])
    if result.returncode != 0:
        raise PulseError(result.stderr.strip() or f"Failed to unload {module_name} for '{name}'")


def replay_custom_sinks(sinks: list[CustomSinkConfig]) -> None:
    """Recreate persisted custom sinks after a PulseAudio (re)start."""
    existing = {s["name"] for s in _list_short_sinks()}
    for cfg in sinks:
        if cfg.name in existing:
            continue
        try:
            create_sink(cfg)
        except PulseError as exc:
            logger.warning("Could not replay custom sink '%s': %s", cfg.name, exc)
