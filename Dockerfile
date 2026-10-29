# PathRenamer without a window, for a server, a NAS or any Docker host.
# Ready-made image: ghcr.io/royalkiller2222/pathrenamer (see DOCKER.md).
# Build it yourself: docker build -t pathrenamer .
FROM python:3.13-slim

LABEL org.opencontainers.image.title="PathRenamer" \
      org.opencontainers.image.description="Renames movies, TV shows and anime for Plex, Jellyfin, Emby and Kodi" \
      org.opencontainers.image.source="https://github.com/Royalkiller2222/PathRenamer" \
      org.opencontainers.image.licenses="GPL-3.0-only"

# tzdata: log times in your time zone (TZ=Europe/Amsterdam)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATHRENAMER_CONFIG_DIR=/config \
    PATHRENAMER_DOCKER=1 \
    PUID=1000 \
    PGID=1000

WORKDIR /app
COPY src/requirements-cli.txt /app/
RUN pip install --no-cache-dir -r requirements-cli.txt
COPY src/*.py /app/
RUN printf '#!/bin/sh\nexec python /app/docker_start.py "$@"\n' > /usr/local/bin/pathrenamer \
    && chmod +x /usr/local/bin/pathrenamer

VOLUME /config
ENTRYPOINT ["pathrenamer"]
CMD ["watch"]
