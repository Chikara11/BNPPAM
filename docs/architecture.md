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
│   ├── schema.py             # output column names and dtypes
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
│   └── run_report.py         # CLI: load -> run -> write report
├── tests/
│   ├── conftest.py
│   ├── test_manifest.py
│   ├── test_controls.py
│   └── test_boundaries.py
├── data/clean/   data/inputs/
├── output/                   # generated reports (git-ignored)
└── docs/                     # architecture.md, data_spec.md, output_spec.md,
                              # anomaly_manifest.xlsx
```

`app.py`, `scripts/` and the tests import `dq` directly (`import dq`): the
repo root is on the path when running from it, and pytest gets it from
`pythonpath = ["."]` in `pyproject.toml`.

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
- `load_config(path: str | Path) -> Config`
- `Config` is a frozen dataclass mirroring `config.yaml`.
- Validates on load: every control has `enabled`, `severity`, `label`;
  severities are Critical / Warning / Information; thresholds exist for each
  asset type. Stops with a clear `ConfigError` otherwise.
- `with_overrides(config, overrides: dict) -> Config` deep-merges the nested
  dict `overrides` (e.g. `{"price_move_thresholds_pct": {"ABS": 4.0}}`) into a
  deep copy of the raw config, re-validates it and returns a new `Config`
  (used by the app sidebar and by tests). The original is never mutated, and
  controls never mutate the config.

### 4.2 `loaders.py`
- `load_inputs(positions, previous_prices, cashflows) -> InputData`
  Each argument is a path or a file-like object (Streamlit upload).
- `InputData` is a dataclass with three DataFrames and `parse_issues`, a
  DataFrame with columns file, SourceRow, column, raw value.
- Rules:
  - every column is first read as `object` (raw values), then converted:
    text columns stay `object` (forced explicitly; pandas 3 would otherwise
    infer its `str` dtype), number columns become `float64` (Nominal
    included), date columns become `datetime64` normalised to midnight, in
    the unit pandas returns (`[us]` with the pinned version);
  - conversion uses `errors="coerce"`. A non-empty value that fails the
    conversion becomes missing in the DataFrame and is recorded in
    `parse_issues` with its raw value. It is reported by INVALID_VALUE (raw
    text as ObservedValue), never by a MISSING_* control;
  - documented normalisation: empty strings and whitespace-only cells
    become missing (`NaN` / `NaT` / `None`), never `""`. This is the only
    normalisation: the loader does **not** trim. A value with leading or
    trailing spaces is kept as is, and reported by INVALID_VALUE if it is
    not an allowed value;
  - required columns from `config.yaml`; a missing column raises
    `InputSchemaError` with the file and column name;
  - adds `SourceRow` (int): the Excel row number (header = row 1, first data
    row = 2), so every exception points to a real row.
- The loader does not validate values. That is the controls' job.

### 4.3 `context.py`
- `Context` (frozen dataclass): `config`, `reference_date`, `horizon_end`.
- `build_context(data, config) -> Context`: reference date = `config` value
  if set, else latest `ValuationDate` in positions.

### 4.4 `exceptions.py`
- `make_exceptions(rows, control_id, ctx, file, field) -> pd.DataFrame`
  builds rows in the common exception format: the `CLAUDE.md` list with
  `ExceptionID` first and `SourceRow` last. Names, order and dtypes are
  defined once, in `docs/output_spec.md`. Severity and label always come
  from `ctx.config`. `Source` is the input file of the row (`positions`,
  `previous_prices` or `cashflows`).
- Multi-row exceptions (duplicates): `SourceRow` = first row; `Comment`
  lists all the rows.
- Duplicates and unmatched cashflows: `Field` = the key columns joined with
  `+` (e.g. `ValuationDate+Portfolio+ISIN`).
- Price movement exceptions are raised on the position row:
  `Source = positions`, `Field = MarketPrice`.
- `empty_exceptions() -> pd.DataFrame` returns an empty frame with the right
  columns and dtypes.
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
  not by checking what another control returned.

Placement:
- `controls/common.py`: MISSING_VALUE, INVALID_VALUE, INVALID_PRICE;
- `controls/cashflows.py`: UNMATCHED_CASHFLOW, CURRENCY_MISMATCH and the
  other cashflow controls (DUPLICATE_CASHFLOW, PAST_FORECAST_CASHFLOW);
- `controls/prices.py`: price controls (PRICE_MOVE_ABOVE_THRESHOLD,
  NO_PRICE_HISTORY);
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
- Steps: build context → keep only the positions with
  `ValuationDate == reference date` (previous prices and cashflows are not
  filtered; the number of position rows ignored is reported in the summary,
  never silently) → run enabled controls in the order of `config.yaml` →
  concatenate → sort (Severity, ControlID, Portfolio, ISIN, SourceRow) →
  assign `ExceptionID` → run analytics (on the same filtered positions) →
  return `Results`.
- Severity is sorted as an ordered categorical: Critical > Warning >
  Information (not alphabetically).
- `Results` (frozen dataclass): `reference_date`, `config`, `exceptions`,
  `price_movements`, `upcoming_cashflows`, `data_quality`, `summary`.

### 4.8 `report.py`
- `write_report(results: Results, path_or_buffer) -> None`
- Sheets: Executive Summary, Exceptions, Price Movements, Cashflows {N}D
  (N = `cashflow_horizon_days` of the config used, e.g. "Cashflows 30D"),
  Data Quality, Parameters (the config used, for audit).
- The Executive Summary shows "Position rows not at the reference date:
  N (ignored)" from `Results.summary`.
- Formatting only (headers, number formats, column widths, severity colours,
  frozen header, filters). No calculation.
- Works with a file path (CLI) or a `BytesIO` (Streamlit download).

### 4.9 `app.py`
- Upload the 3 files or use the bundled `data/inputs/` sample.
- Sidebar: thresholds, horizon and enabled controls, applied through
  `with_overrides`; the engine re-runs on change.
- Pages / tabs: Summary, Exceptions (filters by severity, control,
  portfolio), Price Movements (chart), Cashflows {N}D, Data Quality.
  The Summary shows "Position rows not at the reference date: N (ignored)".
- Download button: the Excel report built by `report.write_report`.
- No business logic in `app.py`. Caching with `st.cache_data` on loading.

---

## 5. Errors

| Error | Raised by | Meaning |
|---|---|---|
| `ConfigError` | `config.py` | config.yaml incomplete or inconsistent |
| `InputSchemaError` | `loaders.py` | missing column or unreadable file |
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

- `conftest.py` loads the config, `data/clean/` and `data/inputs/` once per
  session.
- `test_manifest.py`: the manifest is the oracle (exact control, severity
  and count per case; total on inputs; zero on clean data). An exception
  matches a case on ControlID + Portfolio + ISIN, ignoring the manifest
  `File` column; A01 (no ISIN) matches on ControlID + Portfolio.
- `test_controls.py`: one small hand-built DataFrame per case.
- `test_boundaries.py`: thresholds at the limit, config overrides, disabled
  controls.
- Tests never write files outside a temporary directory.

---

## 8. Out of scope

- Persistence, databases, user accounts.
- Real market data or real identifiers.
- Any LLM call inside the application.