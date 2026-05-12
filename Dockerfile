FROM python:3.10-slim

WORKDIR /app

# Install language runtimes
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jdk \
    g++ \
    golang-go \
    nodejs \
    ruby \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn[standard] python-multipart jinja2 psycopg2-binary

# Copy engine
COPY ants/ /app/ants/
COPY worker/ /app/worker/

# Copy server
COPY server/app.py server/worker.py server/db.py server/languages.py /app/server/
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

WORKDIR /app/server
