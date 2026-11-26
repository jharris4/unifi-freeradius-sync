# The base image is pinned by digest so a rebuild picks up a known Debian
# snapshot rather than whatever the floating tag resolved to at the time.
# Note that apt-get and pip still resolve at build time, so two builds of
# the same commit are not byte-identical.
FROM python:3.13-slim@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY unifi_freeradius_sync.py ./
COPY sync.sh ./
RUN chmod +x sync.sh

CMD ["./sync.sh"]
