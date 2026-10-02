# syntax=docker/dockerfile:1
#
# The bot image. Secrets are NOT baked in: settings come from the environment
# (app.env on the server, see deploy/README.md). Dependencies are installed from
# requirements.lock so every build is the same build.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg and ffprobe are run as subprocesses to read voice notes and videos
# (app/pipeline/normalize.py); the bot cannot handle audio without them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 infobot
WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.lock

COPY app ./app

USER infobot
EXPOSE 8000

# No curl in the slim image; Python is already here.
HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

# One process: rate-limit cooldowns and the Gemini key state live in memory, and
# replies are sent from background tasks. On a stop, uvicorn waits up to 40 s for
# in-flight requests (and the tasks attached to them) before exiting, so a deploy
# does not cut a reply off half way.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "40"]
