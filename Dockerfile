# Stage 1: builder
FROM python:3.12-slim AS builder

WORKDIR /build
COPY server/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: runtime
FROM python:3.12-slim

WORKDIR /app

# Install language runtimes
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jdk \
    g++ \
    golang-go \
    nodejs \
    ruby \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy engine
COPY ants/ /app/ants/
COPY worker/ /app/worker/

# Copy server
COPY server/app.py server/worker.py server/db.py server/languages.py server/glicko2.py /app/server/
COPY server/templates/ /app/server/templates/

# Copy visualizer assets into static dir
RUN mkdir -p /app/server/static/js /app/server/static/data
COPY ants/visualizer/js/ /app/server/static/js/
COPY ants/visualizer/data/ /app/server/static/data/

# Seed sample bots
RUN mkdir -p /app/server/bots /app/server/replays /app/server/data && \
    for bot in HunterBot LeftyBot GreedyBot RandomBot; do \
        mkdir -p /app/server/bots/$bot && \
        cp /app/ants/dist/sample_bots/python/$bot.py /app/server/bots/$bot/MyBot.py && \
        cp /app/ants/dist/sample_bots/python/ants.py /app/server/bots/$bot/ants.py; \
    done && \
    cp /app/ants/dist/sample_bots/python/logutils.py /app/server/bots/GreedyBot/logutils.py

VOLUME ["/app/server/bots", "/app/server/replays", "/app/server/data"]
EXPOSE 5000

RUN useradd -r -s /bin/false appuser && \
    chown -R appuser:appuser /app/server/bots /app/server/replays /app/server/data /app/server
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

WORKDIR /app/server
