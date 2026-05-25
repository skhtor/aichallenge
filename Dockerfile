# Stage 1: builder
FROM python:3.12-slim AS builder

WORKDIR /build
COPY server/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: runtime
FROM python:3.12-slim

WORKDIR /app

# Install language runtimes (headless JDK, no docs/man pages)
RUN echo 'path-exclude /usr/share/doc/*\npath-exclude /usr/share/man/*\npath-exclude /usr/share/locale/*' > /etc/dpkg/dpkg.cfg.d/excludes && \
    apt-get update && apt-get install -y --no-install-recommends \
    default-jdk-headless \
    g++ \
    golang-go \
    nodejs \
    ruby \
    mono-mcs \
    && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man /usr/share/locale

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy engine
COPY ants/ /app/ants/

# Copy server
COPY server/app.py server/worker.py server/db.py server/languages.py server/glicko2.py /app/server/
COPY server/templates/ /app/server/templates/

# Copy static assets
RUN mkdir -p /app/server/static/js /app/server/static/data
COPY ants/visualizer/js/ /app/server/static/js/
COPY ants/visualizer/data/ /app/server/static/data/
COPY server/static/ /app/server/static/

# Stage sample bots in a non-volume path (copied at startup if SEED_SAMPLE_BOTS=true)
RUN mkdir -p /app/sample_bots /app/server/bots /app/server/replays /app/server/data && \
    for bot in HunterBot LeftyBot GreedyBot RandomBot; do \
        mkdir -p /app/sample_bots/$bot && \
        cp /app/ants/dist/sample_bots/python/$bot.py /app/sample_bots/$bot/MyBot.py && \
        cp /app/ants/dist/sample_bots/python/ants.py /app/sample_bots/$bot/ants.py; \
    done && \
    cp /app/ants/dist/sample_bots/python/logutils.py /app/sample_bots/GreedyBot/logutils.py

EXPOSE 5000

RUN useradd -r -s /bin/false -m appuser && \
    chown -R appuser:appuser /app/server/bots /app/server/replays /app/server/data /app/server
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

WORKDIR /app/server
