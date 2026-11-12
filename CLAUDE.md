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
- Deterministic random seed: 42.
- Keep clean data generation separate from anomaly injection. The clean
  dataset must raise zero exceptions; clean price movements are truncated
  below the thresholds (see docs/data_spec.md).
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
- Previous price = latest PreviousPrice strictly before the reference date.
- Price alerts use the absolute percentage movement and trigger when
  |PriceChangePct| is strictly greater than the threshold.
- Default thresholds: 5% for CLO Debt and ABS, 15% for CLO Equity.
- CLO Equity may legitimately have no rating.
- Amounts are never summed across currencies (no FX conversion).

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
- Securities without previous pricing history.
- Asset-specific price movements above thresholds.
- Cashflows without a matching position (key: Portfolio + ISIN).
- Duplicate cashflows (key: Portfolio + ISIN + CashflowDate + CashflowType + Amount).
- Past-dated cashflows still marked Forecast.
- Cashflow currency different from its position currency.

Do not classify a missing previous price as a missing current market price.
Do not compute a price movement when the current or previous price is
missing or non-positive (already reported by another rule).

## Exception rules (one anomaly = one exception)
The anomaly manifest expects exactly one exception per injected anomaly.
The engine follows these rules:
1. A position without ISIN raises only MISSING_ISIN: no other ISIN-based
   control (price history, price movement, duplicate key) runs on that row.
2. MISSING_VALUE and INVALID_VALUE only cover fields without a dedicated
   control (ISIN, MarketPrice, Rating, Nominal and prices have their own).
3. An empty price or nominal is missing, not invalid: INVALID_PRICE and
   INVALID_NOMINAL apply only to a value that is present and <= 0.
4. Cashflow matching checks that Portfolio + ISIN exists in the positions;
   it never joins row by row, so a duplicated position does not duplicate
   cashflows. Reports state that a duplicated position is counted twice
   in totals.
5. Duplicates (positions, cashflows) raise one exception per duplicated
   key, not one per row.
6. CURRENCY_MISMATCH is checked only on cashflows that match a position.
7. PriceChangePct = (MarketPrice - PreviousPrice) / PreviousPrice * 100,
   in this order, rounded to 6 decimals before the comparison with the
   threshold (so that exactly 5 % never triggers through a floating-point
   residue). No movement is computed when either price is missing or <= 0.

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
- Past-dated Forecast cashflow.

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
- One rule = one function returning exceptions in a common format:
  Severity, ControlID, AnomalyType, Source, Portfolio, ISIN, SecurityName,
  ObservedValue, Comment.
- Keep calculations and exception detection deterministic.
- Readable code over clever code: short functions, explicit names, no unnecessary classes.

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
- Run tests:           `pytest -q`
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

## Git
One commit per validated step, with a clear message. Commit only after my approval.