# Testing cheatsheet (pytest, for this project)

Short reference. Examples use this repo's real functions. Lesson order lives in
[testing-implementation-plan.md](testing-implementation-plan.md).

## 1. Running tests

Run from the repo root (`pyproject.toml` sets `testpaths` and `pythonpath`).

| Command | What it does |
|---|---|
| `.venv\Scripts\python.exe -m pytest` | run every test |
| `... -m pytest tests/test_text.py` | one file |
| `... -m pytest tests/test_text.py::test_long_text_is_cut` | one test |
| `... -m pytest -k salary` | tests whose name contains `salary` |
| `... -m pytest -q` | quiet: dots and a summary |
| `... -m pytest -v` | one line per test |
| `... -m pytest -x` | stop at the first failure |
| `... -m pytest --lf` | rerun only what failed last time |
| `... -m pytest -s` | show `print()` output (normally captured) |
| `... -m pytest --pdb` | open the debugger at a failure |

VS Code: the beaker icon (Testing panel) finds the same tests. Click ▶ next to one test to run it,
or the bug icon to debug it with breakpoints.

Output symbols: `.` passed, `F` failed (an `assert` was false), `E` error (crashed in setup or a
fixture), `s` skipped, `x` expected failure.

## 2. Files and names

- Test files: `tests/test_<module>.py`. Test functions: `def test_<what_happens>():`.
- pytest finds them by name, so no class or registration is needed.
- Name the test after the behaviour, not the function: `test_from_salary_has_no_max` beats `test_parse_salary_2`.
- `tests/conftest.py` holds fixtures shared by every test file. You never import it, pytest loads it.

## 3. The shape of a test: Arrange, Act, Assert

```python
from offer_monitor.text import shorten


# a text longer than the limit is cut and ends with "..."
def test_long_text_is_cut():
    # arrange: build the input
    text = "x" * 50
    # act: call the code under test once
    result = shorten(text, limit=10)
    # assert: check the result
    assert result == "xxxxxxx..."
    assert len(result) == 10
```

One behaviour per test. If a test name needs "and", split it.

## 4. `assert`

Plain `assert` is enough. On failure pytest shows both sides of the comparison.

```python
assert job.title == "C# Developer"
assert job.salary is None            # use `is` for None / True / False
assert "Profesia" in text            # membership
assert len(jobs) == 2
assert {j.id for j in jobs} == {1, 2}  # compare sets when order doesn't matter
assert result == pytest.approx(2500.5)  # floats
```

Compare whole objects when you can: `assert salary == Salary(raw=..., min=1500, ...)`.
Dataclasses compare field by field, so one assert checks everything.

## 5. Testing that code raises: `pytest.raises`

```python
import pytest
from offer_monitor.errors import ConfigError
from offer_monitor.config import parse_config


# a config without `providers` is rejected with a hint
def test_missing_providers_is_an_error():
    with pytest.raises(ConfigError, match="providers"):
        parse_config({})
```

`match` is a regex searched in the error message. Keep the `with` block to the one call that should raise.
To inspect the error object: `with pytest.raises(X) as exc_info: ...` then `exc_info.value.retry_after`.

## 6. Many inputs, one test: `@pytest.mark.parametrize`

```python
import pytest
from offer_monitor.providers.profesia_sk.parser import parse_salary


# each salary label maps to (min, max, currency, period)
@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Od 3 900 EUR/mesiac", (3900, None, "EUR", "month")),
        ("12 EUR/hod.", (12, 12, "EUR", "hour")),
    ],
)
def test_salary_label(label, expected):
    s = parse_salary(label)
    assert (s.min, s.max, s.currency, s.period) == expected
```

Each tuple becomes its own test (`test_salary_label[Od 3 900 EUR/mesiac-expected0]`).
Add `ids=["from", "hourly"]` to give them readable names.

## 7. Fixtures: reusable setup

A fixture is a function that builds something a test needs. A test asks for it by naming it as a parameter.

```python
# tests/conftest.py
import pytest
from offer_monitor.store import connect


# a fresh SQLite database file per test, with every table created
@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "jobs.db"


# an open connection to that database, closed after the test
@pytest.fixture
def conn(db_path):
    conn = connect(db_path)
    yield conn        # the test runs here
    conn.close()      # teardown: runs even if the test failed
```

```python
# tests/test_store.py
# a new database has no offers
def test_empty_db(conn):
    assert count_jobs(conn) == 0
```

Why a file and not `:memory:`: `run()` and `on_verdict()` open their own connections by path.
An in-memory database exists only inside one connection, so they would each see an empty one.

### Built-in fixtures you will use

| Fixture | Use |
|---|---|
| `tmp_path` | a new empty folder (`pathlib.Path`) per test, for the db, YAML files, backstory |
| `monkeypatch` | replace an attribute, env var, or dict item for one test; undone after it |
| `caplog` | capture `logging` records: `assert "not retrying" in caplog.text` |
| `capsys` | capture `print()`: `out = capsys.readouterr().out` |

## 8. `monkeypatch`: swap things for one test

```python
# no real waiting during retries
monkeypatch.setattr("offer_monitor.__main__.time.sleep", lambda s: None)

# point load_config() at a temp file (config.py reads CONFIG_PATH at call time for this)
monkeypatch.setattr("offer_monitor.config.CONFIG_PATH", tmp_path / "config.yaml")

# API key from the environment, or none at all
monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
```

Patch the name **where it is used**, not where it is defined. `scraper.py` does `import time` and
calls `time.sleep`, so patching `offer_monitor.providers.profesia_sk.scraper.time.sleep` works.
If a module did `from time import sleep`, you would patch `that_module.sleep` instead.

## 9. Fakes: stand-ins for the network, Telegram, the LLM

Tests must not touch the internet, Telegram, or an LLM. The code already takes these as
arguments, so you pass in a small fake class:

| Real thing | Seam (where the fake goes in) | Fake |
|---|---|---|
| Profesia HTTP | `scrape(..., session=...)`, `ProfesiaProvider(config, session)` | object with `.get()` returning a fake response |
| Telegram HTTP | `Bot(token, session=...)` | object with `.post()` returning a fake response |
| Whole bot | `run(config, bot, subs, ...)`, `handle_updates(bot, ...)`, `notify(bot, ...)` | class with `send`, `edit`, `get_updates` that records calls |
| A job source | `run(..., providers=[...])`, `fetch_source(provider)` | `SourceProvider` subclass whose `fetch()` returns a list or raises |
| The LLM | `run(config, bot, subs, review=...)` | `lambda url: Verdict("GO", "fits", url)` |

```python
# a requests.Response stand-in: only what the code reads
class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None, json_data=None):
        self.status_code, self.text, self.headers = status_code, text, headers or {}
        self._json = json_data

    # Bot.call() reads the body with .json()
    def json(self):
        if self._json is None:
            raise ValueError("not JSON")
        return self._json


# a requests.Session stand-in: returns queued responses in order, remembers each call
class FakeSession:
    def __init__(self, *responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    # scrape() uses .get()
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._next()

    # Bot.call() uses .post()
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._next()

    # an Exception in the queue is raised, to simulate a timeout or DNS failure
    def _next(self):
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
```

```python
# a source that fails `fails` times with `error`, then returns its jobs
class FlakyProvider(SourceProvider):
    name = "profesia"
    label = "Fake"

    def __init__(self, jobs, error=None, fails=0):
        self.jobs, self.error, self.fails, self.calls = jobs, error, fails, 0
        self.retry = RetryPolicy(attempts=3, delay=0)

    def fetch(self):
        self.calls += 1
        if self.calls <= self.fails:
            raise self.error
        return self.jobs
```

Fake vs `unittest.mock.Mock`: a fake is a tiny real class, so it's easy to read and fails loudly if
the code calls something unexpected. `Mock()` accepts any call and silently returns another `Mock`,
which can hide bugs. Prefer fakes here. Reach for `Mock` only for a one-off spy.

## 10. Test data builders

Most tests need a `Job`. A helper keeps every test focused on the one field it cares about:

```python
from offer_monitor.models import Job


# a valid Job; override only the fields the test is about
def make_job(id=1, source="profesia", **fields):
    defaults = dict(title=f"Job {id}", url=f"https://example.com/{id}", company="Acme")
    return Job(id=id, source=source, **(defaults | fields))
```

Put it in `conftest.py` as a plain function (import it: `from conftest import make_job`) or wrap
it in a fixture.

## 11. Threads and futures (verdicts)

`on_verdict(db, bot, job, message_ids, future)` takes a `Future`. Build one by hand, no thread needed:

```python
from concurrent.futures import Future

future = Future()
future.set_result(Verdict("GO", "good fit", job.url))   # or future.set_exception(RuntimeError("boom"))
on_verdict(db_path, fake_bot, job, {111: 5}, future)
```

`run()` submits to the module-level pool `VERDICTS`. Swap it per test so one test can't shut it
down for the others:

```python
from concurrent.futures import ThreadPoolExecutor

pool = ThreadPoolExecutor(max_workers=1)
monkeypatch.setattr("offer_monitor.__main__.VERDICTS", pool)
run(config, bot, subs, review, providers=[provider])
pool.shutdown(wait=True)   # wait for the verdict callbacks before asserting
```

## 12. Skips and markers

```python
@pytest.mark.skip(reason="needs a logged-in LinkedIn browser")
@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
@pytest.mark.xfail(reason="known bug: #12")   # expected to fail; a pass is reported as XPASS
```

## 13. What to test (and what not)

Test:
- your logic: parsing, dedup, retry decisions, migrations, formatting limits
- edges: empty input, `None`, one item, the exact limit, limit + 1
- each error path you wrote on purpose (`retryable = False`, 429 with `Retry-After`, a blocked chat)

Don't test:
- libraries (pydantic validates `ge=1`, you don't need to prove it, but **do** test your own validators)
- real network, Telegram, LLM output
- private details that may change, like exact log wording (check a key word, not the whole line)

A good test fails when the behaviour breaks and keeps passing through a refactor.

## 14. Coverage (optional, later)

```
.venv\Scripts\pip.exe install pytest-cov
.venv\Scripts\python.exe -m pytest --cov=offer_monitor --cov-report=term-missing
```

`term-missing` lists line numbers no test ran. Use it to find untested branches, not as a score to max out.
