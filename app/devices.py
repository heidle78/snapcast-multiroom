"""ALSA playback device discovery via `aplay -l`."""
import logging
import re
import subprocess

logger = logging.getLogger("multiroom.devices")

CARD_RE = re.compile(
    r"^card (?P<card>\d+): (?P<card_id>\S+) \[(?P<card_name>.*?)\], "
    r"device (?P<device>\d+): (?P<device_id>\S+) \[(?P<device_name>.*?)\]$"
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
