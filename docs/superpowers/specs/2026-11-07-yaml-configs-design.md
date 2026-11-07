# YAML configs with pydantic validation — design

Date: 2026-11-07
Status: approved in brainstorming, awaiting review of this written spec.

## Goal

Move every config file from JSON to YAML, and validate each file on load with
typed pydantic models instead of the hand-written checks in `config.py` and the
provider `config.py` modules.

## Non-goals

- No change to search logic, providers, retry rules, or the Telegram and LLM flows.
- No dual-format support: the loader does not read `.json` after the migration.
- No generated JSON schemas (the hand-written ones are removed; see below).

## Files

| File | Holds | On when |
|---|---|---|
| `data/config.yaml` | `db`, `backstory`, `interval`, `telegram`, `model` | always (required) |
| `data/profesia_sk.yaml` | Profesia searches, `pages`, `count_days`, `delay`, `retry` | the file exists |
| `data/linkedin.yaml` | LinkedIn searches, `date_posted`, `max_pages`, `delay`, `retry` | the file exists |

- The real files are under `data/`, which is gitignored. The examples are committed:
  `data/config.example.yaml`, `profesia_monitor/providers/profesia_sk/config.example.yaml`,
  `profesia_monitor/providers/linkedin/config.example.yaml`.
- Examples carry comments on each key. YAML comments replace the schema descriptions.
- Removed: `schemas/` (all four JSON Schema files), `data/config.example.json`, and the
  provider `config.example.json` files. The `$schema` line is no longer used.

## Classes

All models use pydantic v2 with `model_config = ConfigDict(extra="forbid")`, so an
unknown key is an error.

`profesia_monitor/settings.py` (new, shared base):

- `RetryPolicy(BaseModel)`: `attempts: int = Field(3, ge=1, le=10)`,
  `delay: float = Field(30, ge=0)`.
- `SourceSettings(BaseModel)`: `delay: float = Field(1.5, ge=0)`,
  `retry: RetryPolicy = RetryPolicy()`.

`profesia_monitor/providers/profesia_sk/config.py`:

- `Query(BaseModel)`: `keyword` (non-empty), `segments` (each matches `^[a-z0-9-]+$`),
  `remote_work` (`0 | 1 | 2 | None`), `min_salary` (`int > 0 | None`),
  `salary_period` (`"m" | "h"`), `count_days` (`int >= 1 | None`). Keeps the
  `url()` and `params()` methods the scraper uses.
- `ProfesiaConfig(SourceSettings)`: `searches: list[Query] = Field(min_length=1)`,
  `pages: int = Field(3, ge=1)`, `count_days: int | None = Field(None, ge=1)`.
  A search with no `count_days` inherits the file-level value, in a model validator.

`profesia_monitor/providers/linkedin/config.py`:

- `LinkedInSearch(BaseModel)`: `keywords` (non-empty), and `work_type`,
  `experience_level`, `job_type` as optional comma-separated strings. Each value is
  checked against its allowed set by a field validator.
- `LinkedInConfig(SourceSettings)`: `searches: list[LinkedInSearch] = Field(min_length=1)`,
  `date_posted: Literal["past_hour", "past_24_hours", "past_week", "past_month"] = "past_24_hours"`,
  `max_pages: int = Field(1, ge=1, le=10)`.

`profesia_monitor/config.py`:

- `TelegramConfig(BaseModel)`: `token: str | None = None`, `subscribers: Path = "subscribers.json"`.
- `ModelConfig(BaseModel)`: `provider: Literal["anthropic", "openai", "google"] = "anthropic"`,
  `name: str | None = None`, `temperature: float = Field(0.0, ge=0, le=2)`,
  `max_tokens: int = Field(8192, ge=1)`, `api_key: str | None = None`. An empty
  `api_key` becomes `None`.
- `MainConfig(BaseModel)`: `db: Path = "jobs.db"`, `backstory: Path = "backstory.md"`,
  `interval: float = Field(60, gt=0)`, `telegram`, `model`.
- `Config` (the runtime object used by `__main__`) is built from a `MainConfig` plus
  the optional `ProfesiaConfig` and `LinkedInConfig`. It keeps the field names the
  rest of the code uses today (`db`, `profesia`, `linkedin`, ...).

## Loading

`load_config(path)` in `profesia_monitor/config.py`:

1. `yaml.safe_load` reads `path`. A YAML syntax error becomes
   `ConfigError("<path>: invalid YAML (<line>: <problem>)")`.
2. `MainConfig.model_validate(data)`. Any `pydantic.ValidationError` is converted to a
   `ConfigError` (see Errors).
3. Relative `db`, `backstory` and `subscribers` paths resolve against the config's folder.
4. For each provider, if `<folder>/<name>.yaml` exists, its loader runs the same way
   (`ProfesiaConfig.model_validate`, `LinkedInConfig.model_validate`). A missing file leaves
   the source off.

Each provider package keeps a `load_config(path)` helper that does steps 1, 2 and the error
conversion for that file, so the provider and the main loader share one code path.

### Errors

`ConfigError` messages name the file and the field path, one line per problem:

```
data/profesia_sk.yaml: searches[0].remote_work: Input should be 0, 1 or 2
data/config.yaml: model.temperature: Input should be less than or equal to 2
```

Only the first error line is needed by the CLI, which reports the first problem and
exits, matching the current `parser.error` behaviour.

### Moved keys

If `config.yaml` contains a key that now belongs to a provider file (`searches`, `pages`,
`delay`, `count_days`, `retry`, `linkedin`), the error says so:
`config.yaml: 'searches' moved to profesia_sk.yaml`. This keeps the hint the current code
gives, and it is a dedicated check before the model runs so the message is specific.

## Dependencies

- `requirements.txt`: add `pydantic>=2.14,<3` and `pyyaml>=6`. Both are already in the venv
  (pydantic 2.14.1, PyYAML 6.0.3). Pydantic also arrives through langchain, but it is
  pinned here because the code imports it directly.

## Migration

A one-time script, `scripts/migrate_json_to_yaml.py` (run once, then deleted):

1. Reads `data/config.json`, `data/profesia_sk.json` and `data/linkedin.json` when they exist.
2. Writes the same values as `config.yaml`, `profesia_sk.yaml` and `linkedin.yaml`
   (keys in the same order, `$schema` dropped). Secrets are copied unchanged.
3. Moves the three JSON files to `data/json-backup/`. The folder is gitignored, and the
   user deletes it once the YAML works.
4. Validates each written file with the new models, so the migration fails loudly if a
   value does not fit the new rules.

The existing `data/config.pre-split.json` backup moves to `data/json-backup/` with the rest.

## Tests

Removed: `tests/test_config.py`'s schema assertions (`jsonschema`), and the `referencing`
registry code.

New or rewritten:

- Each model: a valid case, and one rejected case per rule (for example
  `pages: 0`, `remote_work: 5`, `max_pages: 11`, `retry.attempts: 0`, an unknown key).
- Error messages include the file name and field path.
- Each example file loads without error.
- A missing provider file turns that source off; a present one turns it on.
- The moved-key hint.
- A YAML syntax error names the file and line.
- The migration script on fixture JSON files: output values equal the input values.
- The existing behavioural tests (store, providers, run_once, Telegram) are unchanged
  except for the import paths they already use.

## Documentation

- README: configuration section and the `data/` tree use `.yaml` names and examples; the
  schema references are removed.
- `.gitignore`: `data/*` already covers the new files; the explicit provider lines change
  from `.json` to `.yaml`.

## Open items

None. All decisions were made during brainstorming.
