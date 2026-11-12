"""Inject controlled anomalies into copies of the clean input files.

Reads data/clean/*.xlsx (never modified), applies the cases of the anomaly
manifest and writes the deliverable input files to data/inputs/ and the test
oracle to docs/anomaly_manifest.xlsx. No randomness: every case is a constant.
Expected exceptions follow CLAUDE.md, section "Exception rules".

Verification: data/clean/ and data/inputs/ are compared row by row. Every
added, deleted or changed row must belong to a manifest case, and every case
that changes data must produce at least one difference. Anything else stops
the script with InjectionError.

Run: python scripts/inject_anomalies.py
"""

import difflib
import hashlib
from pathlib import Path

import pandas as pd

from generate_clean_data import (
    CONFIG_PATH,
    LATEST_PRICE_LAG_DAYS,
    OLDER_PRICE_LAG_DAYS,
    OUTPUT_DIR as CLEAN_DIR,
    REPO_ROOT,
    VALUATION_DATE,
    fingerprint,
    load_config,
    read_workbook,
    save_workbook,
)

INPUT_DIR = REPO_ROOT / "data" / "inputs"
MANIFEST_PATH = REPO_ROOT / "docs" / "anomaly_manifest.xlsx"
FILE_NAMES = ["positions", "previous_prices", "cashflows"]

SHARED_ISINS = {"SYN000000018", "SYN000000019"}
LATEST_PRICE_DATE = VALUATION_DATE - pd.Timedelta(days=LATEST_PRICE_LAG_DAYS)
OLDER_PRICE_DATE = VALUATION_DATE - pd.Timedelta(days=OLDER_PRICE_LAG_DAYS)
BOOK_PRICE_OFFSET = 0.50  # price cases: BookPrice = new MarketPrice + 0.50
PCT_DECIMALS = 6  # CLAUDE.md exception rule 7

MANIFEST_COLUMNS = [
    "AnomalyID", "File", "Portfolio", "AssetType", "Currency", "ISIN", "Field",
    "OriginalValue", "InjectedValue", "ExpectedControl", "ExpectedSeverity",
    "ExpectedExceptionCount", "Description",
]

# Agreed distribution of the 15 anomalies (A cases only).
EXPECTED_DISTRIBUTION = {
    "Portfolio": {"CLO_EURO_01": 4, "CLO_GLOBAL_02": 4, "ABS_EUROPE_03": 4, "MULTI_USD_04": 3},
    "ExpectedSeverity": {"Critical": 6, "Warning": 7, "Information": 2},
    "File": {"positions": 8, "previous_prices": 3, "cashflows": 4},
}

COSMETIC_DISTRIBUTIONS = ("Cosmetic, not checked by any control: the equity distributions "
                          "in cashflows were computed from the clean MarketPrice.")
COSMETIC_COUPONS = ("Cosmetic, not checked by any control: coupons were computed from "
                    "the clean Nominal.")


class InjectionError(Exception):
    """Raised when a case cannot be injected exactly as agreed."""


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------
def require(condition: bool, case_id: str, message: str) -> None:
    if not condition:
        raise InjectionError(f"{case_id}: {message}")


def as_text(value) -> str:
    """Manifest values are stored as text: numbers with 2 decimals, ISO dates."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def price_change_pct(previous_price: float, market_price: float) -> float:
    """CLAUDE.md exception rule 7: formula order and rounding."""
    return round((market_price - previous_price) / previous_price * 100, PCT_DECIMALS)


def insert_after(data: pd.DataFrame, label, row: dict) -> pd.DataFrame:
    position = data.index.get_loc(label) + 1
    new_row = pd.DataFrame([row], columns=data.columns)
    return pd.concat([data.iloc[:position], new_row, data.iloc[position:]], ignore_index=True)


def find_position(positions: pd.DataFrame, case_id: str, portfolio: str, isin: str,
                  asset_type: str, currency: str, ratings=None):
    """Index label of the single matching position; checks its attributes."""
    require(isin not in SHARED_ISINS, case_id, f"{isin} is a shared ISIN and must not be used")
    match = positions[(positions["Portfolio"] == portfolio) & (positions["ISIN"] == isin)]
    require(len(match) == 1, case_id, f"expected 1 position {portfolio}/{isin}, found {len(match)}")
    row = match.iloc[0]
    require(row["AssetType"] == asset_type, case_id, f"{isin} is {row['AssetType']}, not {asset_type}")
    require(row["Currency"] == currency, case_id, f"{isin} is in {row['Currency']}, not {currency}")
    if ratings is not None:
        require(row["Rating"] in ratings, case_id, f"{isin} is rated {row['Rating']}, not {ratings}")
    return match.index[0]


def find_price(previous_prices: pd.DataFrame, case_id: str, isin: str, date: pd.Timestamp):
    match = previous_prices[(previous_prices["ISIN"] == isin)
                            & (previous_prices["PreviousDate"] == date)]
    require(len(match) == 1, case_id, f"expected 1 price for {isin} on {date.date()}, "
                                      f"found {len(match)}")
    return match.index[0]


def find_cashflow(cashflows: pd.DataFrame, case_id: str, portfolio: str, isin: str,
                  date: pd.Timestamp, cashflow_type: str):
    match = cashflows[(cashflows["Portfolio"] == portfolio) & (cashflows["ISIN"] == isin)
                      & (cashflows["CashflowDate"] == date)
                      & (cashflows["CashflowType"] == cashflow_type)]
    require(len(match) == 1, case_id, f"expected 1 {cashflow_type} for {portfolio}/{isin} "
                                      f"on {date.date()}, found {len(match)}")
    return match.index[0]


def manifest_row(config: dict, case_id: str, file: str, portfolio: str, asset_type: str,
                 currency: str, isin: str, field: str, original, injected, control: str,
                 severity: str, count: int, description: str, touches: list) -> dict:
    """One manifest row. `touches` lists the (file, portfolio, isin) the case may change."""
    settings = config["controls"][control]
    require(settings["enabled"], case_id, f"control {control} is disabled in config.yaml")
    if count > 0:
        require(settings["severity"] == severity, case_id,
                f"config.yaml severity of {control} is {settings['severity']}, "
                f"agreed {severity}")
    return {
        "AnomalyID": case_id, "File": file, "Portfolio": portfolio, "AssetType": asset_type,
        "Currency": currency, "ISIN": isin, "Field": field,
        "OriginalValue": as_text(original), "InjectedValue": as_text(injected),
        "ExpectedControl": control,
        "ExpectedSeverity": settings["severity"] if count > 0 else "",
        "ExpectedExceptionCount": count, "Description": description,
        "_touches": touches,
    }


# -----------------------------------------------------------------------------
# Generic injections
# -----------------------------------------------------------------------------
def set_position_field(data: dict, case_id: str, portfolio: str, isin: str, asset_type: str,
                       currency: str, field: str, value):
    """Set one field of one position; returns the original value."""
    positions = data["positions"]
    label = find_position(positions, case_id, portfolio, isin, asset_type, currency)
    original = positions.loc[label, field]
    positions.loc[label, field] = value
    return original


def inject_price_case(data: dict, config: dict, case_id: str, portfolio: str, isin: str,
                      asset_type: str, currency: str, ratings, previous: float, market: float,
                      older=None, expect_alert: bool = True) -> tuple:
    """Set the VD-7 price, MarketPrice and BookPrice (and optionally the VD-14 price).

    Returns (field, original, injected, pct) as manifest texts.
    """
    positions, previous_prices = data["positions"], data["previous_prices"]
    label = find_position(positions, case_id, portfolio, isin, asset_type, currency, ratings)
    require((positions["ISIN"] == isin).sum() == 1, case_id, f"{isin} held in several portfolios")
    price_label = find_price(previous_prices, case_id, isin, LATEST_PRICE_DATE)

    pct = price_change_pct(previous, market)
    threshold = config["price_move_thresholds_pct"][asset_type]
    require((abs(pct) > threshold) == expect_alert, case_id,
            f"{pct} % vs threshold {threshold} % does not give the expected result")

    book = round(market + BOOK_PRICE_OFFSET, 2)
    fields, originals, injected = [], [], []
    if older is not None:
        older_label = find_price(previous_prices, case_id, isin, OLDER_PRICE_DATE)
        fields.append("PreviousPrice (VD-14)")
        originals.append(previous_prices.loc[older_label, "PreviousPrice"])
        injected.append(older)
        previous_prices.loc[older_label, "PreviousPrice"] = older
    fields += ["PreviousPrice (VD-7)", "MarketPrice", "BookPrice"]
    originals += [previous_prices.loc[price_label, "PreviousPrice"],
                  positions.loc[label, "MarketPrice"], positions.loc[label, "BookPrice"]]
    injected += [previous, market, book]
    previous_prices.loc[price_label, "PreviousPrice"] = previous
    positions.loc[label, "MarketPrice"] = market
    positions.loc[label, "BookPrice"] = book
    return ("; ".join(fields), "; ".join(as_text(value) for value in originals),
            "; ".join(as_text(value) for value in injected), f"{pct:+.2f} %")


# -----------------------------------------------------------------------------
# Anomaly cases (15): one function per case, exactly one expected exception
# -----------------------------------------------------------------------------
def inject_a01(data: dict, config: dict) -> dict:
    portfolio, isin = "CLO_EURO_01", "SYN000000007"
    original = set_position_field(data, "A01", portfolio, isin, "CLO Debt", "EUR", "ISIN", None)
    prices = data["previous_prices"]
    deleted_prices = prices["ISIN"] == isin
    data["previous_prices"] = prices[~deleted_prices].reset_index(drop=True)
    cashflows = data["cashflows"]
    deleted_flows = (cashflows["Portfolio"] == portfolio) & (cashflows["ISIN"] == isin)
    data["cashflows"] = cashflows[~deleted_flows].reset_index(drop=True)
    return manifest_row(
        config, "A01", "positions", portfolio, "CLO Debt", "EUR", "", "ISIN", original, None,
        "MISSING_ISIN", "Critical", 1,
        f"ISIN emptied on a CLO Debt position (clean ISIN {isin}). Its "
        f"{deleted_prices.sum()} previous price(s) and {deleted_flows.sum()} cashflow(s) were "
        "deleted so that they do not become unmatched. Exception rule 1: only MISSING_ISIN, "
        "no other ISIN-based control on this row.",
        [("positions", portfolio, isin), ("previous_prices", None, isin),
         ("cashflows", portfolio, isin)])


def inject_a02(data: dict, config: dict) -> dict:
    portfolio, isin = "ABS_EUROPE_03", "SYN000000035"
    positions = data["positions"]
    label = find_position(positions, "A02", portfolio, isin, "ABS", "GBP")
    data["positions"] = insert_after(positions, label, positions.loc[label].to_dict())
    return manifest_row(
        config, "A02", "positions", portfolio, "ABS", "GBP", isin, "(entire row)",
        "1 row", "2 identical rows", "DUPLICATE_POSITION", "Critical", 1,
        "Exact duplicate of the position row, inserted right after it. Its cashflows are not "
        "duplicated. Exception rules 4 and 5: one exception for the duplicated key; cashflows "
        "are not duplicated by a join; the position is counted twice in totals.",
        [("positions", portfolio, isin)])


def inject_a03(data: dict, config: dict) -> dict:
    portfolio, isin = "MULTI_USD_04", "SYN000000046"
    original = set_position_field(data, "A03", portfolio, isin, "ABS", "USD",
                                  "MarketPrice", float("nan"))
    return manifest_row(
        config, "A03", "positions", portfolio, "ABS", "USD", isin, "MarketPrice", original, None,
        "MISSING_MARKET_PRICE", "Critical", 1,
        "MarketPrice emptied. Exception rule 3: missing, not invalid (no INVALID_PRICE). "
        "No price movement computed (exception rule 7).",
        [("positions", portfolio, isin)])


def inject_a04(data: dict, config: dict) -> dict:
    portfolio, isin = "CLO_GLOBAL_02", "SYN000000015"
    original = set_position_field(data, "A04", portfolio, isin, "CLO Debt", "EUR", "Rating", None)
    return manifest_row(
        config, "A04", "positions", portfolio, "CLO Debt", "EUR", isin, "Rating", original, None,
        "MISSING_RATING", "Warning", 1,
        "Rating emptied on a CLO Debt position (rating required). Exception rule 2: "
        "no MISSING_VALUE on Rating.",
        [("positions", portfolio, isin)])


def inject_a05(data: dict, config: dict) -> dict:
    portfolio, isin = "CLO_EURO_01", "SYN000000049"
    for name in FILE_NAMES:
        require(not (data[name]["ISIN"] == isin).any(), "A05", f"{isin} already in {name}")
    positions = data["positions"]
    last_in_portfolio = positions.index[positions["Portfolio"] == portfolio][-1]
    new_position = {
        "ValuationDate": VALUATION_DATE, "Portfolio": portfolio, "ISIN": isin,
        "SecurityName": "Aurora CLO 2026-1 B", "AssetType": "CLO Debt", "Currency": "EUR",
        "Nominal": 5_000_000.0, "BookPrice": 99.75, "MarketPrice": 99.50, "Rating": "AA",
        "MaturityDate": pd.Timestamp("2037-11-15"),
    }
    data["positions"] = insert_after(positions, last_in_portfolio, new_position)
    return manifest_row(
        config, "A05", "positions", portfolio, "CLO Debt", "EUR", isin, "(new row)",
        None, "new position", "NO_PRICE_HISTORY", "Information", 1,
        "New CLO Debt security: Aurora CLO 2026-1 B, rated AA, Nominal 5,000,000, "
        "MarketPrice 99.50, BookPrice 99.75, maturity 2037-11-15. No previous price and no "
        "cashflow. No price movement computed.",
        [("positions", portfolio, isin)])


def inject_a06(data: dict, config: dict) -> dict:
    portfolio, isin = "CLO_EURO_01", "SYN000000004"
    field, original, injected, pct = inject_price_case(
        data, config, "A06", portfolio, isin, "CLO Debt", "EUR", {"A", "BBB"}, 98.00, 91.00)
    return manifest_row(
        config, "A06", "previous_prices", portfolio, "CLO Debt", "EUR", isin, field,
        original, injected, "PRICE_MOVE_ABOVE_THRESHOLD", "Warning", 1,
        f"CLO Debt rated A: 98.00 -> 91.00 ({pct}), threshold 5 %. Latest previous price "
        "(VD-7) and MarketPrice set to exact values (both files changed); "
        "BookPrice = MarketPrice + 0.50. No VD-14 price.",
        [("positions", portfolio, isin), ("previous_prices", None, isin)])


def inject_a07(data: dict, config: dict) -> dict:
    portfolio, isin = "ABS_EUROPE_03", "SYN000000033"
    field, original, injected, pct = inject_price_case(
        data, config, "A07", portfolio, isin, "ABS", "EUR", {"AAA", "AA"}, 100.00, 106.00)
    return manifest_row(
        config, "A07", "previous_prices", portfolio, "ABS", "EUR", isin, field,
        original, injected, "PRICE_MOVE_ABOVE_THRESHOLD", "Warning", 1,
        f"ABS rated AAA: 100.00 -> 106.00 ({pct}), threshold 5 %. Latest previous price "
        "(VD-7) and MarketPrice set to exact values (both files changed); "
        "BookPrice = MarketPrice + 0.50. No VD-14 price.",
        [("positions", portfolio, isin), ("previous_prices", None, isin)])


def inject_a08(data: dict, config: dict) -> dict:
    portfolio, isin = "CLO_GLOBAL_02", "SYN000000025"
    field, original, injected, pct = inject_price_case(
        data, config, "A08", portfolio, isin, "CLO Equity", "USD", None, 80.00, 65.00,
        older=79.50)
    return manifest_row(
        config, "A08", "previous_prices", portfolio, "CLO Equity", "USD", isin, field,
        original, injected, "PRICE_MOVE_ABOVE_THRESHOLD", "Warning", 1,
        f"CLO Equity: 80.00 -> 65.00 ({pct}), threshold 15 %. The VD-14 price was also set "
        "to 79.50, so the history reads 79.50 -> 80.00 -> 65.00 (only the VD-7 price is used "
        f"by the engine). BookPrice = MarketPrice + 0.50. {COSMETIC_DISTRIBUTIONS}",
        [("positions", portfolio, isin), ("previous_prices", None, isin)])


def inject_a09(data: dict, config: dict) -> dict:
    portfolio, isin = "MULTI_USD_04", "SYN000000039"
    original = set_position_field(data, "A09", portfolio, isin, "CLO Debt", "USD", "Nominal", 0.0)
    return manifest_row(
        config, "A09", "positions", portfolio, "CLO Debt", "USD", isin, "Nominal", original, 0.0,
        "INVALID_NOMINAL", "Critical", 1,
        f"Nominal set to 0 (present and <= 0, exception rule 3). {COSMETIC_COUPONS}",
        [("positions", portfolio, isin)])


def inject_a10(data: dict, config: dict) -> dict:
    portfolio, isin = "ABS_EUROPE_03", "SYN000000029"
    original = set_position_field(data, "A10", portfolio, isin, "ABS", "EUR",
                                  "Nominal", -2_000_000.0)
    return manifest_row(
        config, "A10", "positions", portfolio, "ABS", "EUR", isin, "Nominal", original,
        -2_000_000.0, "INVALID_NOMINAL", "Critical", 1,
        f"Nominal set to -2,000,000 (present and <= 0, exception rule 3). "
        f"{COSMETIC_COUPONS} Principal amounts likewise.",
        [("positions", portfolio, isin)])


def inject_a11(data: dict, config: dict) -> dict:
    portfolio, isin = "CLO_GLOBAL_02", "SYN000000020"
    original = set_position_field(data, "A11", portfolio, isin, "CLO Debt", "USD",
                                  "MarketPrice", -1.50)
    positions = data["positions"]
    book_price = positions.loc[positions["ISIN"] == isin, "BookPrice"].iloc[0]
    return manifest_row(
        config, "A11", "positions", portfolio, "CLO Debt", "USD", isin, "MarketPrice",
        original, -1.50, "INVALID_PRICE", "Critical", 1,
        "MarketPrice set to -1.50 (present and <= 0, exception rule 3). No price movement "
        "computed (exception rule 7). Cosmetic, not checked by any control: BookPrice "
        f"unchanged at {book_price:.2f}.",
        [("positions", portfolio, isin)])


def inject_a12(data: dict, config: dict) -> dict:
    portfolio, isin = "CLO_EURO_01", "SYN999999999"
    require(not (data["positions"]["ISIN"] == isin).any(), "A12", f"{isin} must not be held")
    cashflows = data["cashflows"]
    last_in_portfolio = cashflows.index[cashflows["Portfolio"] == portfolio][-1]
    cashflow_date = VALUATION_DATE + pd.Timedelta(days=15)
    new_cashflow = {
        "Portfolio": portfolio, "ISIN": isin, "CashflowDate": cashflow_date,
        "CashflowType": "Coupon", "Currency": "EUR", "Amount": 50_000.00, "Status": "Forecast",
    }
    data["cashflows"] = insert_after(cashflows, last_in_portfolio, new_cashflow)
    return manifest_row(
        config, "A12", "cashflows", portfolio, "", "EUR", isin, "(new row)", None,
        f"Coupon 50000.00 EUR on {as_text(cashflow_date)}, Forecast",
        "UNMATCHED_CASHFLOW", "Warning", 1,
        "Coupon on SYN999999999, which is not held in any portfolio (VD+15, Forecast). "
        "Exception rule 6: no currency check on an unmatched cashflow.",
        [("cashflows", portfolio, isin)])


def inject_a13(data: dict, config: dict) -> dict:
    portfolio, isin = "MULTI_USD_04", "SYN000000042"
    find_position(data["positions"], "A13", portfolio, isin, "CLO Equity", "USD")
    cashflows = data["cashflows"]
    label = find_cashflow(cashflows, "A13", portfolio, isin, pd.Timestamp("2027-01-09"), "Coupon")
    data["cashflows"] = insert_after(cashflows, label, cashflows.loc[label].to_dict())
    return manifest_row(
        config, "A13", "cashflows", portfolio, "CLO Equity", "USD", isin, "(entire row)",
        "1 row", "2 identical rows", "DUPLICATE_CASHFLOW", "Warning", 1,
        "Exact duplicate of the Coupon of 2027-01-09 (84812.01, Forecast), inserted right "
        "after it. Equity distributions are recorded as Coupon (data_spec.md). Beyond the "
        "30-day window, so upcoming totals are not affected. Exception rule 5: one exception "
        "for the duplicated key.",
        [("cashflows", portfolio, isin)])


def inject_a14(data: dict, config: dict) -> dict:
    portfolio, isin = "ABS_EUROPE_03", "SYN000000034"
    find_position(data["positions"], "A14", portfolio, isin, "ABS", "GBP")
    cashflows = data["cashflows"]
    label = find_cashflow(cashflows, "A14", portfolio, isin,
                          pd.Timestamp("2027-01-03"), "Principal")
    require(cashflows.loc[label, "Status"] == "Forecast", "A14", "Principal is not Forecast")
    original = cashflows.loc[label, "CashflowDate"]
    new_date = VALUATION_DATE - pd.Timedelta(days=5)
    cashflows.loc[label, "CashflowDate"] = new_date
    return manifest_row(
        config, "A14", "cashflows", portfolio, "ABS", "GBP", isin, "CashflowDate",
        original, new_date, "PAST_FORECAST_CASHFLOW", "Warning", 1,
        "Principal moved from 2027-01-03 to 2026-11-06 (VD-5); Status still Forecast.",
        [("cashflows", portfolio, isin)])


def inject_a15(data: dict, config: dict) -> dict:
    portfolio, isin = "CLO_GLOBAL_02", "SYN000000022"
    find_position(data["positions"], "A15", portfolio, isin, "CLO Equity", "EUR")
    cashflows = data["cashflows"]
    label = find_cashflow(cashflows, "A15", portfolio, isin, pd.Timestamp("2026-12-19"), "Coupon")
    original = cashflows.loc[label, "Currency"]
    require(original == "EUR", "A15", f"cashflow currency is {original}, not EUR")
    cashflows.loc[label, "Currency"] = "USD"
    return manifest_row(
        config, "A15", "cashflows", portfolio, "CLO Equity", "EUR", isin, "Currency",
        original, "USD", "CURRENCY_MISMATCH", "Information", 1,
        "Currency of the Coupon of 2026-12-19 (equity distribution, recorded as Coupon per "
        "data_spec.md) changed from EUR to USD on a EUR position.",
        [("cashflows", portfolio, isin)])


# -----------------------------------------------------------------------------
# Cases that must raise NO exception
# -----------------------------------------------------------------------------
def inject_n01(data: dict, config: dict) -> dict:
    portfolio, isin = "MULTI_USD_04", "SYN000000044"
    field, original, injected, pct = inject_price_case(
        data, config, "N01", portfolio, isin, "CLO Equity", "USD", None, 80.00, 70.00,
        expect_alert=False)
    return manifest_row(
        config, "N01", "previous_prices", portfolio, "CLO Equity", "USD", isin, field,
        original, injected, "PRICE_MOVE_ABOVE_THRESHOLD", "", 0,
        f"CLO Equity: 80.00 -> 70.00 ({pct}), below the 15 % threshold: no exception. "
        f"BookPrice = MarketPrice + 0.50. No VD-14 price. {COSMETIC_DISTRIBUTIONS}",
        [("positions", portfolio, isin), ("previous_prices", None, isin)])


def inject_n02(data: dict, config: dict) -> dict:
    portfolio, isin = "CLO_EURO_01", "SYN000000001"
    field, original, injected, pct = inject_price_case(
        data, config, "N02", portfolio, isin, "CLO Debt", "EUR", {"AAA", "AA"}, 100.00, 95.00,
        expect_alert=False)
    return manifest_row(
        config, "N02", "previous_prices", portfolio, "CLO Debt", "EUR", isin, field,
        original, injected, "PRICE_MOVE_ABOVE_THRESHOLD", "", 0,
        f"CLO Debt rated AAA: 100.00 -> 95.00 ({pct}), exactly the 5 % threshold, not "
        "strictly greater: no exception. Exception rule 7 (rounded to 6 decimals). "
        "Needs a dedicated test. BookPrice = MarketPrice + 0.50. No VD-14 price.",
        [("positions", portfolio, isin), ("previous_prices", None, isin)])


def describe_n03(data: dict, config: dict) -> dict:
    positions = data["positions"]
    for isin in sorted(SHARED_ISINS):
        held_in = set(positions.loc[positions["ISIN"] == isin, "Portfolio"])
        require(held_in == {"CLO_GLOBAL_02", "MULTI_USD_04"}, "N03",
                f"{isin} held in {sorted(held_in)}")
    return manifest_row(
        config, "N03", "positions", "CLO_GLOBAL_02; MULTI_USD_04", "CLO Debt", "USD",
        "; ".join(sorted(SHARED_ISINS)), "(existing rows)", None, None,
        "DUPLICATE_POSITION", "", 0,
        "Existing rows, unchanged: the same ISIN held in two portfolios is not a duplicate "
        "(key ValuationDate + Portfolio + ISIN differs).",
        [])


def describe_n04(data: dict, config: dict) -> dict:
    positions = data["positions"]
    equity = positions[positions["AssetType"] == "CLO Equity"]
    require(equity["Rating"].isna().all(), "N04", "a CLO Equity position has a rating")
    return manifest_row(
        config, "N04", "positions", "CLO_GLOBAL_02; MULTI_USD_04", "CLO Equity", "EUR; USD",
        "", "Rating", None, None, "MISSING_RATING", "", 0,
        f"All CLO Equity positions ({len(equity)}), no rating: legitimate, equity tranches "
        "are not rated. Existing rows, unchanged.",
        [])


CASES = [
    inject_a01, inject_a02, inject_a03, inject_a04, inject_a05, inject_a06, inject_a07,
    inject_a08, inject_a09, inject_a10, inject_a11, inject_a12, inject_a13, inject_a14,
    inject_a15, inject_n01, inject_n02, describe_n03, describe_n04,
]


# -----------------------------------------------------------------------------
# Checks on the cases
# -----------------------------------------------------------------------------
def check_one_case_per_security(manifest: pd.DataFrame) -> None:
    cases_by_isin = {}
    for _, row in manifest.iterrows():
        for isin in {isin for _, _, isin in row["_touches"]}:
            cases_by_isin.setdefault(isin, []).append(row["AnomalyID"])
    repeated = {isin: cases for isin, cases in cases_by_isin.items() if len(cases) > 1}
    if repeated:
        raise InjectionError(f"Securities used by more than one case: {repeated}")
    shared = sorted(set(cases_by_isin) & SHARED_ISINS)
    if shared:
        raise InjectionError(f"Shared ISINs touched: {shared}")


def check_distribution(manifest: pd.DataFrame) -> None:
    anomalies = manifest[manifest["ExpectedExceptionCount"] > 0]
    if len(anomalies) != 15:
        raise InjectionError(f"Expected 15 anomalies, found {len(anomalies)}")
    for column, expected in EXPECTED_DISTRIBUTION.items():
        actual = anomalies[column].value_counts().to_dict()
        if actual != expected:
            raise InjectionError(f"Distribution by {column}: {actual} != agreed {expected}")


# -----------------------------------------------------------------------------
# Row-by-row comparison of data/clean/ and data/inputs/
# -----------------------------------------------------------------------------
def row_text(row: pd.Series) -> str:
    return " | ".join(as_text(value) if not isinstance(value, float) else repr(value)
                      for value in row)


def compare_rows(clean: pd.DataFrame, inputs: pd.DataFrame) -> list:
    """List of (kind, clean_row, input_row); kind is changed, added or deleted."""
    clean_rows = [row for _, row in clean.iterrows()]
    input_rows = [row for _, row in inputs.iterrows()]
    matcher = difflib.SequenceMatcher(None, [row_text(row) for row in clean_rows],
                                      [row_text(row) for row in input_rows], autojunk=False)
    differences = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and i2 - i1 == j2 - j1:
            differences += [("changed", clean_rows[i], input_rows[j])
                            for i, j in zip(range(i1, i2), range(j1, j2))]
            continue
        differences += [("deleted", clean_rows[i], None) for i in range(i1, i2)]
        differences += [("added", None, input_rows[j]) for j in range(j1, j2)]
    return differences


def row_keys(file: str, row) -> set:
    if row is None:
        return set()
    portfolio = row["Portfolio"] if "Portfolio" in row.index else None
    return {(file, portfolio, row["ISIN"])}


def attribute(file: str, clean_row, input_row, manifest: pd.DataFrame) -> str:
    keys = row_keys(file, clean_row) | row_keys(file, input_row)
    owners = [row["AnomalyID"] for _, row in manifest.iterrows()
              if keys & set(row["_touches"])]
    if len(owners) != 1:
        shown = clean_row if clean_row is not None else input_row
        raise InjectionError(f"Difference in {file} not owned by exactly one case "
                             f"(owners: {owners}):\n{shown.to_string()}")
    return owners[0]


def verify_differences(clean: dict, inputs: dict, manifest: pd.DataFrame) -> pd.DataFrame:
    records = []
    for file in FILE_NAMES:
        for kind, clean_row, input_row in compare_rows(clean[file], inputs[file]):
            owner = attribute(file, clean_row, input_row, manifest)
            records.append({"AnomalyID": owner, "File": file, "Kind": kind})
    summary = pd.DataFrame(records).value_counts().rename("Rows").reset_index()
    changing_cases = manifest.loc[manifest["_touches"].map(len) > 0, "AnomalyID"]
    silent = sorted(set(changing_cases) - set(summary["AnomalyID"]))
    if silent:
        raise InjectionError(f"Cases without any difference in the files: {silent}")
    return summary.sort_values(["AnomalyID", "File", "Kind"], ignore_index=True)


# -----------------------------------------------------------------------------
# Files
# -----------------------------------------------------------------------------
def hash_files(paths: list) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def save_manifest(manifest: pd.DataFrame, path: Path) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        manifest.to_excel(writer, sheet_name="anomaly_manifest", index=False)
        sheet = writer.sheets["anomaly_manifest"]
        for cells in sheet.columns:
            width = max(len(str(cell.value or "")) for cell in cells)
            sheet.column_dimensions[cells[0].column_letter].width = min(width + 2, 60)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main() -> None:
    config = load_config(CONFIG_PATH)
    clean_paths = [CLEAN_DIR / f"{name}.xlsx" for name in FILE_NAMES]
    clean_hash = hash_files(clean_paths)
    clean = {name: read_workbook(CLEAN_DIR / f"{name}.xlsx", name) for name in FILE_NAMES}

    data = {name: frame.copy() for name, frame in clean.items()}
    manifest = pd.DataFrame([case(data, config) for case in CASES])
    check_one_case_per_security(manifest)
    check_distribution(manifest)

    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in FILE_NAMES:
        save_workbook(data[name], INPUT_DIR / f"{name}.xlsx", name)
    save_manifest(manifest[MANIFEST_COLUMNS], MANIFEST_PATH)

    inputs = {name: read_workbook(INPUT_DIR / f"{name}.xlsx", name) for name in FILE_NAMES}
    differences = verify_differences(clean, inputs, manifest)
    if hash_files(clean_paths) != clean_hash:
        raise InjectionError("data/clean/ was modified")

    saved_manifest = pd.read_excel(MANIFEST_PATH, dtype=object)
    print_report(clean, inputs, manifest, differences)
    print(f"\nData fingerprint (inputs + manifest): "
          f"{fingerprint({**inputs, 'anomaly_manifest': saved_manifest})}")


def print_report(clean: dict, inputs: dict, manifest: pd.DataFrame,
                 differences: pd.DataFrame) -> None:
    print("Rows (clean -> inputs): " + "  ".join(
        f"{name} {len(clean[name])} -> {len(inputs[name])}" for name in FILE_NAMES))
    anomalies = manifest[manifest["ExpectedExceptionCount"] > 0]
    for column in EXPECTED_DISTRIBUTION:
        counts = anomalies[column].value_counts().to_dict()
        print(f"Anomalies by {column}: {counts}  (agreed)")
    print(f"Expected exceptions: {manifest['ExpectedExceptionCount'].sum()}  "
          f"(N cases: {(manifest['ExpectedExceptionCount'] == 0).sum()}, 0 expected)")
    print("\n--- Differences data/clean -> data/inputs, all owned by a manifest case ---")
    print(differences.pivot_table(index="AnomalyID", columns=["File", "Kind"], values="Rows",
                                  fill_value=0, aggfunc="sum").to_string())
    print("\nFiles written:")
    for path in [INPUT_DIR / f"{name}.xlsx" for name in FILE_NAMES] + [MANIFEST_PATH]:
        print(f"  {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
