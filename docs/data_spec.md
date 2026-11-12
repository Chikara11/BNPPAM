# Data specification – input files

This document is the contract between the synthetic data generator and the
processing engine. Any change to a column, type or rule must be made here first.

## General conventions

| Item | Convention |
|---|---|
| File format | `.xlsx`, one sheet, header on row 1, one record per row, no merged cells, no totals row |
| Column names | Exactly as written below (case-sensitive, no trailing spaces) |
| Dates | Excel date cells, no time component. Read as pandas `datetime64`, normalised to midnight |
| Numbers | Excel numeric cells (never numbers stored as text). Read as `float64` |
| Text | Trimmed strings. An empty cell or an empty string is treated as missing |
| Currencies | ISO 4217 codes: `EUR`, `USD`, `GBP` |
| Prices | Quoted per 100 of nominal (e.g. `98.5` = 98.5 % of face value) |
| Identifiers | Synthetic, 12 characters: `SYN` + 9 digits (e.g. `SYN000000001`). Same length as an ISIN, clearly fictional, never a real ISIN |
| Valuation date | A single valuation date per dataset: `2026-11-11` |

Missing required columns stop processing. Invalid values (e.g. text in a numeric
column) are reported as data quality exceptions, never silently corrected.

---

## 1. `positions.xlsx`

**Grain:** one row = one security held in one portfolio on the valuation date.
**Uniqueness key:** `ValuationDate + Portfolio + ISIN`.

| Column | Type | Required | Constraints | Example |
|---|---|---|---|---|
| `ValuationDate` | date | Yes | Same value on every row in the clean dataset | `2026-11-11` |
| `Portfolio` | string | Yes | One of the portfolio codes listed below | `CLO_EURO_01` |
| `ISIN` | string (12) | Yes | Pattern `^SYN\d{9}$` | `SYN000000001` |
| `SecurityName` | string | Yes | Fictional deal name + vintage + tranche | `Aurora CLO 2023-1 B` |
| `AssetType` | category | Yes | `CLO Debt`, `CLO Equity`, `ABS` | `CLO Debt` |
| `Currency` | string (3) | Yes | `EUR`, `USD`, `GBP` | `EUR` |
| `Nominal` | float | Yes | > 0. Face amount held, in `Currency` (not a market value) | `5000000` |
| `BookPrice` | float | Yes | > 0, per 100 nominal | `99.20` |
| `MarketPrice` | float | Yes | > 0, per 100 nominal | `98.75` |
| `Rating` | string | Conditional | Required for `CLO Debt` and `ABS`: `AAA`, `AA`, `A`, `BBB`, `BB`, `B`, `CCC`. Not required for `CLO Equity` (equity tranches are not rated): left empty in the clean dataset, and an empty rating on CLO Equity is never an exception | `BBB` |
| `MaturityDate` | date | Yes | > `ValuationDate` | `2036-04-15` |

**Realistic ranges in the clean dataset** (simulation choices, not market data):

| AssetType | Rating | Previous price range | Nominal per line |
|---|---|---|---|
| CLO Debt | AAA, AA | 97 – 101 | 1 m – 10 m |
| CLO Debt | A, BBB | 93 – 100 | 1 m – 10 m |
| CLO Debt | BB, B | 85 – 97 | 1 m – 10 m |
| ABS | AAA, AA | 97 – 101 | 1 m – 10 m |
| ABS | A, BBB | 95 – 100 | 1 m – 10 m |
| CLO Equity | none | 30 – 90 | 0.5 m – 5 m |

The ranges apply to the previous price. `MarketPrice` is derived from it with a
truncated return (see section 2), so it can end slightly outside the range
(e.g. about 104 at most for CLO Debt: 101 × 1.03). `CCC` is a valid rating value but is not used
in the clean dataset (a CCC tranche would trade well below 85).

`BookPrice` stays within ±3 points of `MarketPrice` in the clean dataset.

**Portfolios:**

| Portfolio | Asset types | Currencies |
|---|---|---|
| `CLO_EURO_01` | CLO Debt | EUR |
| `CLO_GLOBAL_02` | CLO Debt, CLO Equity | EUR, USD |
| `ABS_EUROPE_03` | ABS | EUR, GBP |
| `MULTI_USD_04` | CLO Debt, CLO Equity, ABS | USD |

**Example rows:**

| ValuationDate | Portfolio | ISIN | SecurityName | AssetType | Currency | Nominal | BookPrice | MarketPrice | Rating | MaturityDate |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-11 | CLO_EURO_01 | SYN000000001 | Aurora CLO 2023-1 B | CLO Debt | EUR | 5000000 | 99.20 | 98.75 | AA | 2036-04-15 |
| 2026-11-11 | CLO_GLOBAL_02 | SYN000000014 | Boreal CLO 2022-2 Sub | CLO Equity | USD | 2000000 | 62.00 | 60.40 |  | 2035-01-20 |
| 2026-11-11 | ABS_EUROPE_03 | SYN000000027 | Cassis Auto ABS 2024-1 A | ABS | GBP | 3000000 | 100.10 | 99.85 | AAA | 2031-09-25 |

Example rows are illustrative; they are not guaranteed to appear in the
generated files.

---

## 2. `previous_prices.xlsx`

**Grain:** one row = one historical price for one security on one date.
A security may have several rows (price history).
**Uniqueness key:** `ISIN + PreviousDate`.

| Column | Type | Required | Constraints | Example |
|---|---|---|---|---|
| `ISIN` | string (12) | Yes | Pattern `^SYN\d{9}$`. In the clean dataset, every ISIN is held in `positions.xlsx`; prices for ISINs not held are ignored by the engine | `SYN000000001` |
| `PreviousDate` | date | Yes | < `ValuationDate` | `2026-11-04` |
| `PreviousPrice` | float | Yes | > 0, per 100 nominal | `99.10` |
| `PriceSource` | category | Yes | `Pricing Agent`, `Broker Quote`, `Evaluated Price` | `Pricing Agent` |

**Rule used by the engine:** the previous price of a security is the price with
the latest `PreviousDate` strictly before the reference date (= latest
`ValuationDate` by default).

**Clean dataset:** every position has a previous price dated 7 days before the
valuation date (`2026-11-04`); about half of the securities also have an older
price dated 14 days before (`2026-10-28`). Current prices are derived from the
latest previous price with a simulated return drawn from a **truncated** normal
distribution: the return is redrawn until its absolute value is within the
bound, so that no clean movement reaches a threshold:

| AssetType | Return distribution | Truncation bound in clean data |
|---|---|---|
| CLO Debt | normal, σ = 1.5 % | ±3 % |
| ABS | normal, σ = 1 % | ±3 % |
| CLO Equity | normal, σ = 5 % | ±10 % |

**Rounding tolerance.** Prices are stored with 2 decimals:
`MarketPrice = round(PreviousPrice × (1 + r), 2)`. Rounding moves the realised
`PriceChangePct` by at most 0.005 / PreviousPrice × 100, i.e. about 0.017
percentage point at the lowest price (30). The clean-data check recomputes
`PriceChangePct` from the stored prices and accepts
|PriceChangePct| ≤ bound + 0.02 percentage point; it must also be strictly
below the threshold read from `config.yaml`.

**Example rows:**

| ISIN | PreviousDate | PreviousPrice | PriceSource |
|---|---|---|---|
| SYN000000001 | 2026-11-04 | 99.10 | Pricing Agent |
| SYN000000001 | 2026-10-28 | 99.30 | Pricing Agent |
| SYN000000014 | 2026-11-04 | 61.20 | Broker Quote |

---

## 3. `cashflows.xlsx`

**Grain:** one row = one expected or actual cashflow for one holding.
**Uniqueness key:** `Portfolio + ISIN + CashflowDate + CashflowType + Amount`.
**Link to positions:** `Portfolio + ISIN`.

| Column | Type | Required | Constraints | Example |
|---|---|---|---|---|
| `Portfolio` | string | Yes | Existing portfolio code | `CLO_EURO_01` |
| `ISIN` | string (12) | Yes | Pattern `^SYN\d{9}$`; the pair `Portfolio + ISIN` exists in `positions.xlsx` | `SYN000000001` |
| `CashflowDate` | date | Yes | Valid date; ≤ `MaturityDate` of the position | `2026-11-25` |
| `CashflowType` | category | Yes | `Coupon`, `Principal`, `Fee` | `Coupon` |
| `Currency` | string (3) | Yes | Same as the position currency | `EUR` |
| `Amount` | float | Yes | Coupon and Principal > 0 (inflows); Fee < 0 (outflow) | `62500.00` |
| `Status` | category | Yes | `Forecast`, `Confirmed`, `Paid` | `Forecast` |

**Status coherence in the clean dataset** (reference date (= latest
`ValuationDate` by default) = the single valuation date, 2026-11-11):

| Status | CashflowDate (VD = valuation date) |
|---|---|
| `Paid` | strictly before VD |
| `Confirmed` | from VD to VD + 10 days, inclusive |
| `Forecast` | strictly after VD + 10 days |

A cashflow dated exactly on VD is not past-dated (the past-dated rule uses
"strictly before VD"). The upcoming-cashflows window is VD to VD + 30 days,
inclusive, excluding `Paid`.

This status/date rule is a convention of the clean dataset only. It is not a
control: the engine does not flag, for example, a `Confirmed` cashflow dated
after VD + 10 days.

**Amount conventions:**
- Coupon (CLO Debt, ABS): quarterly, `Nominal × annual rate / 4`, annual rate
  4 – 9 %, drawn once per security.
- Coupon (CLO Equity): equity tranches receive residual distributions rather
  than a contractual coupon; they are recorded as `Coupon` for simplicity,
  quarterly, based on market value:
  `Nominal × MarketPrice / 100 × annual rate / 4`, annual rate 12 – 20 %.
  The current `MarketPrice` is used for all dates, including past ones
  (simplification).
- Principal: ABS only, amortisation on each coupon date, 1 – 5 % of `Nominal`
  per payment (drawn per payment).
- Fee: one fee for about 25 % of holdings, uniform between −10,000 and −2,000,
  dated VD + 1 to VD + 60 days. In practice fees are often charged at portfolio
  level; attaching them to a holding is a simplification.
- All amounts are rounded to 2 decimals.

**Time coverage:** past cashflows (`Paid`), cashflows in the next 30 days, and
cashflows beyond 30 days, so that the upcoming-cashflows filter can be checked.

**Example rows:**

| Portfolio | ISIN | CashflowDate | CashflowType | Currency | Amount | Status |
|---|---|---|---|---|---|---|
| CLO_EURO_01 | SYN000000001 | 2026-08-15 | Coupon | EUR | 75000.00 | Paid |
| CLO_EURO_01 | SYN000000001 | 2026-11-15 | Coupon | EUR | 75000.00 | Confirmed |
| ABS_EUROPE_03 | SYN000000027 | 2026-11-25 | Principal | GBP | 90000.00 | Forecast |
| CLO_EURO_01 | SYN000000001 | 2026-11-30 | Fee | EUR | -4500.00 | Forecast |

---

## 4. Clean dataset invariants

The generator asserts all of the following before saving. Running the controls
on the clean dataset must raise **zero** exceptions.

1. All required columns are present, with the types above.
2. `ValuationDate + Portfolio + ISIN` is unique in positions; `ISIN + PreviousDate`
   is unique in previous prices.
3. No missing value, except `Rating` on CLO Equity.
4. Categorical columns only contain their allowed values; every portfolio holds
   only the asset types and currencies listed in the portfolio table.
5. `Rating` is filled for CLO Debt and ABS, and empty for CLO Equity.
6. `Nominal`, `BookPrice`, `MarketPrice`, `PreviousPrice` > 0, and `BookPrice`
   within ±3 points of `MarketPrice`.
7. `MaturityDate` > `ValuationDate`; `PreviousDate` < `ValuationDate`.
8. Every position has a previous price; every ISIN in previous prices is held;
   every price movement is within its truncation bound (with the rounding
   tolerance of section 2) and strictly below its threshold in `config.yaml`.
9. Every cashflow matches a position (`Portfolio + ISIN`), has the position
   currency, is dated on or before the position `MaturityDate`, and follows the
   amount sign convention.
10. Cashflow statuses follow the coherence table above.
11. No duplicate cashflow.

## 5. Generation parameters of the clean dataset

Simulation choices, not market data. They are constants of
`scripts/generate_clean_data.py`; thresholds are read from `config.yaml`.

| Item | Value |
|---|---|
| Random generator | one `numpy.random.default_rng(42)`; changing the call order changes the dataset |
| Determinism | two runs produce identical data (same printed fingerprint), not byte-identical files (xlsx metadata contains timestamps) |
| Positions | 50 positions, 48 distinct securities, ISINs sequential from `SYN000000001` |
| Shared securities | 2 USD CLO Debt securities held in both `CLO_GLOBAL_02` and `MULTI_USD_04`: same name, rating, maturity and `MarketPrice`; own `Nominal`, `BookPrice` and cashflows |
| Nominal | uniform in the range of section 1, rounded to 250,000 (CLO Debt, ABS) or 100,000 (CLO Equity) |
| BookPrice | `round(MarketPrice + U(−2.5, +2.5), 2)` |
| Rating | uniform: `AAA`–`B` for CLO Debt, `AAA`–`BBB` for ABS; drawn before the price |
| PreviousPrice (VD − 7) | uniform in the range of its asset type **and rating** (section 1 table), rounded to 2 decimals **before** `MarketPrice` is derived (the rounding tolerance of section 2 relies on it) |
| MaturityDate | CLO (Debt and Equity): VD + 5 to 12 years; ABS: VD + 3 to 8 years |
| SecurityName | fictional deal word + vintage 2019–2025 + tranche (AAA→A, AA→B, A→C, BBB→D, BB→E, B→F, CLO Equity→Sub; ABS names include Auto, Consumer or Leasing) |
| Older price (VD − 14) | each security has a 50 % probability of an older price (random draw, so the realised count varies); `PreviousPrice / (1 + r2)`, r2 drawn like r. The older price has no effect on the controls |
| PriceSource | uniform among the three values |
| Coupon schedule | defined per security (a security held in two portfolios pays on the same dates in both; amounts depend on each holding's `Nominal`); next date = VD + k days, k uniform 0–85; previous = next − 3 months (`Paid`); following = next + 3 months |

**Position split:**

| Portfolio | Positions | Detail |
|---|---|---|
| `CLO_EURO_01` | 12 | 12 CLO Debt EUR |
| `CLO_GLOBAL_02` | 14 | 9 CLO Debt, 5 CLO Equity; EUR and USD (incl. the 2 shared USD CLO Debt) |
| `ABS_EUROPE_03` | 12 | 7 ABS EUR, 5 ABS GBP |
| `MULTI_USD_04` | 12 | 5 CLO Debt (incl. the 2 shared), 3 CLO Equity, 4 ABS; all USD |

Anomalies are injected afterwards, in a separate step, and recorded in
`docs/anomaly_manifest.xlsx`.