# Profesia Job Monitor

Scrapes job offers from Profesia.sk (LinkedIn works locally), stores them in SQLite, sends new offers to Telegram, and adds an LLM verdict to each one: **GO**, **STRETCH**, **SKIP**, or **UNREACHABLE**.

## Features

- Multiple data source (Profesia.sk, LinkedIn), each with its own retry policy
- Dedup by `(source, id)`, so each offer is processed once
- Telegram alerts for multiple recipients, with verdicts edited into the same message later
- One monitor per database, enforced by a lease row

## Quick start

Requires Python 3.13. Run everything from the repo root.

```powershell
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements-dev.txt

# copy the examples, then edit them
copy data\configs\config.example.yaml data\configs\config.yaml
copy data\configs\profesia_sk.example.yaml data\configs\profesia_sk.yaml
copy data\configs\linkedin.example.yaml data\configs\linkedin.yaml

# one scrape, no Telegram, then exit
.venv\Scripts\python.exe -m offer_monitor --once --no-notify -v

# run forever
.venv\Scripts\python.exe -m offer_monitor -v
```

## Docker

```bash
docker compose up -d   # monitor + SQLite viewer at http://localhost:8080
```

> [!IMPORTANT]
> Stop the Docker `monitor` service before running `run-monitor.bat`. Only one instance can use a database.

> [!IMPORTANT]
> LinkedIn needs `uv` and a logged-in browser profile on the host. The image doesn't include `uv`, so LinkedIn doesn't run in Docker.

## Configuration

Settings live in `data/configs/config.yaml`. Relative paths resolve against `data/configs/`. Unknown keys are errors.

| Key | Default | Purpose |
| --- | --- | --- |
| `db` | `../jobs.db` | SQLite offer database |
| `backstory` | `../agent_context/backstory.md` | Your background, read by the reviewer |
| `interval` | `60` | Minutes between scrapes |
| `providers` | `[profesia]` | Sources to run: `profesia`, `linkedin` |
| `telegram.token` | `null` | Bot token from @BotFather; `null` disables Telegram |
| `model` | section present | Remove the section to run without verdicts |

Real configs hold tokens and API keys. They are gitignored, so don't commit them.

## LLM verdicts

Set `model.provider` to `anthropic`, `openai`, or `google`.

- `model.name: null` uses the provider default: `claude-sonnet-4-5`, `gpt-4o-mini`, or `gemini-2.5-flash`.
- The API key comes from `model.api_key`, or from `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GOOGLE_API_KEY`.
- Put your background in `data/agent_context/backstory.md`, and project notes in `data/agent_context/projects/*.md`.
- Keep `max_tokens` high. Thinking models spend part of it on reasoning.

If the key or backstory can't be loaded, verdicts turn off and the monitor keeps running.

## Adding a source

1. Create a package under `offer_monitor/providers/`.
2. Register it in `providers/__init__.py:SOURCES` and in the `SourceProviderKey` literal in `config.py`.
3. The key must equal the provider's `name`, because Telegram uses it to label messages.
4. Raise `SourceProviderError` subclasses from `providers/base.py` and set `retryable` / `retry_after` so retries behave correctly.
