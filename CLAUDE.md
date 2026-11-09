# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Run everything from the repo root. The config path is relative to the working directory (`data/configs/config.yaml`, see `config.py`), so running from elsewhere fails with "not found".

```
# setup (Windows; the repo uses .venv)
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements-dev.txt

# one scrape, no Telegram, then exit (safe for testing; still writes to data/jobs.db)
.venv\Scripts\python.exe -m offer_monitor --once --no-notify -v

# run forever (what run-monitor.bat does; -v shows verdict log lines)
.venv\Scripts\python.exe -m offer_monitor -v

# Docker: monitor + SQLite web viewer on http://localhost:8080
docker compose up -d
```

- Tests: `requirements-dev.txt` adds pytest and `.vscode/settings.json` points it at `tests/`, but there is currently no `tests/` directory in the working tree or in HEAD. Ask before assuming a test suite exists; if one is restored, run a single file with `pytest tests/test_x.py -q`.
- Formatting: `pyproject.toml` configures black with `line-length = 120`. There is no ruff or lint config in the repo; ruff and vulture have been run ad hoc via `uvx`.
- Python 3.13 (see `Dockerfile`).

## Running it locally vs. in Docker

- Only one monitor may use a given database. It takes a lease row in `monitor_lease` (`store/lease.py`), so a second instance exits with "another monitor ... is using ...". Stop the Docker `monitor` service before using `run-monitor.bat`. The bat file checks for this.
- `data/` is shared by both: it holds `configs/`, `jobs.db`, and `agent_context/` (`backstory.md`, `projects/*.md`). It is gitignored, except `data/configs/*.example.yaml`. Real configs contain Telegram tokens and API keys. Do not commit them.
- LinkedIn is local-only in practice: it needs `uv` on the host and a logged-in browser profile (the MCP server drives it). The Docker image does not install `uv`.

## Architecture

The package is `offer_monitor/`. Everything hangs off one loop in `__main__.py`; the modules below it are mostly libraries it calls.

**Cycle (`__main__.run`).** Each `interval` minutes (default 60, start-to-start):
1. Fetch every configured source. `fetch_source` retries per the provider's `RetryPolicy`. A non-retryable error or exhausted retries logs and skips that source, and the others still run.
2. Dedup and store in one transaction. `provider.dedup_fetched` removes in-run repeats, `provider.db_dedup` removes offers already in the DB (matched by `(source, id)`), `refresh_known` updates `last_seen` on the rest, then `store_new` inserts. Only the inserted offers are "new".
3. Notify Telegram subscribers (`telegram.notify`). Message ids are saved in `offer_messages` so verdicts can edit the same messages later.
4. Submit each new offer to `VERDICTS`, a 2-worker thread pool. `on_verdict` runs in the worker thread, saves the verdict, and edits the Telegram messages. It opens its own DB connection.

Between cycles the main loop long-polls Telegram for `/start` and `/stop` (`sync_subscribers`). Without Telegram it sleeps. `--once` skips the loop and waits for pending verdicts before exit.

**Sources (`providers/`).** A new source is a package plus an entry in `providers/__init__.py:SOURCES` and in the `SourceProviderKey` literal in `config.py`. The key must equal the provider's `name`, because Telegram looks up `job.source` to label messages. Each provider reads its own YAML from `data/configs/` (`profesia_sk.yaml`, `linkedin.yaml`) through its `config.py` module. Errors come from the `SourceProviderError` tree in `providers/base.py`; the `retryable` flag and `retry_after` decide what `fetch_source` does. LinkedIn drives an MCP stdio server (`uvx mcp-server-linkedin@latest`) through an asyncio client. Profesia is plain `requests` plus BeautifulSoup.

**Store (`store/`).** SQLite via the stdlib. `db.py` holds the full schema and `connect()`, which runs on every open, creates missing tables, and applies migrations. Migrations are guarded by column checks and must stay idempotent. Other store modules only query. Offers are keyed by `uuid` and unique on `(source, id)`. Subscribers and the Telegram update offset live in the same DB. The old `subscribers.json` is imported once on first load.

**Verdict pipeline (`agents/`).** LangChain 1.x `create_agent` with structured output. The orchestrator (`orchestrator.py`) receives an offer URL and calls the `offer_reviewer` subagent (`offer_reviewer.py`) as a tool. The reviewer uses `fetch_offer` (`tools.py`, which fetches a page and returns the first 20k characters of text) and returns a pydantic `Verdict` (`GO` / `STRETCH` / `SKIP` / `UNREACHABLE`). The system prompts are Markdown files in `agents/prompts/`. The candidate backstory is appended to the reviewer prompt at startup. `build_review` in `__main__.py` reads it from `data/agent_context/backstory.md` plus `projects/*.md`. If the `model` config section is missing, or the API key or backstory can't be loaded, verdicts are turned off and the monitor keeps running. `agents/llm.py` maps `provider` to a LangChain provider id and default model. The `AGENT` ContextVar sets the `[agent]` tag on log lines.

**Config (`config.py`, `settings.py`).** Pydantic models with `extra="forbid"`, so unknown keys are errors. Relative paths in `config.yaml` (`db`, `backstory`, `telegram.subscribers`) resolve against the config folder. A `ConfigError` becomes `parser.error` (exit 2).

**Concurrency model.** The main thread runs the cycle. Verdicts run in the pool. The lease is renewed by a daemon thread every 30 s. If the lease is lost, the monitor exits with code 1. SIGTERM is mapped to Ctrl+C so `docker stop` releases the lease.

## Docs and status

- `docs/pipeline-report.md` is a review of the pipeline as it was at commit `08bf858`.
- `docs/pipeline-changes.md` is a plan for P0 to P3 changes, including proposed `pending` / `review_attempts` columns and a DB-driven pipeline. Those changes are not in the code yet. Check `store/db.py` before assuming they exist.
