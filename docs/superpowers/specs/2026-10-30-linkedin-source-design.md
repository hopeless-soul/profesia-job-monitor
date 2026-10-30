# LinkedIn source — design

Date: 2026-10-30
Status: approved in brainstorming, pending spec review

## Goal

Add LinkedIn as a second job source next to Profesia.sk. Each run searches recent
LinkedIn jobs, converts them to `Job` (with a new `source` field), stores them in the
same SQLite database, notifies Telegram subscribers and gets an AI verdict, exactly
like Profesia offers. Jobs with the same title and company as an already stored job
(any source) are stored but not notified or reviewed.

## Decisions

| Topic | Decision |
|---|---|
| Fetch method | LinkedIn MCP server (`mcp-server-linkedin`, browser-backed, user's logged-in session) |
| Driver | Python code calls MCP tools directly (no LLM for searching) |
| Mapping to `Job` | `search_jobs` → IDs; skip IDs already in DB; `get_job_details` for the rest; parse header lines |
| Duplicates | Normalized title + company checked against SQLite across all sources, and within the batch |
| Verdicts | Unchanged: orchestrator gets the job URL |
| Where it runs | Local runs only (needs a visible browser for login). Docker: step fails and is skipped with a warning |

## 1. Data model and store

### `models.Job`

- Add `source: str = "profesia"`. Values: `"profesia"`, `"linkedin"`.
- `id` stays `int`; for LinkedIn it holds the LinkedIn job ID.

### `store.py`

- `offers` gets `source TEXT NOT NULL DEFAULT 'profesia'`; the unique key changes from
  `UNIQUE(id)` to `UNIQUE(source, id)`.
- Migration in `connect()`: when `offers` exists without a `source` column, in one
  transaction create the new table, copy rows with `source='profesia'`, drop the old
  table, rename the new one. `offer_messages` references `uuid`, which is unchanged.
- `insert_jobs` / `upsert_jobs` match on `(source, id)`; `source` is added to
  `_INSERT_COLUMNS`.
- `get_job(conn, id, source="profesia")`; existing callers (tests) keep working.
- New `known_ids(conn, source, ids) -> set[int]`.

### Duplicate check (in `upsert_jobs`, applies to both sources)

- Key: `title` and `company`, each `casefold()`ed with whitespace runs collapsed to one space.
- A job with no company is never a duplicate.
- A new job whose key matches a stored job (any source, any ID) or an earlier job in
  the same batch is still inserted (so it is not re-fetched next run) but is left out
  of the returned `added` list: no Telegram message, no verdict.
- `run_once` prints the count as `dup N` in its summary line.
- Behavior change accepted: two Profesia offers with the same title and company
  (e.g. two cities) now notify once.

## 2. LinkedIn client and config

### `profesia_monitor/linkedin.py`

- Official `mcp` Python SDK (`ClientSession` over `stdio_client`); the only new dependency.
- Starts `uvx mcp-server-linkedin@latest --transport stdio` once per run, closes it at the end.
- Public API: `fetch_jobs(config: LinkedInConfig, known: Callable[[list[int]], set[int]]) -> list[Job]`,
  synchronous, wrapping one `asyncio.run`.
- Steps:
  1. For each search, call `search_jobs` with `keywords`, `date_posted`, `max_pages` and
     the optional filters; collect `job_ids`, deduplicated across searches.
  2. Drop IDs returned by `known(ids)`.
  3. For each remaining ID, call `get_job_details` and parse with
     `parse_job_details(job_id: int, text: str) -> Job`.
- `parse_job_details` (pure function) reads the `job_posting` text:
  - first non-empty line → `company`, second → `title`;
  - third line split on `" · "` → `location` (part 1), `posted_text` (part 2);
  - lines in {Remote, Hybrid, On-site, Full-time, Part-time, Contract, Temporary,
    Internship} before the `Apply` line → `labels`;
  - `url = https://www.linkedin.com/jobs/view/<id>/`, `source="linkedin"`,
    `salary=None`, `company_id=None`;
  - raises `ValueError` when company, title or location line is missing.
- Errors (tools report problems as error results, not exceptions):
  - a search fails, or the server reports login in progress / setup not complete →
    log a warning, return `[]`; Profesia still runs;
  - a details call fails or the header doesn't parse → skip that job (not stored, so
    it is retried next run);
  - the whole LinkedIn step has a time limit (5 minutes); on timeout log a warning and
    return the jobs parsed so far.

### Config

New optional top-level section; missing means LinkedIn is off.

```json
"linkedin": {
  "date_posted": "past_24_hours",
  "max_pages": 1,
  "searches": [
    { "keywords": "python developer in Slovakia", "work_type": "remote,hybrid" },
    { "keywords": "c# developer in Bratislava" }
  ]
}
```

- `config.py`: `LinkedInConfig(searches, date_posted="past_24_hours", max_pages=1)` and
  `LinkedInSearch(keywords, work_type=None, experience_level=None, job_type=None)`;
  `Config.linkedin: LinkedInConfig | None = None`.
- Validation:
  - `searches` is a non-empty list; `keywords` is a required non-empty string;
  - `date_posted` ∈ {past_hour, past_24_hours, past_week, past_month};
  - `max_pages` is an integer 1–10;
  - `work_type` ⊆ {on_site, remote, hybrid}; `experience_level` ⊆ {internship, entry,
    associate, mid_senior, director, executive}; `job_type` ⊆ {full_time, part_time,
    contract, temporary, volunteer, internship, other} — each comma-separated;
  - unknown keys are rejected (same style as existing sections).
- Location goes inside `keywords`: LinkedIn's redesigned search drops the separate
  location filter.
- A one-off CLI `KEYWORD` run stays Profesia-only (`config.linkedin = None`).
- `schemas/config.schema.json` and `data/config.example.json` updated.

## 3. Wiring, Telegram, verdicts

### `run_once`

- After the Profesia searches, when `config.linkedin` is set:
  `scraped += linkedin.fetch_jobs(config.linkedin, known=<known_ids on config.db for "linkedin">)`,
  printing `linkedin: N offers`.
- Then unchanged: `upsert_jobs` (now with the duplicate check), `notify`, verdict
  submission, `on_verdict`.
- `NEW` line includes the source: `NEW linkedin 4484440903  Title | Company | -`.

### Telegram

- `format_job` details line appends the source display name: `Ciklum · Bratislava, Slovakia · LinkedIn`
  (Profesia offers show `· Profesia`).

### Verdicts

- Unchanged. `fetch_offer` downloads the public `linkedin.com/jobs/view/<id>/` page.
  If LinkedIn serves a login wall, the reviewer answers UNREACHABLE (existing behavior).
  Passing the already-fetched description is deferred until that happens in practice.

## Testing

No real browser or network in tests.

- `tests/test_linkedin.py`:
  - `parse_job_details` against `tests/fixtures/linkedin_job_details.txt` (shortened real
    sample) and a malformed sample;
  - `fetch_jobs` with a fake MCP session: known IDs skipped, IDs deduplicated across
    searches, login error → `[]`, failing details call skips one job.
- `tests/test_store.py`: old-schema migration keeps rows, uuids and messages and sets
  `source='profesia'`; `(source, id)` uniqueness; duplicate check across sources and
  within a batch; no company → never a duplicate.
- `tests/test_config.py`: valid and invalid `linkedin` sections.
- `tests/test_main.py`: `run_once` merges stubbed LinkedIn jobs; duplicates are not
  notified or reviewed.
- `tests/test_telegram.py`: source label.

## Docs and dependencies

- `requirements.txt`: add `mcp`.
- README: LinkedIn section — setup (`uv` installed, first run downloads the server
  and Chromium), first manual login in a console run, local-only, account risk of
  automated use.

## Out of scope

- Docker support for LinkedIn (needs Chromium and a mounted logged-in profile).
- LinkedIn salary parsing.
- Passing fetched description text to the reviewer.
- AI-driven search (scout agent).
