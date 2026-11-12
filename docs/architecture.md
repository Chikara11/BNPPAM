# Architecture

This document describes how the application is organised and the contracts
between its parts. Code must follow it. Any change to this document needs the
owner's approval first.

Related documents:
- `CLAUDE.md`: working agreement, business rules, exception rules.
- `docs/data_spec.md`: the three input files (single source of truth).
- `docs/output_spec.md`: the output tables, column by column, with types
  (single detailed definition of the exception format and of every output).
- `config.yaml`: every threshold, severity, allowed value and key.

---

## 1. Principles

1. **One source of computation.** All numbers come from the engine. The Excel
   report and the Streamlit app only display a `Results` object; they never
   recompute anything. They cannot disagree.
2. **The engine has no I/O.** It receives DataFrames and a config object and
   returns DataFrames. It never reads or writes files and never imports
   Streamlit. File reading lives in `loaders.py`, file writing in `report.py`.
3. **Configuration drives behaviour.** No threshold, severity, label, allowed
   value or key is hard-coded. Everything is read from `config.yaml` through
   `config.py`.
4. **Deterministic and explainable.** Same inputs + same config = same
   exceptions, in the same order. Every exception says which rule fired, on
   which file, row and field, and what value was observed.
5. **Never repair data.** The engine reports problems; it does not fill, drop,
   deduplicate or correct input rows.
6. **One anomaly = one exception.** Controls follow the exception rules in
   `CLAUDE.md` so that one bad value never produces several exceptions.

---

## 2. Project layout

```
securitised-credit-dq/
├── app.py                    # Streamlit UI: display only
├── config.yaml               # all parameters
├── requirements.txt          # pinned versions
├── pyproject.toml            # pytest settings only (pythonpath, testpaths)
├── dq/                       # the package, at the repo root (no src/)
│   ├── __init__.py
│   ├── config.py             # load and validate config.yaml
│   ├── loaders.py            # read the 3 Excel files, convert types
│   ├── schema.py             # output column names, dtypes and vocabulary
│   ├── context.py            # Context: config + reference date
│   ├── exceptions.py         # build exception rows in the common format
│   ├── controls/
│   │   ├── __init__.py       # CONTROL_REGISTRY
│   │   ├── common.py         # MISSING_VALUE, INVALID_VALUE, INVALID_PRICE
│   │   ├── positions.py
│   │   ├── prices.py
│   │   └── cashflows.py
│   ├── analytics.py          # price movements, upcoming cashflows,
│   │                         # data quality, summary
│   ├── engine.py             # run(): the only entry point
│   └── report.py             # write the Excel report from Results
├── scripts/
│   ├── generate_clean_data.py
│   ├── inject_anomalies.py
│   ├── generate_large_sample.py  # large sample: clean base + injection (reuses both)
│   └── run_report.py         # CLI: load -> run -> write report (run with -m)
├── tests/
│   ├── conftest.py
│   ├── helpers.py            # paths, config, small hand-built tables
│   ├── test_config.py
│   ├── test_loaders.py
│   ├── test_engine.py
│   ├── test_manifest.py
│   ├── test_controls.py
│   ├── test_analytics.py
│   ├── test_boundaries.py
│   ├── test_report.py
│   ├── test_app.py
│   └── test_large_sample.py
├── data/clean/   data/inputs/          # test fixture: clean base, injected
├── data/large_clean/   data/large/     # large sample: clean base, injected
├── output/                   # generated reports (git-ignored)
└── docs/                     # architecture.md, data_spec.md, output_spec.md,
                              # anomaly_manifest.xlsx, large_manifest.xlsx
```

`app.py` and the tests import `dq` directly (`import dq`): `streamlit run
app.py` puts the repo root on the path, and pytest gets it from
`pythonpath = ["."]` in `pyproject.toml`. A script run as
`python scripts/<name>.py` only has `scripts/` on its path and cannot import
`dq`, so a script that needs `dq` is run as a module from the repo root:
`python -m scripts.run_report --inputs data/inputs --output output/report.xlsx`
(no `sys.path` change, no install). The generator and the injector are
standalone and read `config.yaml` themselves.

---

## 3. Data flow

```
data/inputs/*.xlsx ──► loaders.load_inputs() ──► InputData
                                                    │
config.yaml ──► config.load_config() ──► Config ────┤
                                                    ▼
                                     engine.run(data, config)
                                       ├─ build Context (reference date)
                                       ├─ keep positions at the reference date
                                       ├─ run enabled controls  ──► exceptions
                                       └─ analytics             ──► price_movements,
                                                                    upcoming_cashflows,
                                                                    data_quality, summary
                                                    │
                                                    ▼
                                                 Results
                                      ┌─────────────┴─────────────┐
                                      ▼                           ▼
                           report.write_report()            app.py (Streamlit)
                           output/*.xlsx                    tables, charts, download
```

---

## 4. Modules and contracts

### 4.1 `config.py`
- `load_config(path: str | Path) -> Config`: reads the YAML
  (`yaml.safe_load`) and calls `config_from_dict`.
- `config_from_dict(raw: dict) -> Config`: validates a deep copy of `raw`
  and builds the `Config` (also used by tests to build a reduced config).
- `Config` is a frozen dataclass mirroring `config.yaml`; its mappings are
  read-only and it keeps the raw dict (`raw`) for `with_overrides` and the
  Parameters sheet.
- Validates on load: every control has `enabled`, `severity`, `label`;
  severities are Critical / Warning / Information; thresholds exist for each
  asset type; every required column has a type in `COLUMN_TYPES` (the
  column types of `docs/data_spec.md`, defined in `config.py` and used by
  the loader), so a new required column can never skip type checking.
  Stops with a clear `ConfigError` otherwise.
- `with_overrides(config, overrides: dict) -> Config` deep-merges the nested
  dict `overrides` (e.g. `{"price_move_thresholds_pct": {"ABS": 4.0}}`) into a
  deep copy of the raw config, re-validates it and returns a new `Config`
  (used by the app sidebar and by tests). The original is never mutated, and
  controls never mutate the config. A key in `overrides` that does not exist
  in the config raises `ConfigError` (a typo is never silently ignored).
  Dicts are merged; lists and scalars are replaced.

### 4.2 `loaders.py`
- `load_inputs(positions, previous_prices, cashflows, config) -> InputData`
  Each file argument is a path or a file-like object (Streamlit upload);
  `config` gives the required columns.
- `InputData` is a frozen dataclass with three DataFrames and
  `parse_issues`, a DataFrame defined in `docs/output_spec.md` section 0
  (File, SourceRow, Column, RawValue; `PARSE_ISSUE_COLUMNS` in `schema.py`).
- Rules:
  - every column is first read as `object` (raw cell values), then
    converted: text columns stay `object` (forced explicitly; pandas 3 would
    otherwise infer its `str` dtype), number columns become `float64`
    (Nominal included), date columns become `datetime64` normalised to
    midnight, in the unit pandas returns (`[us]` with the pinned version);
  - conversion is strict by cell type (no `pd.to_numeric` /
    `pd.to_datetime` coercion of text): text columns accept only text
    cells, number columns only numeric cells (not booleans), date columns
    only date cells. Any other non-empty value (e.g. `"98.5"` stored as
    text, `TRUE`, a number in a date column, a number in a text column)
    becomes missing in the DataFrame and is recorded in `parse_issues` with
    its raw value. It is reported by INVALID_VALUE (raw text as
    ObservedValue), never by a MISSING_* control;
  - documented normalisation: empty strings and whitespace-only cells
    become missing (`NaN` / `NaT` / `None`), never `""`. This is the only
    normalisation: the loader does **not** trim. A value with leading or
    trailing spaces is kept as is, and reported by INVALID_VALUE if it is
    not an allowed value;
  - required columns from `config.yaml`; a missing column raises
    `InputSchemaError` with the file and column name. A column name that
    appears twice in the header also raises `InputSchemaError` naming it
    (checked on the raw header, before pandas renames it `X.1`);
  - adds `SourceRow` (Int64): the Excel row number (header = row 1, first
    data row = 2), so every exception points to a real row. Empty rows in
    the middle of a sheet are kept, so the numbering stays right.
- Helpers used by the controls:
  - `parse_issues_of(data, file, column) -> pd.DataFrame`: the parse issues
    of one column;
  - `truly_missing(data, file, column) -> pd.Series` (bool): the cell is
    empty and has no parse issue (exception rule 10);
  - `empty_rows(data, file) -> pd.Series` (bool): every column of the row
    is empty (exception rule 11).
- The loader does not validate values. That is the controls' job.

### 4.3 `context.py`
- `Context` (frozen dataclass): `config`, `reference_date`, `horizon_end`,
  `ignored_position_rows`.
- `build_context(data, config) -> Context`: reference date = `config` value
  if set, else latest valid `ValuationDate` in positions. No valid
  `ValuationDate` and no config date raises `InputSchemaError` (no reference
  date to work with: file-level error). A config date with no position at
  that date is not an error: 0 positions, all rows ignored, shown in the
  summary.
- `ignored_position_rows`: position rows with a valid `ValuationDate`
  different from the reference date.
- `positions_at_reference_date(data, ctx) -> InputData`: keeps the
  positions dated at the reference date **and** those whose
  `ValuationDate` is empty or unparseable (so that MISSING_VALUE /
  INVALID_VALUE report them instead of dropping them silently).

### 4.4 `exceptions.py`
- `make_exceptions(rows, control_id, ctx, file, field, *, observed=None,
  comment) -> pd.DataFrame` builds one exception per row of `rows` (a
  subset of an input DataFrame); `observed` (Series aligned on `rows`, or
  None = empty) gives ObservedValue and `comment` (text or Series) the
  Comment. Rows follow the common exception format: the `CLAUDE.md` list with
  `ExceptionID` first and `SourceRow` last. Names, order and dtypes are
  defined once, in `docs/output_spec.md`. Severity and label always come
  from `ctx.config`. `Source` is the input file of the row (`positions`,
  `previous_prices` or `cashflows`).
- Multi-row exceptions (duplicates): `SourceRow` = first row; `Comment`
  lists all the rows. Built by `make_duplicate_exceptions` (one exception
  per duplicated key, rule 5).
- `combine_exceptions(frames) -> pd.DataFrame` concatenates the exceptions
  of several checks (empty frames skipped) in the common format.
- Duplicates and unmatched cashflows: `Field` = the key columns joined with
  `+` (e.g. `ValuationDate+Portfolio+ISIN`).
- Price movement exceptions are raised on the position row:
  `Source = positions`, `Field = MarketPrice`.
- `empty_exceptions() -> pd.DataFrame` returns an empty frame with the right
  columns and dtypes. It is defined in `schema.py` (with the other output
  schemas) and used by `exceptions.py` and the controls.
- `ExceptionID` is assigned once, by the engine, after sorting:
  `EXC-0001`, `EXC-0002`, ... (zero-padded to 4 digits).

### 4.5 `controls/`
Every control is a function with the same signature:

```python
def check_<name>(data: InputData, ctx: Context) -> pd.DataFrame:
    """Return exceptions in the common format, or an empty frame."""
```

Rules for a control:
- pure: reads `data`, never modifies it;
- receives the whole `InputData`, so cross-file checks are allowed;
- one control ID = one registered function (it may call helpers);
- reads its parameters from `ctx.config` only;
- returns one row per exception, following the exception rules of
  `CLAUDE.md`;
- does not know about other controls; overlap is prevented by the rules,
  not by checking what another control returned;
- identifies rows by `SourceRow`, never by the DataFrame index: the engine
  filters positions without renumbering them, so the index is not a row
  number. The same applies to analytics, the report and the app.

Placement:
- `controls/common.py`: MISSING_VALUE, INVALID_VALUE, INVALID_PRICE, and the
  `evaluated_rows` (rules 1, 11, 12; for previous prices, securities held
  only), the row scope of every non-duplicate control. The lower-level row
  helpers live in `analytics.py`, which controls and analytics both use
  (analytics never imports the controls): `valid_isin` (rule 1),
  `duplicate_candidates` / `first_occurrence` (rule 12), `held_isins`,
  `matching_positions` (one position per Portfolio + ISIN: valid ISIN,
  complete key, first occurrence of a duplicated position, rules 1, 4
  and 12) and `position_values` (the value of a position column for each
  cashflow: SecurityName, AssetType, MaturityDate, Currency);
- `controls/cashflows.py`: UNMATCHED_CASHFLOW, CURRENCY_MISMATCH and the
  other cashflow controls (DUPLICATE_CASHFLOW, PAST_FORECAST_CASHFLOW);
- `controls/prices.py`: price controls (PRICE_MOVE_ABOVE_THRESHOLD,
  NO_PRICE_HISTORY, DUPLICATE_PREVIOUS_PRICE);
- `controls/positions.py`: the rest (MISSING_ISIN, DUPLICATE_POSITION,
  MISSING_MARKET_PRICE, INVALID_NOMINAL, MISSING_RATING).

`controls/__init__.py` holds the registry:

```python
CONTROL_REGISTRY: dict[str, Callable[[InputData, Context], pd.DataFrame]] = {
    "MISSING_ISIN": positions.check_missing_isin,
    ...
}
```

The engine runs every control that is in the registry **and** enabled in
`config.yaml`, in the order of `config.yaml`. A control in the config but
not in the registry (or the reverse) raises an error at start-up.

### 4.6 `analytics.py`
Pure functions, same inputs as controls, returning the non-exception outputs:
- `price_movements(data, ctx)`: one row per position, with the move when it
  is computable and the reason when it is not;
- `upcoming_cashflows(data, ctx)`: cashflows with
  `reference_date <= CashflowDate <= reference_date + horizon` (both
  inclusive) and `Status != Paid`, flagged matched / unmatched;
- `data_quality(data, ctx)`: per file and column, completeness. A
  `NotApplicable` column counts cells where a value is not expected (an
  empty Rating on CLO Equity); completeness is computed on applicable rows
  only;
- `summary(exceptions, data, ctx)`: counts by severity, control, portfolio,
  and the headline figures of the executive summary, including
  "Position rows not at the reference date: N (ignored)" (the rows removed
  by the engine filter of section 4.7; information only, no exception).

Amounts are always totalled per currency, never summed across currencies
(no FX conversion), in the summary, the app and the report.

Formulas (price change, market value) are written once here and reused;
the price change formula and its rounding are those of `CLAUDE.md`.

### 4.7 `engine.py`
- `run(data: InputData, config: Config) -> Results` is the only entry point.
- Steps: check the registry → build context → keep only the positions with
  `ValuationDate == reference date`, plus those with an empty or
  unparseable `ValuationDate` (section 4.3; previous prices and cashflows
  are not filtered; the number of position rows ignored is reported in the
  summary, never silently) → run enabled controls in the order of
  `config.yaml` → concatenate → sort (Severity, ControlID, Portfolio, ISIN,
  SourceRow, Field; empty values last) →
  assign `ExceptionID` → run analytics (on the same filtered positions) →
  return `Results`.
- Severity is sorted as an ordered categorical: Critical > Warning >
  Information (not alphabetically).
- `Results` (frozen dataclass): `reference_date`, `config`, `exceptions`,
  `price_movements`, `upcoming_cashflows`, `data_quality`, `summary`.

### 4.8 `report.py`
- `write_report(results: Results, path_or_buffer, *, generated_at=None) -> None`
  `generated_at` is the run timestamp shown on the Executive Summary
  (default: now, to the second); tests pass a fixed value so the file is
  deterministic.
- Sheets: Executive Summary, Exceptions, Price Movements, Cashflows {N}D
  (N = `cashflow_horizon_days` of the config used, e.g. "Cashflows 30D"),
  Data Quality, Parameters (the config used, for audit: one row per
  setting, dotted path and YAML spelling, e.g. `controls.MISSING_ISIN.enabled`
  = `true`).
- The Executive Summary shows the reference date, the run timestamp, then
  `Results.summary` section by section, including "Position rows not at the
  reference date (ignored)".
- Formatting only (headers, number formats, column widths, severity tint on
  the Severity cell, breach rows highlighted, frozen header, filters). No
  calculation. Sheet names, number formats and colours are presentation
  constants defined once in `report.py` (output_spec.md section 6).
- Every text cell is stored as plain text, never as a formula: a value such
  as `=1+1` coming from the data cannot run in Excel.
- Works with a file path (CLI) or a `BytesIO` (Streamlit download).
- `scripts/run_report.py` (`python -m scripts.run_report`, options
  `--inputs`, `--output`, `--config`): load -> run -> write, then prints the
  reference date, the exceptions by severity (from `Results.summary`) and
  the report path. A `ConfigError`, `InputSchemaError`, `RegistryError` or
  an unwritable output prints a readable message and exits with code 1,
  never a traceback.

### 4.9 `app.py`
- Run with `streamlit run app.py` from the repo root.
- Sidebar: data source "Test fixture (data/inputs)" (default), "Large
  sample (data/large)" or "Upload the 3 files" (nothing runs until all 3
  are there); thresholds per asset type, horizon and one
  checkbox per control, applied through `with_overrides` (every value is
  passed and re-validated); "Reset to config.yaml" clears them. No
  reference-date override. The engine re-runs on every change.
- Tabs: Summary (severity metrics, exceptions-by-control chart, every
  `Results.summary` section, including "Position rows not at the reference
  date (ignored)"), Exceptions (filters by severity, control, portfolio;
  they only hide rows), Price Movements, Cashflows {N}D, Data Quality.
- Price Movements chart: one dot strip per asset type (the thresholds
  differ), one dot per computed position on the x axis (change in %),
  breach / within threshold colours with a legend, dashed ±threshold lines,
  symmetric axis. The vertical spread is a fixed function of the Excel row
  (no meaning, no randomness), so close dots stay visible; the same chart
  reads at 50 or 500 positions. The hover names Portfolio · ISIN, with the
  Excel row for a duplicated position.
- Same look as the report: the severity and breach tints and the column
  formats come from `report.py` (`SEVERITY_TINTS`, `BREACH_TINT`,
  `COLUMN_FORMATS`), translated to Streamlit syntax.
- Download button: the Excel report built by `report.write_report` with the
  current sidebar settings.
- Config, input and registry errors are shown with `st.error`, never a
  traceback.
- No business logic in `app.py`: every number comes from `Results`.
  Caching with `st.cache_data` on loading only (key = the file bytes).

---

## 5. Errors

| Error | Raised by | Meaning |
|---|---|---|
| `ConfigError` | `config.py` | config.yaml incomplete or inconsistent |
| `InputSchemaError` | `loaders.py`, `context.py` | missing column, unreadable file, or no reference date |
| `RegistryError` | `engine.py` | config and registry do not match |

Bad **values** in the data never raise: they become exceptions.
The app shows errors as a readable message, never as a traceback.

---

## 6. Adding or changing a control (live modification guide)

1. Write `check_<name>` in the `controls/` file given by the placement rule
   of section 4.5.
2. Register it in `CONTROL_REGISTRY`.
3. Add it to `config.yaml` with `enabled`, `severity`, `label`; its position
   in `config.yaml` is its run order.
4. Add a unit test in `tests/test_controls.py` (one firing case, one
   non-firing case).
5. If it changes the expected results on `data/inputs/`, update the manifest
   through `scripts/inject_anomalies.py`, never by hand.
6. Run `pytest -q`.

Changing a threshold or a severity = edit `config.yaml` only.

---

## 7. Testing

- `helpers.py` holds the repo paths, the real config (`CONFIG`) and the
  small hand-built tables of the unit tests (`make_data`, `position`,
  `price`, `cashflow`, `empty_row`, `parse_issue`, `raised`).
- `conftest.py` loads `data/clean/` and `data/inputs/` and runs the engine
  on them once per session, with the real config.
- `test_config.py`, `test_loaders.py`, `test_engine.py`: config validation
  and overrides, loader conversions and errors, registry / sort / IDs.
- `test_analytics.py`: the non-exception outputs (price movements,
  upcoming cashflows, data quality, summary) and their schemas.
- `test_manifest.py`: the manifest is the oracle (exact control, severity
  and count per case; total on inputs; zero on clean data). An exception
  matches a case on ControlID + Portfolio + ISIN, ignoring the manifest
  `File` column; A01 (no ISIN) matches on ControlID + Portfolio.
- `test_controls.py`: one small hand-built DataFrame per case.
- `test_boundaries.py`: thresholds at the limit, config overrides, disabled
  controls.
- `test_report.py`: the Excel report read back with openpyxl (sheets,
  headers, rows, number formats, colours, plain text against formula
  injection, Executive Summary, Parameters) and the `run_report` CLI
  (a real `python -m` run, readable errors with exit code 1).
- `test_large_sample.py`: the large sample against `docs/large_manifest.xlsx`
  (clean base 0 exceptions, exact multiset of ControlID + Portfolio + ISIN,
  totals and severities, two-problem rows, near-boundary cases, engine run
  under 5 s) and the "Datasets" tables of data_spec.md against the
  manifests and the files.
- `test_app.py`: the app driven by `streamlit.testing.v1.AppTest` (sample
  and real uploads, sidebar settings, reset, filters, errors, chart
  content read from the figure spec, column formats and tints).
- Tests never write files outside a temporary directory.

---

## 8. Out of scope

- Persistence, databases, user accounts.
- Real market data or real identifiers.
- Any LLM call inside the application.