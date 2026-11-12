# Output specification

Single source of truth for the outputs of the engine (`Results`) and the sheets
of the Excel report. `dq/schema.py` implements this document; if they differ,
this document wins and the code is fixed.

Conventions:
- dtypes are pandas dtypes. Text = `object`. Nullable integers = `Int64`.
  Nullable booleans = `boolean`. Dates = `datetime64` (unit as returned by
  pandas).
- Column order in this document = column order in the DataFrame and in the
  report sheet.
- Percentages are stored as percent values (`-7.142857` means -7.142857 %),
  rounded to 6 decimals.
- Amounts are never summed across currencies.
- Every output is deterministic: same inputs + same config = same rows in the
  same order.

---

## 0. `parse_issues` (part of `InputData`, built by the loader)

One row per non-empty input cell that failed its type conversion
(CLAUDE.md exception rule 10). The cell is missing in the converted
DataFrame and reported only by INVALID_VALUE.

| # | Column | dtype | Nullable | Description |
|---|---|---|---|---|
| 1 | File | object | no | `positions`, `previous_prices` or `cashflows` |
| 2 | SourceRow | Int64 | no | Excel row number (header = 1) |
| 3 | Column | object | no | Column of the cell |
| 4 | RawValue | object | no | Raw cell value as text, e.g. `98.5` (text cell), `True`, `45000` |

Sort order: File in the order above, SourceRow, then column order of
data_spec.md.

---

## 1. `exceptions`

One row per exception. Format referenced by CLAUDE.md.

| # | Column | dtype | Nullable | Description |
|---|---|---|---|---|
| 1 | ExceptionID | object | no | `EXC-0001`, zero-padded to 4 digits, assigned after sorting |
| 2 | Severity | category (ordered: Critical < Warning < Information for sorting) | no | From config.yaml |
| 3 | ControlID | object | no | Key in config.yaml, e.g. `PRICE_MOVE_ABOVE_THRESHOLD` |
| 4 | Dimension | object | no | Data quality dimension of the control, from config.yaml |
| 5 | AnomalyType | object | no | Control `label` from config.yaml |
| 6 | Source | object | no | Input file: `positions`, `previous_prices` or `cashflows` |
| 7 | Portfolio | object | yes | |
| 8 | ISIN | object | yes | Empty for MISSING_ISIN |
| 9 | SecurityName | object | yes | Empty when unknown (e.g. unmatched cashflow) |
| 10 | Field | object | no | Column checked; key columns joined with `+` for duplicates and unmatched cashflows |
| 11 | ObservedValue | object | yes | Value as text (raw text for conversion failures) |
| 12 | Comment | object | no | Plain-language explanation with the expected rule and, for multi-row exceptions, all rows |
| 13 | SourceRow | Int64 | no | Excel row number (header = 1); first row for multi-row exceptions |

Sort order: Severity (Critical, Warning, Information), ControlID, Portfolio,
ISIN, SourceRow, Field (last tie-breaker, e.g. two INVALID_PRICE on the same
row). Empty Portfolio / ISIN sort last.

**[Decision]** `ExceptionID` has 4 digits: the format supports up to 9,999
exceptions per run, enough for the prototype (documented limit).

---

## 2. `price_movements`

One row per position at the reference date, including positions whose move
cannot be computed.

| # | Column | dtype | Nullable | Description |
|---|---|---|---|---|
| 1 | Portfolio | object | yes | |
| 2 | ISIN | object | yes | |
| 3 | SecurityName | object | yes | |
| 4 | AssetType | object | yes | |
| 5 | Currency | object | yes | |
| 6 | Rating | object | yes | |
| 7 | Nominal | float64 | yes | |
| 8 | PreviousDate | datetime64 | yes | Date of the latest previous price before the reference date |
| 9 | PreviousPrice | float64 | yes | |
| 10 | MarketPrice | float64 | yes | |
| 11 | PriceChangePct | float64 | yes | `(MarketPrice - PreviousPrice) / PreviousPrice * 100`, rounded to 6 decimals |
| 12 | ThresholdPct | float64 | yes | Threshold of the asset type, from config.yaml |
| 13 | Breach | boolean | yes | `abs(PriceChangePct) > ThresholdPct`; empty when not computable |
| 14 | MoveStatus | object | no | See vocabulary below |
| 15 | MarketValue | float64 | yes | `Nominal * MarketPrice / 100`, in the position currency. Empty when Nominal or MarketPrice is missing or <= 0, or when Currency is empty or not in `allowed_values.Currency`: an invalid value never enters a total |
| 16 | SourceRow | Int64 | no | Row in positions |

**[Decision]** `MoveStatus` vocabulary (exactly one per row; when several
apply, the first in this list wins):
`Missing ISIN` (empty), `Invalid ISIN` (present but not matching
`isin_pattern`), `Missing market price`, `Invalid market price` (<= 0),
`No price history` (no previous_prices row for the ISIN), `No usable previous
price` (rows exist but none has a valid PreviousDate before the reference
date), `Conflicting previous prices` (several different prices on the latest
PreviousDate), `Missing previous price` (empty on the latest PreviousDate),
`Invalid previous price` (<= 0 on the latest PreviousDate), `Computed`.

Sort order: Breach (True first), abs(PriceChangePct) descending, Portfolio, ISIN.

---

## 3. `upcoming_cashflows`

Cashflows with `reference_date <= CashflowDate <= reference_date + horizon`
and `Status != Paid`.

| # | Column | dtype | Nullable | Description |
|---|---|---|---|---|
| 1 | CashflowDate | datetime64 | no | |
| 2 | DaysFromReference | Int64 | no | `CashflowDate - reference_date` in days |
| 3 | Portfolio | object | yes | |
| 4 | ISIN | object | yes | |
| 5 | SecurityName | object | yes | From the matching position; empty if unmatched |
| 6 | AssetType | object | yes | From the matching position; empty if unmatched |
| 7 | CashflowType | object | yes | |
| 8 | Amount | float64 | yes | |
| 9 | Currency | object | yes | Cashflow currency |
| 10 | Status | object | yes | |
| 11 | MatchedPosition | boolean | no | A position exists on Portfolio + ISIN |
| 12 | SourceRow | Int64 | no | Row in cashflows |

Sort order: CashflowDate, Portfolio, ISIN, SourceRow.

A cashflow whose Currency is empty or not allowed stays in this table (it is
reported by MISSING_VALUE or INVALID_VALUE) but is excluded from the currency
totals of the summary.

---

## 4. `data_quality`

One row per input file and column.

| # | Column | dtype | Nullable | Description |
|---|---|---|---|---|
| 1 | Source | object | no | `positions`, `previous_prices`, `cashflows` |
| 2 | Column | object | no | |
| 3 | Required | boolean | no | Listed in `required_columns` |
| 4 | Rows | Int64 | no | Rows analysed (positions: at the reference date) |
| 5 | NotApplicable | Int64 | no | e.g. Rating on CLO Equity |
| 6 | Missing | Int64 | no | Empty cells among applicable rows |
| 7 | ParseIssues | Int64 | no | Non-empty values that failed type conversion |
| 8 | CompletenessPct | float64 | yes | `(Applicable - Missing - ParseIssues) / Applicable * 100`, rounded to 2 decimals; empty if no applicable row. A cell that cannot be read is not complete |

Sort order: Source in the order above, then column order of data_spec.md.

---

## 4b. `dimension_quality`

One row per data quality dimension of `config.yaml` (`dimensions`), in that
order, zeros included. Each control belongs to exactly one dimension
(`controls.<ID>.dimension`).

| # | Column | dtype | Nullable | Description |
|---|---|---|---|---|
| 1 | Dimension | object | no | Completeness, Validity, Uniqueness, Consistency, Timeliness, Plausibility |
| 2 | Controls | object | yes | Enabled controls of the dimension, comma-separated, in config.yaml order; empty if none is enabled |
| 3 | Exceptions | Int64 | no | Exceptions raised by these controls |
| 4 | Critical | Int64 | no | Of which Critical |
| 5 | Warning | Int64 | no | Of which Warning |
| 6 | Information | Int64 | no | Of which Information |

Notes:
- Plausibility = review alerts (e.g. large price moves), not necessarily
  data errors. They are counted in the totals like the other dimensions.
- **[Limitation]** One control belongs to one dimension. INVALID_VALUE is
  in Validity although it also reports invalid dates (MaturityDate,
  CashflowDate after maturity, PreviousDate) and empty rows, which are
  closer to Timeliness and Completeness. Splitting them would need new
  control IDs.

---

## 5. `summary`

**[Decision]** Long format, so one table holds every headline figure and
currencies are never mixed.

| # | Column | dtype | Nullable | Description |
|---|---|---|---|---|
| 1 | Section | object | no | See list below |
| 2 | Item | object | no | e.g. `Critical`, `CLO_EURO_01`, `Positions` |
| 3 | Currency | object | yes | Only for amounts |
| 4 | Value | float64 | no | |
| 5 | Unit | object | no | `count`, `amount` or `pct` |

Sections, in this order:
1. `Overview`, in this order: `Positions` (at the reference date),
   `Portfolios`, `Distinct ISINs`, `Duplicated position rows (counted twice in
   totals)` (extra rows of a duplicated key, never removed), `Position rows not
   at the reference date (ignored)`, `Cashflows in the horizon`, `Duplicated
   cashflow rows in the horizon` (extra rows of a duplicated cashflow key among
   the upcoming cashflows; they are in the totals).
2. `Exceptions by severity`: Critical, Warning, Information (zeros included).
3. `Exceptions by control`: every enabled control (zeros included).
3b. `Exceptions by dimension`: every dimension of config.yaml, in that
   order (zeros included).
4. `Exceptions by portfolio`: every portfolio, plus `(none)` for exceptions
   without a portfolio.
5. `Market value by currency`: sum of MarketValue where computable.
6. `Upcoming cashflows by currency`: sum of Amount in the horizon.

   Sections 5 and 6 list only the currencies of `allowed_values.Currency`,
   in that order: an amount in an empty or unknown currency is never
   totalled (same rule as a Nominal or price <= 0).
7. `Price moves`: positions computed, breaches, not computable.

The reference date is not a row: it is `Results.reference_date`.

---

## 6. Excel report sheets

| Order | Sheet | Content |
|---|---|---|
| 1 | Executive Summary | Reference date, run timestamp, then `summary` by section |
| 2 | Exceptions | `exceptions`, severity colours, filters, frozen header |
| 3 | Price Movements | `price_movements`, breaches highlighted |
| 4 | Cashflows {N}D | `upcoming_cashflows`; N = horizon in days; unmatched cashflows highlighted with the Warning tint |
| 5 | Data Quality | "Checks by dimension": `dimension_quality` (Controls wrapped in their cell), then the note "Plausibility = review alerts (e.g. large price moves), not necessarily data errors."; a blank row; "Completeness by column": `data_quality`. Autofilter on the column table only, no frozen header (Excel allows one filter per sheet) |
| 6 | Parameters | Config used for the run: `Parameter`, `Value` (text), one row per setting |

**[Decision]** Number formats: amounts `#,##0.00`, prices `0.00`,
percentages `0.00` followed by ` %` in the header name, dates `YYYY-MM-DD`.
Severity colours: Critical red, Warning orange, Information blue.