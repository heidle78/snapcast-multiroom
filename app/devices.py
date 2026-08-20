"""ALSA playback device discovery via `aplay -l`."""
import logging
import re
import subprocess

logger = logging.getLogger("multiroom.devices")

# The "id" token aplay prints before each bracketed name (e.g. "PCH" in
# "card 0: PCH [HDA Intel PCH]") is conventionally a single word, but on a
# lot of real hardware it isn't (e.g. "card 0: Generic [HD-Audio Generic],
# device 0: ALC1220 Analog [ALC1220 Analog]" - "ALC1220 Analog" has a
# space). A previous version of this regex required that id to be exactly
# one \S+ token, which made it silently fail to match - and therefore
# silently drop the device from discovery - on any hardware like that. We
# don't use card_id/device_id for anything (only the card/device numbers
# and the bracketed *_name), so match everything up to the last bracket on
# each side greedily instead of trying to isolate that token.
CARD_RE = re.compile(
    r"^card (?P<card>\d+): .*\[(?P<card_name>[^\]]*)\], "
    r"device (?P<device>\d+): .*\[(?P<device_name>[^\]]*)\]$"
)


def list_alsa_devices() -> list[dict]:
    """Return detected ALSA playback devices, plus a couple of fallback entries.

    Each entry contains an `id` (the recommended value to use as --soundcard,
    using the safer `plughw` variant which handles sample-rate/format
    conversion), the raw `hw` device string, and a human readable `label`.
    """
    devices: list[dict] = []
    text = ""
    try:
        result = subprocess.run(
            ["aplay", "-l"], capture_output=True, text=True, timeout=5, check=False
        )
        text = result.stdout
    except FileNotFoundError:
        logger.warning("aplay not found - is alsa-utils installed in the image?")
    except subprocess.TimeoutExpired:
        logger.warning("aplay -l timed out")

    for line in text.splitlines():
        match = CARD_RE.match(line.strip())
        if not match:
            continue
        card = match.group("card")
        device = match.group("device")
        hw = f"hw:{card},{device}"
        plughw = f"plughw:{card},{device}"
        label = f"{match.group('card_name')} - {match.group('device_name')} [{hw}]"
        devices.append(
            {
                "id": plughw,
                "hw": hw,
                "plughw": plughw,
                "card": card,
                "device": device,
                "label": label,
            }
        )

    # Always offer generic fallbacks: the system ALSA default, and anything
    # defined in a custom /etc/asound.conf (dmix/softvol/etc.) that aplay -l
    # won't enumerate but snapclient can still open by name.
    devices.append(
        {
            "id": "default",
            "hw": "default",
            "plughw": "default",
            "card": None,
            "device": None,
            "label": "default (system default ALSA device)",
        }
    )
    return devices


def sanitize_sink_name(card: str, device: str) -> str:
    """Deterministic PulseAudio sink name for a given ALSA hw:card,device."""
    return f"hw_{card}_{device}"


def list_output_targets(backend: str) -> list[dict]:
    """Unified device list for the Add Player dropdown.

    - backend == "alsa": same as list_alsa_devices() (raw hw devices).
    - backend == "pulse": PulseAudio sinks - hardware-backed ones (kept in
      sync with the detected ALSA devices) plus any custom combine/remap
      sinks the user created. Delegates to pulse.py to avoid a circular
      import (pulse.py itself calls list_alsa_devices() from here).
    """
    if backend == "pulse":
        from pulse import list_sinks  # local import: avoids import cycle

        return [
            {
                "id": s["name"],
                "hw": s["name"],
                "plughw": s["name"],
                "card": None,
                "device": None,
                "label": f"{s['description']} [{s['kind']}]" if s.get("description") else s["name"],
            }
            for s in list_sinks()
        ]
    return list_alsa_devices()
