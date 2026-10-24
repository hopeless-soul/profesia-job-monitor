# Profesia Job Monitor

A lightweight CLI daemon that watches [Profesia.sk](https://www.profesia.sk) for new job offers matching your searches, keeps a deduplicated JSON archive, and pushes new listings to Telegram subscribers in real time.

No browser required — listing pages are plain server-rendered HTML, so the scraper runs on `requests` + BeautifulSoup with minimal overhead.

## Features

- **Multi-search monitoring** — run several keyword + filter combinations in parallel, all merged into one store
- **Structured salary parsing** — extracts min/max, currency (EUR/CZK), and period (monthly/hourly) from Slovak-format salary strings like `Od 3 900 EUR/mesiac`
- **Telegram notifications** — new offers are batched and pushed to subscribers via a Telegram bot; users self-manage with `/start` and `/stop`
- **Atomic persistence** — writes go through `tempfile` → `os.replace`, so a crash never leaves a truncated `jobs.json`
- **Offer deduplication** — unique by Profesia offer ID, with `first_seen` / `last_seen` tracking across scrapes
- **Rich filtering** — region, remote work mode, minimum salary, recency, position type, language, and more via path segments and query params
- **Schema-validated config** — JSON Schema for both `config.json` and `jobs.json`, with editor autocompletion in VS Code
- **One-off searches** — run a quick search from the CLI without touching the config file
- **Offline tests** — test suite runs against saved HTML fixtures, no network required

## Requirements

- Python 3.10+
- [`requests`](https://pypi.org/project/requests/) ≥ 2.31
- [`beautifulsoup4`](https://pypi.org/project/beautifulsoup4/) ≥ 4.12

## Setup

```bash
git clone https://github.com/hopeless-soul/profesia-job-monitor.git
cd profesia-job-monitor
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp config.example.json config.json
```

Edit `config.json` with your searches — the schema provides autocompletion in VS Code.

## Usage

### Continuous monitoring (default)

```bash
python -m profesia_monitor
```

Scrapes immediately, then every `interval` minutes (default: 60). Prints a `NEW ...` line for each offer it hasn't seen before. Ctrl+C to stop.

### One-off scrape

```bash
python -m profesia_monitor --once
```

### Quick search without config

```bash
python -m profesia_monitor "c#" --segment bratislavsky-kraj --remote 2 --min-salary 2000 --once
```

### CLI options

| Option | Description |
|---|---|
| `--once` | Scrape once and exit |
| `--interval N` | Minutes between scrapes (default: 60) |
| `-c FILE` | Use a different config file |
| `--days N` | Only offers posted in the last N days |
| `--pages N` | Max listing pages per search (20 offers/page) |
| `--out FILE` | Where to store jobs |
| `--no-notify` | Skip Telegram notifications |
| `--log FILE` | Append output to a file instead of console |
| `-v` | Verbose logging |

### Background mode (Windows)

```powershell
Start-Process .venv\Scripts\pythonw.exe `
  -ArgumentList "-m profesia_monitor --log monitor.log" `
  -WorkingDirectory $PWD
```

## Configuration

`config.json` is read from the working directory. It's git-ignored because it holds your bot token.

```json
{
  "$schema": "./schemas/config.schema.json",
  "out": "jobs.json",
  "pages": 3,
  "delay": 1.5,
  "interval": 60,
  "count_days": 7,
  "searches": [
    {
      "keyword": "c#",
      "segments": ["bratislavsky-kraj"],
      "remote_work": 2,
      "min_salary": 2000
    },
    {
      "keyword": "python",
      "segments": ["informacne-technologie", "plny-uvazok"]
    }
  ],
  "telegram": {
    "token": "123456:ABC-your-bot-token",
    "subscribers": "subscribers.json"
  }
}
```

### Search filters

Each search supports these filter options:

| Key | Values | Description |
|---|---|---|
| `keyword` | free text | Search across offer text (required) |
| `segments` | path slugs | Region, position, employment type, work area, language |
| `remote_work` | `0` / `1` / `2` / `null` | On-site only / remote only / partially remote / any |
| `min_salary` | integer | Minimum salary in EUR |
| `salary_period` | `"m"` / `"h"` | Monthly or hourly (used with `min_salary`) |
| `count_days` | integer | Only offers from the last N days |

Common path segments:

| Filter | Examples |
|---|---|
| Region | `bratislavsky-kraj`, `zilinsky-kraj`, `kosicky-kraj`, `zahranicie` |
| Position | `programator-programatorka`, `dotnet-programator-programatorka` |
| Employment | `plny-uvazok`, `skrateny-uvazok`, `na-dohodu-brigady`, `zivnost` |
| Work area | `informacne-technologie`, `elektrotechnika-a-energetika` |
| Language | `anglicky-jazyk`, `nemecky-jazyk` |

All results from all searches merge into one file. Duplicate offers (same ID) are stored once.

## Telegram notifications

1. Create a bot with [@BotFather](https://t.me/BotFather) and add the token to `config.json` (or set the `TELEGRAM_BOT_TOKEN` environment variable).
2. Start the monitor.
3. Users send `/start` to the bot to subscribe, `/stop` to unsubscribe.

New offers arrive as formatted messages with title (linked), company, location, and salary. Messages are packed to fit Telegram's 4096-char limit. Subscribers who block the bot are removed automatically.

## Output format

`jobs.json` stores deduplicated offers sorted by `first_seen` (newest first):

```json
{
  "schema_version": 1,
  "updated_at": "2026-09-23T10:00:00+00:00",
  "jobs": [
    {
      "id": 4812345,
      "title": "Frontend Developer (Vue.js)",
      "url": "https://www.profesia.sk/praca/.../O4812345",
      "company": "ACME s.r.o.",
      "company_id": 12345,
      "location": "Bratislava",
      "salary": {
        "raw": "Od 2 500 EUR/mesiac",
        "min": 2500,
        "max": null,
        "currency": "EUR",
        "period": "month"
      },
      "labels": [],
      "posted_text": "Nová",
      "first_seen": "2026-09-23T10:00:00+00:00",
      "last_seen": "2026-09-23T10:00:00+00:00"
    }
  ]
}
```

Both `config.json` and `jobs.json` have JSON Schema definitions in `schemas/` for validation and editor support.

## Architecture

```
profesia_monitor/
├── __main__.py     CLI entry point, argument parsing, main loop
├── config.py       Config loading and validation
├── scraper.py      HTTP fetching with Query builder and pagination
├── parser.py       Pure HTML parsing (BeautifulSoup, no network)
├── models.py       Frozen dataclasses: Job, Salary
├── store.py        JSON persistence with atomic writes and merge logic
└── telegram.py     Bot API client, subscriber management, message formatting
```

The pipeline is deliberately simple: **scrape → parse → merge → persist → notify**. Each module has a single responsibility and no cross-dependencies beyond the shared `Job` model.

Key design decisions:

- **No browser engine.** Profesia listing pages are server-rendered; `requests` is sufficient and keeps the footprint minimal.
- **Offer ID as primary key.** The original fork used `(title, company)` tuples, which collapsed reposts and missed renames. The numeric ID from `/O<id>` URLs is stable.
- **Atomic file writes.** Both `jobs.json` and `subscribers.json` are written via `tempfile.mkstemp` → `os.replace` to prevent corruption on crash.
- **Regex salary parser.** Handles `Od 3 900 EUR/mesiac`, `1 500 - 2 000 EUR/mesiac`, `8 EUR/hod.`, and gracefully degrades to raw text for unrecognized formats.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Tests run offline against saved HTML fixtures in `tests/fixtures/` — no network access needed.

| Test file | Covers |
|---|---|
| `test_parser.py` | HTML card parsing, salary extraction, edge cases |
| `test_scraper.py` | Query URL building, pagination logic |
| `test_store.py` | Job merging, deduplication, atomic persistence |
| `test_config.py` | Config validation, error messages |
| `test_telegram.py` | Bot commands, subscriber management, message batching |