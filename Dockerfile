FROM python:3.12-alpine

# snapcast-client: Alpine's community repo ships prebuilt snapclient
# packages, so we don't have to chase GitHub release asset names/versions.
# alsa-utils: needed for `aplay -l` device discovery.
# alsa-lib: ALSA runtime libs required by snapclient's alsa backend.
RUN apk add --no-cache \
        snapcast-client \
        alsa-utils \
        alsa-lib \
        tzdata \
        curl

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/

ENV CONFIG_PATH=/app/config \
    LOG_PATH=/app/logs \
    WEB_PORT=8098 \
    LOG_LEVEL=info

VOLUME ["/app/config", "/app/logs"]
EXPOSE 8098

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:${WEB_PORT}/api/health || exit 1

CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${WEB_PORT}"]
