# Source providers — design

Date: 2026-11-07
Status: draft for review. Builds on `2026-10-30-linkedin-source-design.md`.

## Goals

- One interface for every job source. `run_once` loops over providers instead of
  calling `scrape()` and `linkedin.fetch_jobs()` by hand.
- Adding a source means adding one provider class and one line in `providers.py`.
- Each provider's network client is injected, so tests run with a fake session.
- The source name is set once per provider. The `'profesia'` / `'linkedin'` literals
  in `Job`, `store.get_job` and `telegram` are removed or derived.
- Existing behaviour and the config format stay the same, except where listed under
  "Behaviour changes".
- Pin `mcp` to the major version the code uses.

## Non-goals

- No plugin registry or auto-discovery. The list in `providers.py` is explicit.
- No per-search isolation for Profesia (its searches stay as they are).
- No change to duplicate detection, verdicts, Telegram flow or the config format.
- No LinkedIn AI search, salary parsing or Docker support.

## Decisions (assumed, to confirm in "Open questions")

| Topic | Decision |
|---|---|
| Interface shape | `ABC` with `name`, `label` class attributes and `fetch(known)` |
| Failure handling | `run_once` catches `SourceProviderError` per provider, logs it and continues |
| Source name | Class attribute `name`; passed into every `Job` the provider builds |
| `Job.source` default | Removed: required keyword-only field |
| LinkedIn search failures | Isolated per search (review finding 3) |
| LinkedIn pacing | Reuse top-level `delay` (default 1.5 s) between `get_job_details` calls (finding 4) |
| MCP pin | `mcp>=2.0,<3` (evidence below) |

## Interface

### `profesia_monitor/sources.py` (new, base only, no provider imports)

```python
# Base error: run_once logs it and moves on to the next provider
class SourceProviderError(RuntimeError):
    pass


# Base class for job sources
class SourceProvider(ABC):
    # Written to Job.source and the offers table; set once per provider
    name: ClassVar[str]
    # Shown in Telegram messages
    label: ClassVar[str]

    # New offers only, each with source=self.name. `known(ids)` returns the IDs already stored.
    # Raises SourceProviderError when the source failed for this run.
    @abstractmethod
    def fetch(self, known: Callable[[list[int]], set[int]]) -> list[Job]: ...
```

### `profesia_monitor/providers.py` (new, registry)

```python
# Every provider class; a new source is added here
PROVIDER_CLASSES = (ProfesiaProvider, LinkedInProvider)  # imported at top of the module
# Label per source name, used by telegram.format_job
SOURCE_LABELS = {cls.name: cls.label for cls in PROVIDER_CLASSES}


# Providers for this run, in order; LinkedIn only when its config section exists
def build_providers(config: Config) -> list[SourceProvider]:
    providers: list[SourceProvider] = [ProfesiaProvider(config.searches, config.pages, config.delay)]
    if config.linkedin:
        providers.append(LinkedInProvider(config.linkedin, config.delay))
    return providers
```

`providers.py` imports `scraper` and `linkedin`; neither imports `telegram`, `__main__`
or `providers`, so there is no cycle. `telegram` imports `SOURCE_LABELS` from `providers`.

### `profesia_monitor/scraper.py`

```python
# Profesia listing scraper as a provider
class ProfesiaError(SourceProviderError):
    pass


class ProfesiaProvider(SourceProvider):
    name = "profesia"
    label = "Profesia"

    def __init__(self, searches: list[Query], pages: int, delay: float, session: requests.Session | None = None):
        self.searches, self.pages, self.delay, self.session = searches, pages, delay, session

    # Scrape every search; a first-page failure becomes ProfesiaError
    def fetch(self, known) -> list[Job]:
        jobs: list[Job] = []
        try:
            for query in self.searches:
                jobs += scrape(query, source=self.name, max_pages=self.pages, delay=self.delay, session=self.session)
        except requests.RequestException as e:
            raise ProfesiaError(str(e)) from e
        return jobs
```

`scrape(query, *, source, max_pages=3, delay=1.5, session=None)` passes `source` to
`parse_listing(html, source)` → `parse_row(row, source)`, which sets `Job.source`.

### `profesia_monitor/linkedin.py`

```python
# LinkedIn errors share the base class; the subclass is unchanged in behaviour
class LinkedInError(SourceProviderError):
    pass


# Opens an MCP session; the default starts the server. Tests pass a fake context manager.
SessionOpener = Callable[[], AbstractAsyncContextManager[ClientSession]]


# Real session: start the server over stdio and initialise it
@asynccontextmanager
async def stdio_session() -> AsyncIterator[ClientSession]:
    async with stdio_client(SERVER) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


class LinkedInProvider(SourceProvider):
    name = "linkedin"
    label = "LinkedIn"
    time_limit = 300  # seconds for the whole LinkedIn step

    def __init__(self, config: LinkedInConfig, delay: float, open_session: SessionOpener = stdio_session):
        self.config, self.delay, self.open_session = config, delay, open_session

    # Blocking wrapper around one asyncio run. A timeout returns the jobs parsed so far.
    # Any other failure to talk to the server becomes LinkedInError.
    def fetch(self, known) -> list[Job]:
        jobs: list[Job] = []
        try:
            asyncio.run(asyncio.wait_for(self._run(known, jobs), self.time_limit))
        except TimeoutError:
            log.warning("linkedin stopped after %ss, keeping %d jobs", self.time_limit, len(jobs))
        except LinkedInError:
            raise
        except Exception as e:
            raise LinkedInError(f"server failed: {e}") from e
        return jobs

    # Open the session, then collect
    async def _run(self, known, jobs: list[Job]) -> None:
        async with self.open_session() as session:
            await collect(partial(_call, session), self.name, self.config, self.delay, known, jobs)
```

`collect(call, source, config, delay, known, jobs)` changes:

- Each search is wrapped in its own `try`. A `LinkedInError` is logged as a warning
  and the loop continues, so other searches' IDs are kept.
- `await asyncio.sleep(delay)` before every `get_job_details` call except the first.
- `parse_job_details(job_id, text, source)` receives `source` from the provider.

`_call` keeps the `result.is_error` check and also converts invalid JSON to
`LinkedInError`, so a bad reply is handled like a server error.

`parse_job_details(job_id, text, source)` no longer hard-codes `source="linkedin"`.

### `profesia_monitor/__main__.py` — `run_once`

```python
# Scrape every provider; a failing provider is logged and the others still run
def run_once(config, bot, subs, review=None, providers: list[SourceProvider] | None = None) -> None:
    providers = build_providers(config) if providers is None else providers

    # fetch each source; known(ids) reads only this provider's stored IDs
    scraped: list[Job] = []
    for provider in providers:
        with closing(connect(config.db)) as conn:
            try:
                jobs = provider.fetch(partial(known_ids, conn, provider.name))
            except SourceProviderError as e:
                logging.warning("%s skipped: %s", provider.name, e)
                continue
        print(f"{provider.name}: {len(jobs)} offers")
        scraped.extend(jobs)

    # store, notify and review: unchanged from here on
    ...
```

The `providers` parameter is the test seam for `run_once`. Store, notify and verdict
code after the fetch loop does not change.

## Error hierarchy

```
RuntimeError
└── SourceProviderError            sources.py   run_once catches this
    ├── ProfesiaError              scraper.py
    └── LinkedInError              linkedin.py
```

`ValueError` from parsing (`parse_job_details`, `parse_listing`) stays a per-job skip, as
now. Anything else (a bug) is not caught and still stops the run.

## Store and model changes

- `models.Job.source`: `source: str = field(kw_only=True)`. Required, no default.
- `store._OFFERS_TABLE`: `source TEXT NOT NULL` with no `DEFAULT`. Existing databases
  keep their old default, which is harmless because every insert passes `source`.
- `store._migrate`: keeps the literal `'profesia'`, with a comment that all rows before
  the LinkedIn change came from Profesia.
- `store.get_job(conn, id, source)`: `source` is required, no default.
- `store.known_ids`, `upsert_jobs`, `insert_jobs`: unchanged.

### Source-name defaults after this change

| Where | Before | After |
|---|---|---|
| `models.Job.source` | default `"profesia"` | required, keyword-only |
| `store` DDL | `DEFAULT 'profesia'` | no default (old DBs keep theirs) |
| `store.get_job` | `source="profesia"` | required |
| `parser.parse_row` | relies on `Job` default | `source` passed from `scrape` |
| `linkedin.parse_job_details` | literal `"linkedin"` | `source` passed from provider |
| `telegram.SOURCE_NAMES` | literal dict | `SOURCE_LABELS` from `providers.py` |
| `store._migrate` | `'profesia'` | unchanged (correct: old rows are Profesia) |

## MCP pin decision

Evidence, from the repo and from downloaded wheels:

- `pip show mcp` in `.venv`: **2.3.0** (`mcp-types` 2.3.0 installed alongside).
- `linkedin.py` reads `result.is_error`. In `mcp-types` 2.3.0 the field is `is_error`
  (wire alias `isError`), and the docstring says so.
- mcp **1.26.0** wheel (downloaded to scratch, not installed): `CallToolResult` has
  `isError: bool = False`, no `is_error`. On 1.x, `_call` raises `AttributeError`. That
  is not a `LinkedInError`, so the whole LinkedIn step fails silently with "linkedin
  skipped" in the log.
- mcp **2.0.0** wheel: `mcp-types` 2.0.0 defines `is_error`; `mcp/__init__.py` exports
  `ClientSession` and `stdio_client`; `ClientSession.call_tool` exists.
- `StdioServerParameters`, `ClientSession`, `stdio_client` are exported from the top
  level `mcp` package in 2.3.0 and 2.0.0 (checked).

Recommendation: `mcp>=2.0,<3` in `requirements.txt`. The floor is the first 2.x I checked
that has `is_error`. Testing is against 2.3.0. Add a test that asserts
`"is_error" in CallToolResult.model_fields`, so a future downgrade fails loudly.

## Behaviour changes

1. A Profesia first-page failure no longer ends the run. It is logged as a warning, the
   other providers still run, and `run_once` returns normally. Before, `main` logged
   "run failed; retry in 30 s". Cron mode now waits the full interval. (Open question 1.)
2. Each provider prints `"<name>: N offers"`. Profesia gets a line it did not have.
3. LinkedIn: a failing search no longer discards the other searches' IDs.
4. LinkedIn: details calls are spaced by `delay`.
5. The DB has no `DEFAULT` for new tables. No effect on existing data.

## Testing plan

No real browser, network or uvx in tests.

- `tests/test_sources.py` (new):
  - `build_providers` returns Profesia only when `config.linkedin` is None, and both
    when it is set;
  - `SourceProviderError` is the base of `ProfesiaError` and `LinkedInError`;
  - each provider's `name` is unique and `SOURCE_LABELS` covers them.
- `tests/test_linkedin.py`:
  - replace the `monkeypatch` of `_fetch` with a fake session passed as `open_session`;
  - the fake session's `call_tool` returns real `CallToolResult` objects, for example
    `CallToolResult(content=[TextContent(type="text", text=...)], is_error=True)` for a
    login error, so `_call` runs for real;
  - failing search: other searches' IDs are still read (per-search isolation);
  - `is_error=True` result raises `LinkedInError` through `_call`;
  - invalid JSON text raises `LinkedInError`;
  - a session that raises `FileNotFoundError` on open becomes `LinkedInError`;
  - timeout (monkeypatch `time_limit` to a small value, slow fake) returns partial jobs;
  - `delay` is passed to `asyncio.sleep` between details calls (monkeypatch sleep);
  - `parse_job_details` takes `source` and sets it.
- `tests/test_scraper.py`: `ProfesiaProvider.fetch` wraps a first-page `RequestException`
  as `ProfesiaError`; `scrape` sets `source` on jobs.
- `tests/test_parser.py`: `parse_listing(html, source)` sets `source`.
- `tests/test_store.py`: update `get_job` calls to pass `source`; a `Job` built without
  `source` raises `TypeError`; the migration test is unchanged.
- `tests/test_main.py`: `run_once(..., providers=[fake_a, fake_b])`:
  - a fake provider raising `SourceProviderError` does not stop the other provider's
    jobs from being stored and notified;
  - the existing LinkedIn merge test uses a fake provider instead of
    `monkeypatch(main.linkedin, "fetch_jobs")`.
- `tests/test_telegram.py`: the source label comes from `SOURCE_LABELS`.
- `tests/test_requirements.py` or a line in `test_linkedin.py`: `CallToolResult` has
  `is_error`.

## Review findings

Folded into this design:

| # | Finding | Handling |
|---|---|---|
| 1 | `is_error` vs mcp 1.x | Pin `mcp>=2.0,<3` plus the field-name test |
| 3 | One failed search discards all results | Per-search isolation in `collect` |
| 4 | No pacing on `get_job_details` | `delay` from top-level config |
| 6 | No provider abstraction | `SourceProvider`, `run_once` loop |
| 7 | `stdio_client` hard-wired, untested `_call` | `open_session` injection, real `CallToolResult` tests |

Left out (out of scope, separate change):

| # | Finding | Why |
|---|---|---|
| 2 | Title + company duplicate rule has no time window or location | Product rule, not a provider concern; the design does not change `upsert_jobs` |
| 5 | ANSI codes in logs, `os.system("")` on import | Console formatting, unrelated to providers |
| 8 | `_retry_after(resp) or delay` treats 0 as missing; retry budget uncapped | In `agents/tools.py`, not the job sources |
| 9 | `date_posted not in _DATE_POSTED` raises TypeError for unhashable values | One-line fix in `config.py`, separate commit |
| 10 | `work_type` pattern rejects spaces; `keywords` accepts whitespace | Schema tweak, separate commit |

## Open questions

1. **Profesia failure behaviour.** Assumed: isolated like LinkedIn (log and continue).
   Alternative: only LinkedIn errors are isolated, and a Profesia failure still stops the run.
2. **`Job.source` required.** Assumed: required keyword-only, which means updating about
   a dozen test constructors and nine `get_job` calls. Alternative: keep a default.
3. **LinkedIn per-search isolation.** Assumed: yes (the spec's "return []" wording
   contradicts "Profesia still runs" for multi-search configs).
4. **LinkedIn pacing.** Assumed: reuse top-level `delay` (1.5 s). Alternative: a new
   `linkedin.delay` key.
5. **Registry.** Assumed: an explicit tuple in `providers.py`. Alternative:
   auto-registration through `__init_subclass__`, which is more magic than the project needs.
6. **Subscribe message text.** Kept as is ("Profesia.sk and LinkedIn"). Alternative:
   build it from `SOURCE_LABELS`.
7. **mcp floor.** Assumed: `>=2.0,<3`, tested on 2.3.0. Alternative: `>=2.3,<3` if you
   want only the version the tests ran on.
