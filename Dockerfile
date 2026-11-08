# Profesia job monitor: scraper, Telegram bot and verdict agents
FROM python:3.13-slim

# show prints and logs immediately in `docker compose logs`
ENV PYTHONUNBUFFERED=1

# dependencies first, so code edits don't reinstall them
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# application code, including agents/prompts
COPY offer_monitor/ offer_monitor/

# configs/ (config.yaml and the provider files), jobs.db (offers and Telegram subscribers) and agent_context/ (backstory.md, projects/)
# live in the mounted volume at /app/data, the data/ folder next to the code where the app looks for its config
VOLUME /app/data
CMD ["python", "-m", "offer_monitor", "-v"]
