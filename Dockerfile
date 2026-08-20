FROM python:3.12-alpine

# snapcast-client: Alpine's community repo ships prebuilt snapclient
# packages (built with -DBUILD_WITH_PULSE=ON, so its "pulse" player backend
# works out of the box), so we don't have to chase GitHub release asset
# names/versions.
# alsa-utils: needed for `aplay -l` device discovery.
# alsa-lib: ALSA runtime libs required by snapclient's alsa backend.
# pulseaudio + pulseaudio-utils: only used when Settings.backend == "pulse"
# (Custom Sinks / combine & remap). pulseaudio bundles module-alsa-sink so
# it can still reach real hardware; pulseaudio-utils provides `pactl`. Both
# are lightweight enough to always install rather than building two images.
RUN apk add --no-cache \
        snapcast-client \
        alsa-utils \
        alsa-lib \
        pulseaudio \
        pulseaudio-utils \
        tzdata \
        curl

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/

ENV CONFIG_PATH=/app/config \
    LOG_PATH=/app/logs \
    WEB_PORT=8098 \
    LOG_LEVEL=info \
    PULSE_RUNTIME_PATH=/run/pulse

# Pin the PulseAudio socket/cookie location explicitly via
# PULSE_RUNTIME_PATH above, inherited by every process in the container
# (the FastAPI app, its pactl calls, and snapclient's own libpulse-based
# "pulse" player backend). Alpine's build-time default for this path isn't
# guaranteed, and --system mode has no per-user session to autodiscover -
# pinning it here means server and clients always agree on where to find
# each other without any per-call plumbing.
RUN mkdir -p /run/pulse

VOLUME ["/app/config", "/app/logs"]
EXPOSE 8098

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:${WEB_PORT}/api/health || exit 1

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${WEB_PORT}"]
