# Project: CLO / ABS Portfolio Monitoring Prototype

## Working agreement (read first)
I own the design and every business decision; Claude assists with implementation.
All changes are reviewed before they are kept (four-eyes principle).

- For any non-trivial task, propose a short plan first (files, functions,
  edge cases) and wait for my approval before writing code.
- One function or one rule per iteration. Never change several modules at once.
- After each change, summarise in max 3 lines what changed and why.
- If a requirement is ambiguous, ask instead of assuming. State every
  assumption explicitly so it can be documented in the README.
- Flag weaknesses in my approach (missing edge case, questionable rule).
- No new dependency, no commit, no file deletion without my approval.
- Never let an LLM determine financial calculations, exception existence
  or severity: all of these are deterministic code driven by config.yaml.

## Objective
Build a prototype that imports three Excel files, validates and processes
portfolio data with Python, displays results in Streamlit, and generates an
Excel reporting workbook.

## Business context
The prototype simulates the monitoring of CLO Debt, CLO Equity and ABS
securities held in several investment portfolios.
This is a synthetic demonstration, not a production financial system.

## Data policy
- All records are fictional and generated locally.
- Never use real portfolio, market or client data or any confidential information.
- Synthetic identifiers: 12 characters, `SYN` + 9 digits (e.g. SYN000000001),
  same length as an ISIN but clearly fictional.
- Two datasets, one design (docs/data_spec.md "Datasets"), same file
  contract, invariants and config.yaml:
  - test fixture: `data/clean/` -> `data/inputs/`, oracle
    `docs/anomaly_manifest.xlsx`, seed 42, ISINs `SYN000000001`...; one
    isolated anomaly per control, the oracle of the unit and end-to-end
    tests;
  - large sample: `data/large_clean/` -> `data/large/`, oracle
    `docs/large_manifest.xlsx`, seed 43, ISINs `SYN001000001`...; a
    production-like feed (500 positions, anomalies weighted like
    production, two-problem rows, near-boundary cases).
- Keep clean data generation separate from anomaly injection. Each clean
  base must raise zero exceptions; clean price movements are truncated
  below the thresholds (see docs/data_spec.md).
- Counts in tests and docs are computed from the manifests and the files,
  never typed.
- Never silently repair or overwrite source data: detect and report only.

## Input files
`docs/data_spec.md` is the single source of truth for the three input files:
columns, exact types, constraints, keys, relationships, examples and the
invariants of the clean dataset. Read it before touching the generator or the
engine, and update it first if a column or rule changes.

1. `positions.xlsx`: ValuationDate, Portfolio, ISIN, SecurityName, AssetType,
   Currency, Nominal, BookPrice, MarketPrice, Rating, MaturityDate
2. `previous_prices.xlsx`: ISIN, PreviousDate, PreviousPrice, PriceSource
3. `cashflows.xlsx`: Portfolio, ISIN, CashflowDate, CashflowType, Currency,
   Amount, Status

Missing required columns must stop processing with a clear message naming them.

## Configuration
All thresholds, severities, the cashflow horizon, required columns and
duplicate keys live in `config.yaml`. Never hardcode them in the code.

## Financial assumptions
- Prices are quoted per 100 nominal.
- MarketValue = Nominal * MarketPrice / 100.
- BookValue = Nominal * BookPrice / 100.
- PriceChange = MarketPrice - PreviousPrice.
- PriceChangePct = PriceChange / PreviousPrice * 100.
- Previous price = latest PreviousPrice strictly before the reference date
  (= latest ValuationDate by default).
- Price alerts use the absolute percentage movement and trigger when
  |PriceChangePct| is strictly greater than the threshold.
- Default thresholds: 5% for CLO Debt and ABS, 15% for CLO Equity.
- CLO Equity may legitimately have no rating.
- Amounts are never summed across currencies (no FX conversion): totals are
  always per currency, in the summary, the app and the report.

## Reference date
- Reference date = latest ValuationDate in positions.xlsx (overridable in config.yaml).
- "Past-dated" means CashflowDate strictly before the reference date.
- Upcoming cashflows: CashflowDate between the reference date and
  reference date + 30 days (inclusive), Status different from Paid.

## Data quality rules
Detect:
- Missing required identifiers and values.
- Duplicate positions (key: ValuationDate + Portfolio + ISIN). The same ISIN
  held in two different portfolios is not a duplicate. Controls use only the
  positions dated at the reference date.
- Missing current market prices.
- Invalid or non-positive nominal and prices.
- Missing ratings where a rating is expected (CLO Debt, ABS).
- Securities without previous pricing history: no previous_prices row at
  all for the ISIN. Rows that exist but are unusable (empty, unparseable or
  future PreviousDate) are reported only by their own date control.
- Duplicate previous prices (key: ISIN + PreviousDate), on securities held.
- Asset-specific price movements above thresholds.
- Cashflows without a matching position (key: Portfolio + ISIN).
- Duplicate cashflows (key: Portfolio + ISIN + CashflowDate + CashflowType + Amount).
- Past-dated cashflows still marked Forecast.
- Cashflow currency different from its position currency.
- Values outside the allowed lists of config.yaml.
- Cashflow amount with the wrong sign: Coupon and Principal > 0, Fee < 0
  (`amount_sign` in config.yaml, data_spec.md section 3), INVALID_VALUE.
- ISIN not matching `isin_pattern` of config.yaml (`^SYN\d{9}$`, data_spec.md).
- Invalid dates: MaturityDate on or before the reference date, cashflow
  dated after the MaturityDate of its position, PreviousDate on or after
  the reference date.

Do not classify a missing previous price as a missing current market price.
Do not compute a price movement when the current or previous price is
missing or non-positive (already reported by another rule).

## Exception rules (one anomaly = one exception)
The anomaly manifest expects exactly one exception per injected anomaly.
The engine follows these rules:
1. A position without ISIN raises only MISSING_ISIN: no other ISIN-based
   control (price history, price movement, duplicate key) runs on that row.
   Controls that do not depend on the ISIN still run on it: a missing ISIN
   and, for example, a negative Nominal are two different problems and
   raise two exceptions.
   An ISIN that is present but does not match `isin_pattern` (config.yaml)
   is treated like a missing ISIN, in all 3 files: it raises only
   INVALID_VALUE and every ISIN-based control skips the row (duplicate keys,
   price history and movement, cashflow matching, currency and maturity
   checks). In previous_prices every control is ISIN-based, since prices of
   securities not held are ignored: a price row with an empty or badly
   formatted ISIN raises only that ISIN exception.
2. Only ISIN, MarketPrice and Rating have a dedicated "missing" control
   (MISSING_ISIN, MISSING_MARKET_PRICE, MISSING_RATING). MISSING_VALUE
   covers every other required field, including Nominal, BookPrice and
   PreviousPrice. INVALID_VALUE on allowed values ignores empty cells: an
   empty cell is only ever reported by a MISSING_* control (otherwise an
   emptied Rating would raise both MISSING_RATING and INVALID_VALUE).
3. An empty Nominal, BookPrice or PreviousPrice is MISSING_VALUE, not
   invalid: INVALID_NOMINAL and INVALID_PRICE apply only to a value that
   is present and <= 0.
4. Cashflow matching checks that Portfolio + ISIN exists in the positions;
   it never joins row by row, so a duplicated position does not duplicate
   cashflows. Reports state that a duplicated position is counted twice
   in totals.
5. Duplicates (positions, previous prices, cashflows) raise one exception
   per duplicated key, not one per row.
6. CURRENCY_MISMATCH is checked only on cashflows that match a position.
7. PriceChangePct = (MarketPrice - PreviousPrice) / PreviousPrice * 100,
   in this order, rounded to 6 decimals before the comparison with the
   threshold (so that exactly 5 % never triggers through a floating-point
   residue). No movement is computed when MarketPrice or the latest
   PreviousPrice is missing or <= 0 (no fallback to an older price).
   When the latest PreviousDate has several rows (DUPLICATE_PREVIOUS_PRICE),
   the movement uses their price if it is the same on every row, and is not
   computed if the prices differ.
8. INVALID_PRICE covers MarketPrice, BookPrice and PreviousPrice <= 0:
   one exception per invalid field, with the field named in the Field
   column of the exception.
9. Invalid dates raise INVALID_VALUE:
   - MaturityDate <= reference date;
   - CashflowDate > MaturityDate of its position. Checked only on
     cashflows that match a position (Portfolio + ISIN): an unmatched
     cashflow has no maturity to compare with and raises only
     UNMATCHED_CASHFLOW, as in rule 6;
   - PreviousDate >= reference date. Checked only on prices of securities
     held in positions: prices of securities not held are ignored by the
     engine (docs/data_spec.md section 2).
10. A non-empty value that cannot be converted to its type (e.g. text in a
    number or date column) is recorded by the loader in `parse_issues` and
    raises only INVALID_VALUE, with the raw text as ObservedValue. MISSING_*
    controls report only truly empty cells (empty, empty string or
    whitespace only).
11. A fully empty row (every column of the sheet empty) is reported once,
    by INVALID_VALUE with Field `row` and Comment `Empty row`, and is
    excluded from every other control (otherwise it would raise one
    MISSING_* exception per column).
12. When rows share a duplicate key (positions, previous prices,
    cashflows), only the duplicate control reports the extra rows; every
    other control evaluates the first occurrence only (lowest SourceRow).
    A duplicated past Forecast cashflow gives one DUPLICATE_CASHFLOW and
    one PAST_FORECAST_CASHFLOW. Rows that the duplicate control does not
    compare (incomplete key, invalid ISIN) are always evaluated.

## Data quality dimensions (configurable)
Each control belongs to one dimension (`controls.<ID>.dimension` in
config.yaml, validated at load against the `dimensions` list):
- Completeness: MISSING_ISIN, MISSING_MARKET_PRICE, MISSING_RATING, MISSING_VALUE.
- Validity: INVALID_NOMINAL, INVALID_PRICE, INVALID_VALUE (which also covers
  invalid dates and empty rows: documented limitation, output_spec.md 4b).
- Uniqueness: DUPLICATE_POSITION, DUPLICATE_CASHFLOW, DUPLICATE_PREVIOUS_PRICE.
- Consistency: UNMATCHED_CASHFLOW, CURRENCY_MISMATCH, NO_PRICE_HISTORY.
- Timeliness: PAST_FORECAST_CASHFLOW.
- Plausibility: PRICE_MOVE_ABOVE_THRESHOLD (a review alert, not a data error).
Every exception carries its Dimension; the summary and the Data Quality
sheet / tab count exceptions per dimension (zeros included).

## Severity (defaults, configurable)
Critical:
- Missing ISIN.
- Duplicate position.
- Missing current market price.
- Invalid nominal or invalid price.

Warning:
- Significant price movement.
- Missing expected rating.
- Unmatched or duplicate cashflow.
- Duplicate previous price (DUPLICATE_PREVIOUS_PRICE).
- Past-dated Forecast cashflow.
- Other required value missing (MISSING_VALUE).
- Value outside an allowed list, or invalid date (INVALID_VALUE).

Information:
- New security without previous price history.
- Cashflow currency different from position currency.

## Architecture
- pandas for data processing, openpyxl for Excel formatting, Streamlit for the UI.
- Allowed dependencies (pinned in requirements.txt, Python 3.12): pandas,
  numpy, openpyxl, pyyaml, pytest, streamlit, plotly. Any other dependency
  needs my approval.
- pandas 3 infers a new string dtype for text columns. Every Excel loader
  (generator read-back and engine) reads text columns as `object` (plain
  Python strings) and numeric columns as `float64`, so that missing values
  and type checks behave the same everywhere.
- Separate modules: loading, validation, business rules, reporting, UI.
  Package `dq/` at the repo root; structure and contracts in
  `docs/architecture.md`.
- One rule = one function returning exceptions in a common format:
  ExceptionID, Severity, ControlID, Dimension, AnomalyType, Source,
  Portfolio, ISIN, SecurityName, Field, ObservedValue, Comment, SourceRow.
  `docs/output_spec.md` is the single detailed definition (names, order,
  dtypes).
- Keep calculations and exception detection deterministic.
- Readable code over clever code: short functions, explicit names, no unnecessary classes.
## Code quality rules

Readability
- Code is written for a reviewer who has never seen it: clear names, no
  abbreviations except domain terms (ISIN, VD, CLO, ABS).
- One function = one job. If a function needs "and" to describe it, split it.
  Target: under ~40 lines per function.
- Type hints on every public function. A docstring only on the contract
  functions named in docs/architecture.md (load_config, config_from_dict,
  with_overrides, load_inputs, truly_missing, empty_rows, build_context,
  positions_at_reference_date, make_exceptions, empty_exceptions, the
  check_* controls, the analytics outputs, engine.run, report.write_report):
  what it does, what it returns, which CLAUDE.md rule it applies.
  Everywhere else: short comments that explain WHY.
- Comments explain WHY (a business rule, a pandas pitfall), never WHAT the
  next line does.

No duplication
- Every business formula (price change, market value, horizon, keys) exists
  in exactly one place and is imported everywhere else.
- No copy-pasted control logic: shared patterns (duplicates, matching,
  first occurrence, valid ISIN) live in shared helpers.
- No hard-coded threshold, severity, label, allowed value, key, column list
  or pattern: they come from config.yaml or dq/schema.py.

No bloat
- No dead code, unused imports, unused parameters, commented-out code or
  speculative features "for later".
- No wrapper that only calls another function, no class where a function is
  enough, no abstraction used only once unless it makes a rule explicit.
- Prefer vectorised pandas over row loops; use a loop only when it is clearly
  more readable and the data is small, and say why.

Correctness and safety
- Pure functions in the engine: no I/O, no global state, no mutation of
  inputs (pandas copy-on-write; use .loc, never chained assignment).
- Errors: raise the project's own exceptions (ConfigError, InputSchemaError,
  RegistryError) with a message naming the file, column or control.
  Never a bare except, never a silent pass.
- Deterministic output: explicit sort keys, stable sorts, no reliance on
  dict or set ordering for results.

Tests
- Every rule has a test that fails if the rule is removed. Test names say
  the behaviour: test_<control>_<case>_<expected>.
- Tests use small hand-built tables (tests/helpers.py) for unit cases and
  the manifest for end-to-end cases. No test depends on another test.

Before every commit
- pytest -q -W error passes.
- No new warning, no new dependency, no file outside the agreed layout.


## Testing
- `docs/anomaly_manifest.xlsx` lists every injected anomaly and its expected
  result; it is the test oracle.
- Every expected anomaly must be detected with the expected severity,
  and no unexpected exception may be raised (no false positives).
- Test positive and negative price movements.
- Test threshold boundaries (exactly 5% must not trigger).
- Test a movement below threshold that must not trigger (e.g. +10% on CLO Equity).
- Test legitimate unrated CLO Equity.
- Test the same ISIN in different portfolios.
- Test missing previous prices separately from missing current market prices.
- Test that changing a threshold in config.yaml changes the result.

## Commands
- Generate clean data: `python scripts/generate_clean_data.py` → `data/clean/`
- Inject anomalies:    `python scripts/inject_anomalies.py` → `data/inputs/`
  (deliverable input files) and `docs/anomaly_manifest.xlsx`
- Large sample:        `python scripts/generate_large_sample.py` →
  `data/large_clean/`, `data/large/` and `docs/large_manifest.xlsx`
- Run tests:           `pytest -q`
- Run the report:      `python -m scripts.run_report --inputs data/inputs --output output/report.xlsx`
  (from the repo root, as a module, so that it can import `dq`)
- Run the app:         `streamlit run app.py`

Run the tests after every change and show the result.

## Development workflow
1. Agree on the data schema and assumptions.
2. Generate clean synthetic datasets and check that they raise no exception.
3. Inject controlled anomalies and generate the anomaly manifest.
4. Write the tests from the manifest (they fail at this stage).
5. Build the processing engine, one rule at a time, until all tests pass.
6. Build the Excel report.
7. Build the Streamlit application.
8. Deploy on Streamlit Community Cloud.
9. Finalise documentation (README, AI usage note).

## AI-assisted development log
For traceability, after each validated step append an entry to `AI_LOG.md`:

```
### <date> – <step>
- Request: <summary of my prompt>
- Proposal: <summary of Claude's proposal>
- Decision: accepted / modified / rejected, and why
- Verification: <tests run, manual recalculation, review>
```

Never delete or rewrite existing entries, especially the corrections.

`AI_LOG.md` is a local file, ignored by git (`.gitignore`) and removed from
the history: keep writing entries in it, never stage or commit it, until I
decide otherwise.

## Git
One commit per validated step, with a clear message. Commit only after my approval.
Never stage `AI_LOG.md`.


## Environment and libraries

- Python **3.12.15**, virtual environment in `.venv/`.
- Pinned versions (`requirements.txt`). Do not upgrade, downgrade or add a
  package without my approval.

| Package | Version | Used for | Documentation |
|---|---|---|---|
| pandas | 3.1.1 | data loading and controls | https://pandas.pydata.org/docs/ |
| numpy | 2.5.4 | numeric operations | https://numpy.org/doc/stable/ |
| openpyxl | 3.1.5 | Excel read/write and report formatting | https://openpyxl.readthedocs.io/en/stable/ |
| PyYAML | 6.0.3 | config.yaml (`yaml.safe_load` only) | https://pyyaml.org/wiki/PyYAMLDocumentation |
| pytest | 9.2.0 | tests | https://docs.pytest.org/en/stable/ |
| streamlit | 1.67.0 | web app | https://docs.streamlit.io/ |
| plotly | 7.1.0 | charts in the app | https://plotly.com/python/ |

Python reference: https://docs.python.org/3.12/
Deployment: https://docs.streamlit.io/deploy/streamlit-community-cloud

Rules:
- When unsure about an API, check the documentation of the pinned version.
  Do not rely on memory of older versions.
- pandas 3: copy-on-write is the default (no chained assignment such as
  `df[col][mask] = x`; use `df.loc[mask, col] = x`). Text columns are read as
  `object` by our loaders (see docs/architecture.md).
- Streamlit: use the current API (for example `width="stretch"`, not the
  deprecated `use_container_width`). Run with `streamlit run app.py`.
- Architecture: follow `docs/architecture.md`. Any change to it needs my
  approval first.