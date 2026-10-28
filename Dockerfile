# Profesia job monitor: scraper, Telegram bot and verdict agents
FROM python:3.13-slim

# show prints and logs immediately in `docker compose logs`
ENV PYTHONUNBUFFERED=1

# dependencies first, so code edits don't reinstall them
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# application code, including agents/prompts
COPY profesia_monitor/ profesia_monitor/

# config.json, jobs.db, subscribers.json and backstory.md live in the mounted volume
VOLUME /data
CMD ["python", "-m", "profesia_monitor", "-c", "/data/config.json", "-v"]
