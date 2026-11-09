# Testing implementation plan

Build a test suite for `offer_monitor` from zero, one lesson at a time, easiest code first.
Each lesson adds one test file and one or two new pytest ideas. Reference: [testing-cheatsheet.md](testing-cheatsheet.md).

Rules for the whole suite:
- No network, no Telegram, no LLM, no `data/` folder. Everything runs from `tmp_path` and fakes.
- All test data is written by hand in the tests (small HTML/text snippets), nothing copied from real pages.
- The whole suite should run in a few seconds: `time.sleep` is patched out wherever the code waits.
- One `#` line above each test says the scenario.

Run everything with `.venv\Scripts\python.exe -m pytest -q`.

---

## Lesson 0: Setup (done)

- `pyproject.toml` has `[tool.pytest.ini_options]` with `testpaths = ["tests"]` and `pythonpath = ["."]`,
  so `pytest` finds `offer_monitor` from the repo root and from the VS Code Testing panel.
- pytest 9 is already in `requirements-dev.txt`.
- No `tests/__init__.py`: pytest then puts `tests/` on `sys.path`, so tests can `from conftest import ...`.

## Lesson 1: Pure functions (`assert`, `parametrize`)

Functions that take a value and return a value. No setup, no fakes.

| Code | File |
|---|---|
| `text.shorten` | `tests/test_text.py` |
| `profesia_sk.parser.parse_salary` | `tests/test_slovakia_salary.py` |

Cases:
- [x] `shorten`: exactly the limit, over the limit, custom limit, never longer than the limit
- [x] `parse_salary`: range, "Od"/"From" (no max), "Do" (no min), single amount (min == max), hourly, yearly,
      English words, USD, unknown text keeps `raw` only, `None` / `""` / spaces give `None`
- [ ] `parse_salary`: decimal comma (`2 500,50 Kč/mesiac`), `\xa0` spaces, trailing text after the period
      gives all `None` (the `$` anchor)

New ideas: Arrange-Act-Assert, `assert` on whole dataclasses, `@pytest.mark.parametrize`, `ids=`.

## Lesson 2: Small helpers with a stand-in object

| Code | File |
|---|---|
| `http.retry_after_seconds` | `tests/test_http.py` |
| `telegram._command` | `tests/test_telegram_format.py` |
| `models.Job.to_dict` / `from_dict` | `tests/test_models.py` |

Cases:
- [x] `retry_after_seconds`: `"30"`, `"0"`, negative clamps to 0, missing header, HTTP-date form gives `None`
- [x] `_command`: `/start`, `/START`, `/start@MyBot payload`, plain text gives `""`
- [ ] `Job` round trip: `Job.from_dict(job.to_dict()) == job` with salary and verdict set, and with both `None`

New ideas: use the real object when it's cheap to build (`requests.Response()`), round-trip tests.

## Lesson 3: HTML parsing with hand-written snippets

| Code | File |
|---|---|
| `profesia_sk.parser.parse_row`, `parse_listing` | `tests/test_profesia_parser.py` |
| `linkedin.provider.parse_job_details`, `_error_for` | `tests/test_linkedin_parser.py` |

Write the smallest HTML that has the elements the parser reads (`li.list-row`, `h2 a[id^=offer]`,
`.employer`, `.job-location`, `.label-group a[data-dimension7]`, `ul.pagination a.next`).

Cases:
- [ ] one full row: id, title, url without query string, company, company_id from the logo link, location, salary, labels
- [ ] a row without the offer link (an ad) is skipped
- [ ] page with rows but none parse raises `ProfesiaLayoutError`
- [ ] empty page gives `([], False)`; `has_next` is True only with `a.next`
- [ ] LinkedIn: header lines parsed, labels stop at `Apply`, too-short text raises `ValueError`
- [ ] `_error_for`: "Please log in" gives `LinkedInAuthError`, "Downloading..." gives `LinkedInUnavailableError`

New ideas: `pytest.raises(..., match=...)`, test data as module constants, building inputs with f-strings.

## Lesson 4: Config validation (`tmp_path`, `monkeypatch`)

| Code | File |
|---|---|
| `config.parse_config`, `load_config`, `settings.read_yaml`, `require_file`, `validate_file` | `tests/test_config.py` |
| `profesia_sk.config.Query`, `ProfesiaConfig` | `tests/test_profesia_config.py` |
| `linkedin.config` validators | `tests/test_linkedin_config.py` |

Cases:
- [ ] missing `providers` gives a `ConfigError` that names it
- [ ] unknown key is rejected (`extra="forbid"`), error names the field path like `searches[0].remote_work`
- [ ] `read_yaml`: empty file gives `{}`, a list at top level is an error, bad YAML names the line
- [ ] `require_file` message names the `.example.yaml` to copy
- [ ] `load_config` resolves relative `db` / `backstory` against the config folder (patch `CONFIG_PATH`)
- [ ] `Query.url()` and `params(page)` include only the filters that are set
- [ ] `count_days` inheritance: unset inherits the file value, explicit `null` stays `None`
- [ ] blank keyword and a bad segment slug are rejected

New ideas: `tmp_path` for real files, `monkeypatch.setattr` on a module global.

## Lesson 5: The database (fixtures, `conftest.py`)

| Code | File |
|---|---|
| `store.jobs` | `tests/test_store_jobs.py` |
| `store.lease` | `tests/test_store_lease.py` |
| `store.subscribers` | `tests/test_store_subscribers.py` |
| `store.db` migrations | `tests/test_store_migrations.py` |

Create `tests/conftest.py` with `db_path`, `conn` fixtures and a `make_job()` builder.

Cases:
- [ ] `store_new` inserts all, returns all on an empty db; stamps `first_seen` / `last_seen`
- [ ] same title + company (case and spaces differ) from another source is stored but not returned
- [ ] offers without a company are never treated as duplicates
- [ ] inserting the same `(source, id)` twice raises `sqlite3.IntegrityError`
- [ ] `known_ids` only matches the given source
- [ ] `refresh_known` updates title / `last_seen`, keeps `uuid`, `first_seen`, verdict
- [ ] `save_verdict` then `get_job` returns the `Verdict`
- [ ] lease: free gets claimed; fresh foreign lease refused (returns the holder); stale one (> `LEASE_TTL`) taken;
      same owner retakes at once; `renew_lease` False after takeover; `release_lease` ignores a non-owner
- [ ] subscribers: save then load round trip; offset `None` before first save
- [ ] migrations: create an old-style table by hand, `connect()` upgrades it and keeps rows;
      `connect()` twice in a row is a no-op (idempotent)

New ideas: fixtures with `yield` teardown, fixtures using fixtures, testing with timestamps you choose.

## Lesson 6: HTTP and scraping (fake session)

| Code | File |
|---|---|
| `profesia_sk.scraper._fetch_page`, `scrape` | `tests/test_profesia_scraper.py` |
| `profesia_sk.provider.ProfesiaProvider.fetch` | same file |

Cases:
- [ ] status mapping: 429 gives `ProfesiaRateLimitedError` with `retry_after`, 401/403 Blocked, 5xx Unavailable,
      404 `ProfesiaError`, `requests.ConnectionError` Unavailable
- [ ] `scrape` stops when there is no next page, and at `max_pages`
- [ ] a duplicate id on page 2 is dropped
- [ ] page 1 failing raises; page 2 failing keeps page 1's jobs
- [ ] the request carries `search_anywhere` and `page_num`
- [ ] provider runs every search and labels jobs `source="profesia"`

New ideas: `FakeSession` that queues responses and records calls, `delay=0` instead of patching sleep.

## Lesson 7: Retry policy

| Code | File |
|---|---|
| `__main__.fetch_source` | `tests/test_fetch_source.py` |

Cases:
- [ ] success on the first try: one call, no sleep
- [ ] retryable error twice then success: 3 calls, returns jobs
- [ ] retryable error on every try: `None` after `attempts` calls
- [ ] non-retryable (`SourceBlockedError`): `None` after one call
- [ ] `retry_after` on the error is used instead of `delay`
- [ ] a warning is logged when it gives up (`caplog`)

New ideas: a fake `SourceProvider` subclass, recording `sleep` calls with `monkeypatch`, `caplog`.

## Lesson 8: Telegram

| Code | File |
|---|---|
| `telegram.Bot.call` | `tests/test_telegram_bot.py` |
| `handle_updates`, `notify`, `Subscribers.load` | `tests/test_telegram_subscribers.py` |
| `format_job`, `format_verdict` | `tests/test_telegram_format.py` |

Cases:
- [ ] `ok: true` returns `result`; 401 gives `TelegramAuthError`; 403 and "chat not found" give `TelegramBlockedError`
- [ ] 429 with `retry_after` sleeps once and retries; a second 429 raises
- [ ] network error retried once, then `TelegramNetworkError` whose message hides the token
- [ ] non-JSON reply (gateway HTML) retried once
- [ ] `/start` adds a chat, `/stop` keeps the row but sets `active=False`, `/start` again reactivates
- [ ] offset becomes last `update_id + 1`; returns `False` when no updates
- [ ] `format_job` escapes `<` and `&` in titles; unknown source shows its raw name
- [ ] `format_verdict` never exceeds 4096 characters, even with a huge report full of `&`
- [ ] `notify` disables a chat on `TelegramBlockedError` and returns `changed=True`
- [ ] `Subscribers.load` imports the legacy JSON once, then ignores it

New ideas: a fake `Bot` class recording `send` / `edit`, testing a size limit with generated input.

## Lesson 9: The pipeline end to end

| Code | File |
|---|---|
| `__main__.run`, `on_verdict`, `build_review` | `tests/test_pipeline.py` |

Cases:
- [ ] `run()` with one fake provider: offers stored, fake bot got one message per offer, message ids saved
- [ ] second `run()` with the same offers: nothing new, nothing sent, `last_seen` updated
- [ ] one provider failing does not stop the other
- [ ] with a fake `review`: verdict saved in the db and the Telegram message edited (swap `VERDICTS`)
- [ ] `on_verdict` with a failed future logs and saves nothing; a cancelled future does nothing
- [ ] `build_review` returns `None` without a `model` section, and when the backstory file is missing

New ideas: integration tests, `Future` built by hand, swapping a module-level thread pool, `capsys`.

## Lesson 10: Agents and LinkedIn without the outside world

| Code | File |
|---|---|
| `agents.tools.fetch_offer` | `tests/test_agent_tools.py` |
| `agents.llm.build_llm` | `tests/test_agent_llm.py` |
| `linkedin.provider.collect`, `LinkedInProvider.fetch` | `tests/test_linkedin_collect.py` |

Cases:
- [ ] `fetch_offer` drops `<script>` / `<style>`, cuts at `MAX_PAGE_CHARS`, returns `"error: ..."` on a 404
- [ ] `fetch_offer` retries a 429 then succeeds (patch `requests.get` and `time.sleep`)
- [ ] `build_llm` without a key in config or env raises `ConfigError` naming the env var
- [ ] `collect` with an async fake `call`: ids deduped across searches, a failed search skipped,
      `LinkedInAuthError` stops everything, a bad detail text skipped
- [ ] `LinkedInProvider.fetch` with a fake `open_session`: a crashing session becomes `LinkedInUnavailableError`

New ideas: `fetch_offer.invoke({"url": ...})` for LangChain tools, `asyncio.run` in a sync test, async fakes.

## Lesson 11: Wrap-up

- [ ] coverage report (`pytest-cov`), look at the missing lines, add tests only where a bug could hide
- [ ] update `CLAUDE.md` "Tests" note: the suite exists, how to run it
- [ ] optional: run the suite in Docker or a GitHub Action

## Out of scope

- `main()`'s forever loop, signal handling, lease heartbeat thread: thin glue over tested parts, slow to test.
- Real LLM verdict quality: not something a unit test can judge.
- `cli.build_parser`: argparse itself, nothing of ours to check.
