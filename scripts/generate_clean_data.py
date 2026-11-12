"""Generate the CLEAN synthetic input files (no anomaly at all).

Outputs data/clean/positions.xlsx, previous_prices.xlsx and cashflows.xlsx.
The contract is docs/data_spec.md; the 11 invariants of its section 4 are
checked before saving and again on the saved files. Any failure stops the
script with CleanDataError: data is never repaired silently.

Anomalies are injected by a separate script into copies of these files.
This script must never be used for that.

Run: python scripts/generate_clean_data.py
"""

import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# -----------------------------------------------------------------------------
# Paths and global settings
# -----------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
OUTPUT_DIR = REPO_ROOT / "data" / "clean"

SEED = 42
VALUATION_DATE = pd.Timestamp("2026-11-11")

# -----------------------------------------------------------------------------
# Simulation parameters (docs/data_spec.md sections 1, 2, 3 and 5).
# These are simulation choices, not control rules: thresholds, required
# columns, allowed values and keys are read from config.yaml.
# -----------------------------------------------------------------------------
PORTFOLIO_RULES = {
    "CLO_EURO_01": {"asset_types": {"CLO Debt"}, "currencies": {"EUR"}},
    "CLO_GLOBAL_02": {"asset_types": {"CLO Debt", "CLO Equity"}, "currencies": {"EUR", "USD"}},
    "ABS_EUROPE_03": {"asset_types": {"ABS"}, "currencies": {"EUR", "GBP"}},
    "MULTI_USD_04": {"asset_types": {"CLO Debt", "CLO Equity", "ABS"}, "currencies": {"USD"}},
}

# Distinct securities: (home portfolio, asset type, currency, count). 48 in total.
SECURITY_LAYOUT = [
    ("CLO_EURO_01", "CLO Debt", "EUR", 12),
    ("CLO_GLOBAL_02", "CLO Debt", "EUR", 5),
    ("CLO_GLOBAL_02", "CLO Debt", "USD", 4),
    ("CLO_GLOBAL_02", "CLO Equity", "EUR", 2),
    ("CLO_GLOBAL_02", "CLO Equity", "USD", 3),
    ("ABS_EUROPE_03", "ABS", "EUR", 7),
    ("ABS_EUROPE_03", "ABS", "GBP", 5),
    ("MULTI_USD_04", "CLO Debt", "USD", 3),
    ("MULTI_USD_04", "CLO Equity", "USD", 3),
    ("MULTI_USD_04", "ABS", "USD", 4),
]

# The first 2 USD CLO Debt securities of CLO_GLOBAL_02 are also held in MULTI_USD_04.
SHARED_FROM_PORTFOLIO = "CLO_GLOBAL_02"
SHARED_TO_PORTFOLIO = "MULTI_USD_04"
SHARED_ASSET_TYPE = "CLO Debt"
SHARED_CURRENCY = "USD"
SHARED_SECURITY_COUNT = 2

ALL_RATINGS = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"]
RATINGS_BY_ASSET_TYPE = {
    "CLO Debt": ["AAA", "AA", "A", "BBB", "BB", "B"],
    "ABS": ["AAA", "AA", "A", "BBB"],
    "CLO Equity": [],
}

# Previous price range (min, max) by asset type and rating.
PRICE_RANGES = {
    ("CLO Debt", "AAA"): (97.0, 101.0),
    ("CLO Debt", "AA"): (97.0, 101.0),
    ("CLO Debt", "A"): (93.0, 100.0),
    ("CLO Debt", "BBB"): (93.0, 100.0),
    ("CLO Debt", "BB"): (85.0, 97.0),
    ("CLO Debt", "B"): (85.0, 97.0),
    ("ABS", "AAA"): (97.0, 101.0),
    ("ABS", "AA"): (97.0, 101.0),
    ("ABS", "A"): (95.0, 100.0),
    ("ABS", "BBB"): (95.0, 100.0),
    ("CLO Equity", None): (30.0, 90.0),
}

# Simulated returns: normal(0, sigma), redrawn until |r| <= bound (truncated).
RETURN_SIGMA = {"CLO Debt": 0.015, "ABS": 0.01, "CLO Equity": 0.05}
TRUNCATION_BOUND = {"CLO Debt": 0.03, "ABS": 0.03, "CLO Equity": 0.10}
ROUNDING_TOLERANCE_PCT = 0.02  # percentage point, see data_spec.md section 2

BOOK_PRICE_SPREAD = 2.5  # BookPrice = MarketPrice + U(-2.5, +2.5)
BOOK_PRICE_MAX_GAP = 3.0  # invariant 6: |BookPrice - MarketPrice| <= 3

# Nominal (min, max, rounding step) by asset type.
NOMINAL_RANGES = {
    "CLO Debt": (1_000_000, 10_000_000, 250_000),
    "ABS": (1_000_000, 10_000_000, 250_000),
    "CLO Equity": (500_000, 5_000_000, 100_000),
}

# Maturity: valuation date + (min, max) years.
MATURITY_YEARS = {"CLO Debt": (5, 12), "CLO Equity": (5, 12), "ABS": (3, 8)}

VINTAGE_YEARS = (2019, 2025)
DEAL_WORDS = [
    "Aurora", "Boreal", "Cassis", "Delta Vale", "Elmstone", "Fjord", "Granite Bay",
    "Harbourline", "Iris Peak", "Juniper", "Kestrel", "Larchmont", "Meridian Oak",
    "Nimbus", "Orchard Hill", "Pinecrest", "Quarry Lane", "Riverbend", "Saltmarsh",
    "Tidewater", "Umber Ridge", "Valemont", "Willowbrook", "Yarrow",
]
TRANCHE_BY_RATING = {"AAA": "A", "AA": "B", "A": "C", "BBB": "D", "BB": "E", "B": "F"}
EQUITY_TRANCHE = "Sub"
ABS_KINDS = ["Auto", "Consumer", "Leasing"]

PRICE_SOURCES = ["Pricing Agent", "Broker Quote", "Evaluated Price"]
PREVIOUS_PRICE_KEY = ["ISIN", "PreviousDate"]
LATEST_PRICE_LAG_DAYS = 7
OLDER_PRICE_LAG_DAYS = 14
OLDER_PRICE_SHARE = 0.5

# Annual coupon rate (min, max). CLO Equity: distribution on market value.
COUPON_RATE_RANGES = {"CLO Debt": (0.04, 0.09), "ABS": (0.04, 0.09), "CLO Equity": (0.12, 0.20)}
PRINCIPAL_PCT_RANGE = (0.01, 0.05)  # ABS amortisation per payment, % of Nominal
NEXT_COUPON_MAX_OFFSET_DAYS = 85
COUPON_PERIOD_MONTHS = 3

FEE_SHARE = 0.25
FEE_RANGE = (-10_000.0, -2_000.0)
FEE_OFFSET_DAYS = (1, 60)

CONFIRMED_WINDOW_DAYS = 10  # Confirmed: VD to VD + 10 days inclusive

ISIN_PATTERN = re.compile(r"^SYN\d{9}$")

# Column types (data_spec.md): used by invariant 1 and to read files back.
COLUMN_TYPES = {
    "positions": {
        "ValuationDate": "date", "Portfolio": "text", "ISIN": "text",
        "SecurityName": "text", "AssetType": "text", "Currency": "text",
        "Nominal": "number", "BookPrice": "number", "MarketPrice": "number",
        "Rating": "text", "MaturityDate": "date",
    },
    "previous_prices": {
        "ISIN": "text", "PreviousDate": "date", "PreviousPrice": "number",
        "PriceSource": "text",
    },
    "cashflows": {
        "Portfolio": "text", "ISIN": "text", "CashflowDate": "date",
        "CashflowType": "text", "Currency": "text", "Amount": "number",
        "Status": "text",
    },
}


class CleanDataError(Exception):
    """Raised when the clean dataset breaks an invariant of data_spec.md."""


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


# -----------------------------------------------------------------------------
# Securities
# -----------------------------------------------------------------------------
def make_isin(number: int) -> str:
    return f"SYN{number:09d}"


def draw_rating(asset_type: str, rng: np.random.Generator):
    ratings = RATINGS_BY_ASSET_TYPE[asset_type]
    if not ratings:
        return None
    return str(rng.choice(ratings))


def draw_previous_price(asset_type: str, rating, rng: np.random.Generator) -> float:
    low, high = PRICE_RANGES[(asset_type, rating)]
    # Rounded to 2 decimals BEFORE MarketPrice is derived (rounding tolerance).
    return round(float(rng.uniform(low, high)), 2)


def draw_truncated_return(asset_type: str, rng: np.random.Generator) -> float:
    sigma = RETURN_SIGMA[asset_type]
    bound = TRUNCATION_BOUND[asset_type]
    while True:
        simulated_return = float(rng.normal(0.0, sigma))
        if abs(simulated_return) <= bound:
            return simulated_return


def derive_market_price(previous_price: float, asset_type: str, rng: np.random.Generator) -> float:
    simulated_return = draw_truncated_return(asset_type, rng)
    return round(previous_price * (1 + simulated_return), 2)


def draw_maturity_date(asset_type: str, rng: np.random.Generator) -> pd.Timestamp:
    min_years, max_years = MATURITY_YEARS[asset_type]
    earliest = VALUATION_DATE + pd.DateOffset(years=min_years)
    latest = VALUATION_DATE + pd.DateOffset(years=max_years)
    offset_days = int(rng.integers(0, (latest - earliest).days + 1))
    return earliest + pd.Timedelta(days=offset_days)


def make_security_name(asset_type: str, rating, index: int, rng: np.random.Generator) -> str:
    # (deal word, series) is unique per index, so names are unique.
    word = DEAL_WORDS[index % len(DEAL_WORDS)]
    series = index // len(DEAL_WORDS) + 1
    vintage = int(rng.integers(VINTAGE_YEARS[0], VINTAGE_YEARS[1] + 1))
    if asset_type == "CLO Equity":
        return f"{word} CLO {vintage}-{series} {EQUITY_TRANCHE}"
    tranche = TRANCHE_BY_RATING[rating]
    if asset_type == "ABS":
        kind = str(rng.choice(ABS_KINDS))
        return f"{word} {kind} ABS {vintage}-{series} {tranche}"
    return f"{word} CLO {vintage}-{series} {tranche}"


def build_one_security(index: int, portfolio: str, asset_type: str, currency: str,
                       rng: np.random.Generator) -> dict:
    rating = draw_rating(asset_type, rng)
    previous_price = draw_previous_price(asset_type, rating, rng)
    rate_low, rate_high = COUPON_RATE_RANGES[asset_type]
    return {
        "ISIN": make_isin(index + 1),
        "HomePortfolio": portfolio,
        "SecurityName": make_security_name(asset_type, rating, index, rng),
        "AssetType": asset_type,
        "Currency": currency,
        "Rating": rating,
        "MaturityDate": draw_maturity_date(asset_type, rng),
        "PreviousPrice": previous_price,
        "MarketPrice": derive_market_price(previous_price, asset_type, rng),
        "CouponRate": float(rng.uniform(rate_low, rate_high)),
        "NextCouponDate": VALUATION_DATE
        + pd.Timedelta(days=int(rng.integers(0, NEXT_COUPON_MAX_OFFSET_DAYS + 1))),
    }


def build_securities(rng: np.random.Generator) -> pd.DataFrame:
    """One row per distinct security, with its security-level attributes."""
    rows = []
    for portfolio, asset_type, currency, count in SECURITY_LAYOUT:
        for _ in range(count):
            rows.append(build_one_security(len(rows), portfolio, asset_type, currency, rng))
    return pd.DataFrame(rows)


# -----------------------------------------------------------------------------
# Positions
# -----------------------------------------------------------------------------
def draw_nominal(asset_type: str, rng: np.random.Generator) -> float:
    low, high, step = NOMINAL_RANGES[asset_type]
    return float(rng.integers(low // step, high // step + 1) * step)


def draw_book_price(market_price: float, rng: np.random.Generator) -> float:
    return round(market_price + float(rng.uniform(-BOOK_PRICE_SPREAD, BOOK_PRICE_SPREAD)), 2)


def shared_isins(securities: pd.DataFrame) -> list:
    candidates = securities[
        (securities["HomePortfolio"] == SHARED_FROM_PORTFOLIO)
        & (securities["AssetType"] == SHARED_ASSET_TYPE)
        & (securities["Currency"] == SHARED_CURRENCY)
    ]
    return candidates["ISIN"].head(SHARED_SECURITY_COUNT).tolist()


def position_row(security: pd.Series, portfolio: str, rng: np.random.Generator) -> dict:
    return {
        "ValuationDate": VALUATION_DATE,
        "Portfolio": portfolio,
        "ISIN": security["ISIN"],
        "SecurityName": security["SecurityName"],
        "AssetType": security["AssetType"],
        "Currency": security["Currency"],
        "Nominal": draw_nominal(security["AssetType"], rng),
        "BookPrice": draw_book_price(security["MarketPrice"], rng),
        "MarketPrice": security["MarketPrice"],
        "Rating": security["Rating"],
        "MaturityDate": security["MaturityDate"],
    }


def build_positions(securities: pd.DataFrame, rng: np.random.Generator,
                    columns: list) -> pd.DataFrame:
    rows = [position_row(security, security["HomePortfolio"], rng)
            for _, security in securities.iterrows()]
    for isin in shared_isins(securities):
        security = securities.loc[securities["ISIN"] == isin].iloc[0]
        rows.append(position_row(security, SHARED_TO_PORTFOLIO, rng))
    positions = pd.DataFrame(rows)
    positions = positions.sort_values(["Portfolio", "ISIN"], ignore_index=True)
    return positions[columns]


# -----------------------------------------------------------------------------
# Previous prices
# -----------------------------------------------------------------------------
def build_previous_prices(securities: pd.DataFrame, rng: np.random.Generator,
                          columns: list) -> pd.DataFrame:
    latest_date = VALUATION_DATE - pd.Timedelta(days=LATEST_PRICE_LAG_DAYS)
    older_date = VALUATION_DATE - pd.Timedelta(days=OLDER_PRICE_LAG_DAYS)
    rows = []
    for _, security in securities.iterrows():
        rows.append({
            "ISIN": security["ISIN"],
            "PreviousDate": latest_date,
            "PreviousPrice": security["PreviousPrice"],
            "PriceSource": str(rng.choice(PRICE_SOURCES)),
        })
        if rng.random() < OLDER_PRICE_SHARE:
            older_return = draw_truncated_return(security["AssetType"], rng)
            rows.append({
                "ISIN": security["ISIN"],
                "PreviousDate": older_date,
                "PreviousPrice": round(security["PreviousPrice"] / (1 + older_return), 2),
                "PriceSource": str(rng.choice(PRICE_SOURCES)),
            })
    return pd.DataFrame(rows)[columns]


# -----------------------------------------------------------------------------
# Cashflows
# -----------------------------------------------------------------------------
def cashflow_status(cashflow_date: pd.Timestamp) -> str:
    """Clean-data convention only (data_spec.md section 3), not a control."""
    if cashflow_date < VALUATION_DATE:
        return "Paid"
    if cashflow_date <= VALUATION_DATE + pd.Timedelta(days=CONFIRMED_WINDOW_DAYS):
        return "Confirmed"
    return "Forecast"


def coupon_dates(next_coupon_date: pd.Timestamp) -> list:
    """Previous (past), next and following quarterly coupon dates."""
    period = pd.DateOffset(months=COUPON_PERIOD_MONTHS)
    return [next_coupon_date - period, next_coupon_date, next_coupon_date + period]


def cashflow_row(position: pd.Series, cashflow_date: pd.Timestamp,
                 cashflow_type: str, amount: float) -> dict:
    return {
        "Portfolio": position["Portfolio"],
        "ISIN": position["ISIN"],
        "CashflowDate": cashflow_date,
        "CashflowType": cashflow_type,
        "Currency": position["Currency"],
        "Amount": round(amount, 2),
        "Status": cashflow_status(cashflow_date),
    }


def coupon_amount(position: pd.Series, security: pd.Series) -> float:
    quarterly_rate = security["CouponRate"] / 4
    if position["AssetType"] == "CLO Equity":
        market_value = position["Nominal"] * position["MarketPrice"] / 100
        return market_value * quarterly_rate
    return position["Nominal"] * quarterly_rate


def build_coupon_rows(position: pd.Series, security: pd.Series, dates: list) -> list:
    amount = coupon_amount(position, security)
    return [cashflow_row(position, date, "Coupon", amount) for date in dates]


def build_principal_rows(position: pd.Series, dates: list, rng: np.random.Generator) -> list:
    if position["AssetType"] != "ABS":
        return []
    rows = []
    for date in dates:
        pct = float(rng.uniform(*PRINCIPAL_PCT_RANGE))
        rows.append(cashflow_row(position, date, "Principal", position["Nominal"] * pct))
    return rows


def build_fee_rows(position: pd.Series, rng: np.random.Generator) -> list:
    if rng.random() >= FEE_SHARE:
        return []
    offset_days = int(rng.integers(FEE_OFFSET_DAYS[0], FEE_OFFSET_DAYS[1] + 1))
    fee_date = VALUATION_DATE + pd.Timedelta(days=offset_days)
    amount = float(rng.uniform(*FEE_RANGE))
    return [cashflow_row(position, fee_date, "Fee", amount)]


def build_cashflows(positions: pd.DataFrame, securities: pd.DataFrame,
                    rng: np.random.Generator, columns: list) -> pd.DataFrame:
    security_by_isin = securities.set_index("ISIN")
    rows = []
    for _, position in positions.iterrows():
        security = security_by_isin.loc[position["ISIN"]]
        dates = coupon_dates(security["NextCouponDate"])
        rows += build_coupon_rows(position, security, dates)
        rows += build_principal_rows(position, dates, rng)
        rows += build_fee_rows(position, rng)
    cashflows = pd.DataFrame(rows)
    cashflows = cashflows.sort_values(
        ["Portfolio", "ISIN", "CashflowDate", "CashflowType"], ignore_index=True)
    return cashflows[columns]


# -----------------------------------------------------------------------------
# Invariant checks (data_spec.md section 4). Each raises CleanDataError.
# -----------------------------------------------------------------------------
def fail_if_any(offending: pd.DataFrame, invariant: int, message: str) -> None:
    if len(offending) > 0:
        raise CleanDataError(
            f"Invariant {invariant} failed: {message} ({len(offending)} row(s))\n"
            f"{offending.head(10).to_string()}"
        )


def is_missing(series: pd.Series) -> pd.Series:
    """NaN, None, or an empty / whitespace-only string."""
    as_text = series.astype("object").where(series.notna(), "")
    return series.isna() | (as_text.astype(str).str.strip() == "")


def check_required_columns_and_types(files: dict, config: dict) -> None:
    """Invariant 1."""
    for name, data in files.items():
        required = config["required_columns"][name]
        if list(data.columns) != required:
            raise CleanDataError(
                f"Invariant 1 failed: {name} columns {list(data.columns)} != {required}")
        for column, kind in COLUMN_TYPES[name].items():
            series = data[column]
            if kind == "date":
                ok = (pd.api.types.is_datetime64_any_dtype(series)
                      and (series.dropna() == series.dropna().dt.normalize()).all())
            elif kind == "number":
                ok = pd.api.types.is_float_dtype(series)
            else:
                ok = series.dropna().map(lambda value: isinstance(value, str)).all()
            if not ok:
                raise CleanDataError(
                    f"Invariant 1 failed: {name}.{column} is not of type {kind} "
                    f"(dtype {series.dtype})")


def check_unique_keys(positions: pd.DataFrame, previous_prices: pd.DataFrame,
                      config: dict) -> None:
    """Invariant 2."""
    position_key = config["keys"]["position_unique"]
    fail_if_any(positions[positions.duplicated(position_key, keep=False)], 2,
                f"duplicate positions on {position_key}")
    fail_if_any(previous_prices[previous_prices.duplicated(PREVIOUS_PRICE_KEY, keep=False)], 2,
                f"duplicate previous prices on {PREVIOUS_PRICE_KEY}")


def check_missing_values(files: dict) -> None:
    """Invariant 3: no missing value, except Rating on CLO Equity."""
    for name, data in files.items():
        for column in data.columns:
            missing = is_missing(data[column])
            if name == "positions" and column == "Rating":
                missing = missing & (data["AssetType"] != "CLO Equity")
            fail_if_any(data[missing], 3, f"missing {name}.{column}")


def check_allowed_values_and_portfolio_layout(files: dict, config: dict) -> None:
    """Invariant 4."""
    allowed = dict(config["allowed_values"])
    allowed["Portfolio"] = list(PORTFOLIO_RULES)
    allowed["PriceSource"] = PRICE_SOURCES
    for name, data in files.items():
        for column, values in allowed.items():
            if column in data.columns:
                fail_if_any(data[~data[column].isin(values)], 4,
                            f"{name}.{column} outside {values}")
        bad_isin = ~data["ISIN"].map(lambda isin: bool(ISIN_PATTERN.match(str(isin))))
        fail_if_any(data[bad_isin], 4, f"{name}.ISIN does not match {ISIN_PATTERN.pattern}")
    positions = files["positions"]
    rating_present = positions["Rating"].notna()
    fail_if_any(positions[rating_present & ~positions["Rating"].isin(ALL_RATINGS)], 4,
                f"Rating outside {ALL_RATINGS}")
    outside_layout = positions.apply(
        lambda row: row["AssetType"] not in PORTFOLIO_RULES[row["Portfolio"]]["asset_types"]
        or row["Currency"] not in PORTFOLIO_RULES[row["Portfolio"]]["currencies"], axis=1)
    fail_if_any(positions[outside_layout], 4, "asset type or currency not allowed in portfolio")


def check_ratings(positions: pd.DataFrame, config: dict) -> None:
    """Invariant 5: filled for CLO Debt and ABS (within their scale), empty for CLO Equity."""
    rated = positions["AssetType"].isin(config["rating_required_for"])
    unrated_expected = positions[rated & is_missing(positions["Rating"])]
    fail_if_any(unrated_expected, 5, "missing rating on CLO Debt or ABS")
    outside_scale = positions[rated].apply(
        lambda row: row["Rating"] not in RATINGS_BY_ASSET_TYPE[row["AssetType"]], axis=1)
    fail_if_any(positions[rated][outside_scale], 5, "rating outside the clean scale")
    fail_if_any(positions[~rated & ~is_missing(positions["Rating"])], 5,
                "rating filled on CLO Equity")


def check_positive_values_and_book_spread(positions: pd.DataFrame,
                                          previous_prices: pd.DataFrame) -> None:
    """Invariant 6."""
    for column in ["Nominal", "BookPrice", "MarketPrice"]:
        fail_if_any(positions[~(positions[column] > 0)], 6, f"{column} not > 0")
    fail_if_any(previous_prices[~(previous_prices["PreviousPrice"] > 0)], 6,
                "PreviousPrice not > 0")
    gap = (positions["BookPrice"] - positions["MarketPrice"]).abs()
    fail_if_any(positions[gap > BOOK_PRICE_MAX_GAP], 6,
                f"|BookPrice - MarketPrice| > {BOOK_PRICE_MAX_GAP}")


def check_dates(positions: pd.DataFrame, previous_prices: pd.DataFrame) -> None:
    """Invariant 7."""
    fail_if_any(positions[positions["ValuationDate"] != VALUATION_DATE], 7,
                f"ValuationDate != {VALUATION_DATE.date()}")
    fail_if_any(positions[~(positions["MaturityDate"] > VALUATION_DATE)], 7,
                "MaturityDate not after the valuation date")
    fail_if_any(previous_prices[~(previous_prices["PreviousDate"] < VALUATION_DATE)], 7,
                "PreviousDate not before the valuation date")


def latest_previous_prices(previous_prices: pd.DataFrame) -> pd.DataFrame:
    """Latest PreviousPrice strictly before the valuation date, per ISIN."""
    before = previous_prices[previous_prices["PreviousDate"] < VALUATION_DATE]
    latest = before.sort_values("PreviousDate").groupby("ISIN").tail(1)
    return latest[["ISIN", "PreviousDate", "PreviousPrice"]]


def price_moves(positions: pd.DataFrame, previous_prices: pd.DataFrame) -> pd.DataFrame:
    moves = positions.merge(latest_previous_prices(previous_prices), on="ISIN", how="left")
    moves["PriceChangePct"] = ((moves["MarketPrice"] - moves["PreviousPrice"])
                               / moves["PreviousPrice"] * 100)
    return moves


def check_price_history_and_movements(positions: pd.DataFrame,
                                      previous_prices: pd.DataFrame, config: dict) -> None:
    """Invariant 8."""
    latest_date = VALUATION_DATE - pd.Timedelta(days=LATEST_PRICE_LAG_DAYS)
    priced_latest = set(previous_prices.loc[previous_prices["PreviousDate"] == latest_date, "ISIN"])
    fail_if_any(positions[~positions["ISIN"].isin(priced_latest)], 8,
                f"position without a previous price on {latest_date.date()}")
    fail_if_any(previous_prices[~previous_prices["ISIN"].isin(positions["ISIN"])], 8,
                "previous price for an ISIN that is not held")
    moves = price_moves(positions, previous_prices)
    bound_pct = moves["AssetType"].map(TRUNCATION_BOUND) * 100 + ROUNDING_TOLERANCE_PCT
    threshold_pct = moves["AssetType"].map(config["price_move_thresholds_pct"])
    fail_if_any(moves[moves["PriceChangePct"].abs() > bound_pct], 8,
                "price movement beyond truncation bound + rounding tolerance")
    fail_if_any(moves[~(moves["PriceChangePct"].abs() < threshold_pct)], 8,
                "price movement not strictly below its config.yaml threshold")


def check_cashflow_links(positions: pd.DataFrame, cashflows: pd.DataFrame,
                         config: dict) -> None:
    """Invariant 9."""
    link = config["keys"]["cashflow_to_position"]
    held = positions[link + ["Currency", "MaturityDate"]].rename(
        columns={"Currency": "PositionCurrency"})
    linked = cashflows.merge(held, on=link, how="left", indicator=True)
    fail_if_any(linked[linked["_merge"] != "both"], 9, f"cashflow without position on {link}")
    fail_if_any(linked[linked["Currency"] != linked["PositionCurrency"]], 9,
                "cashflow currency differs from position currency")
    fail_if_any(linked[~(linked["CashflowDate"] <= linked["MaturityDate"])], 9,
                "cashflow dated after the position MaturityDate")
    inflow = linked["CashflowType"].isin(["Coupon", "Principal"])
    fail_if_any(linked[inflow & ~(linked["Amount"] > 0)], 9, "Coupon/Principal amount not > 0")
    fee = linked["CashflowType"] == "Fee"
    fail_if_any(linked[fee & ~(linked["Amount"] < 0)], 9, "Fee amount not < 0")


def check_cashflow_statuses(cashflows: pd.DataFrame) -> None:
    """Invariant 10, recomputed independently of cashflow_status()."""
    dates = cashflows["CashflowDate"]
    confirmed_end = VALUATION_DATE + pd.Timedelta(days=CONFIRMED_WINDOW_DAYS)
    expected = np.select(
        [dates < VALUATION_DATE, dates <= confirmed_end], ["Paid", "Confirmed"], "Forecast")
    fail_if_any(cashflows[cashflows["Status"] != expected], 10,
                "Status inconsistent with CashflowDate")


def check_no_duplicate_cashflows(cashflows: pd.DataFrame, config: dict) -> None:
    """Invariant 11."""
    key = config["keys"]["cashflow_unique"]
    fail_if_any(cashflows[cashflows.duplicated(key, keep=False)], 11,
                f"duplicate cashflows on {key}")


INVARIANT_LABELS = {
    1: "required columns present, with the expected types",
    2: "position and previous-price keys unique",
    3: "no missing value (except Rating on CLO Equity)",
    4: "allowed values and portfolio layout",
    5: "Rating filled for CLO Debt/ABS, empty for CLO Equity",
    6: "positive amounts/prices, BookPrice within 3 points",
    7: "MaturityDate > VD, PreviousDate < VD",
    8: "price history complete, movements within bound and below threshold",
    9: "cashflows linked, same currency, before maturity, sign convention",
    10: "cashflow Status coherent with CashflowDate",
    11: "no duplicate cashflow",
}


def run_all_checks(positions: pd.DataFrame, previous_prices: pd.DataFrame,
                   cashflows: pd.DataFrame, config: dict) -> list:
    files = {"positions": positions, "previous_prices": previous_prices, "cashflows": cashflows}
    check_required_columns_and_types(files, config)
    check_unique_keys(positions, previous_prices, config)
    check_missing_values(files)
    check_allowed_values_and_portfolio_layout(files, config)
    check_ratings(positions, config)
    check_positive_values_and_book_spread(positions, previous_prices)
    check_dates(positions, previous_prices)
    check_price_history_and_movements(positions, previous_prices, config)
    check_cashflow_links(positions, cashflows, config)
    check_cashflow_statuses(cashflows)
    check_no_duplicate_cashflows(cashflows, config)
    return [f"Invariant {number:>2}: PASS  {label}" for number, label in INVARIANT_LABELS.items()]


def cashflow_bucket(cashflows: pd.DataFrame, config: dict) -> pd.Series:
    horizon_end = VALUATION_DATE + pd.Timedelta(days=config["cashflow_horizon_days"])
    dates = cashflows["CashflowDate"]
    return pd.Series(np.select(
        [dates < VALUATION_DATE, dates <= horizon_end],
        ["past", f"VD..VD+{config['cashflow_horizon_days']}"],
        f"> VD+{config['cashflow_horizon_days']}"), index=cashflows.index)


def check_cashflow_coverage(cashflows: pd.DataFrame, config: dict) -> None:
    """Not an invariant: the dataset must cover past, upcoming and later cashflows."""
    buckets = set(cashflow_bucket(cashflows, config))
    if len(buckets) < 3:
        raise CleanDataError(f"Cashflow coverage incomplete: only {sorted(buckets)}")


# -----------------------------------------------------------------------------
# Saving and reading back
# -----------------------------------------------------------------------------
def save_workbook(data: pd.DataFrame, path: Path, name: str) -> None:
    """One sheet, header on row 1; date columns displayed as YYYY-MM-DD."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        data.to_excel(writer, sheet_name=name, index=False)
        sheet = writer.sheets[name]
        for position, column in enumerate(data.columns, start=1):
            if COLUMN_TYPES[name][column] != "date":
                continue
            for (cell,) in sheet.iter_rows(min_row=2, min_col=position, max_col=position):
                cell.number_format = "YYYY-MM-DD"


def read_workbook(path: Path, name: str) -> pd.DataFrame:
    """Read back with the conventions of data_spec.md (numbers as float64)."""
    numbers = {column: "float64" for column, kind in COLUMN_TYPES[name].items()
               if kind == "number"}
    texts = {column: "object" for column, kind in COLUMN_TYPES[name].items() if kind == "text"}
    return pd.read_excel(path, dtype={**numbers, **texts})


def fingerprint(files: dict) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode())
        digest.update(files[name].to_csv(index=False).encode())
    return digest.hexdigest()[:16]


# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
def print_section(title: str, content) -> None:
    print(f"\n--- {title} ---")
    print(content.to_string() if hasattr(content, "to_string") else content)


def print_summary(positions: pd.DataFrame, previous_prices: pd.DataFrame,
                  cashflows: pd.DataFrame, config: dict) -> None:
    print(f"Valuation date: {VALUATION_DATE.date()}   seed: {SEED}")
    print(f"Rows: positions={len(positions)}  "
          f"(distinct ISINs={positions['ISIN'].nunique()})  "
          f"previous_prices={len(previous_prices)}  cashflows={len(cashflows)}")
    print_section("Positions: Portfolio x AssetType",
                  pd.crosstab(positions["Portfolio"], positions["AssetType"], margins=True))
    print_section("Positions: Portfolio x Currency",
                  pd.crosstab(positions["Portfolio"], positions["Currency"], margins=True))
    print_section("Ratings by AssetType",
                  pd.crosstab(positions["Rating"].fillna("(empty)"), positions["AssetType"]))
    held_twice = positions[positions.duplicated("ISIN", keep=False)]
    print_section("ISINs held in several portfolios",
                  held_twice[["ISIN", "Portfolio", "Nominal", "MarketPrice"]])
    moves = price_moves(positions, previous_prices)
    stats = moves.groupby("AssetType")["PriceChangePct"].agg(["count", "min", "max", "mean"])
    stats["bound_pct"] = stats.index.map(lambda asset: TRUNCATION_BOUND[asset] * 100)
    stats["threshold_pct"] = stats.index.map(config["price_move_thresholds_pct"])
    print_section("PriceChangePct by AssetType (%)", stats.round(3))
    print(f"Securities with an older price (VD-{OLDER_PRICE_LAG_DAYS}): "
          f"{previous_prices['ISIN'].duplicated().sum()}")
    print_section("Cashflows: Status x CashflowType",
                  pd.crosstab(cashflows["Status"], cashflows["CashflowType"], margins=True))
    print_section("Cashflows by date bucket",
                  cashflow_bucket(cashflows, config).value_counts())
    print_section("Cashflows by Currency", cashflows["Currency"].value_counts())


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main() -> None:
    config = load_config(CONFIG_PATH)
    columns = config["required_columns"]
    rng = np.random.default_rng(SEED)

    securities = build_securities(rng)
    positions = build_positions(securities, rng, columns["positions"])
    previous_prices = build_previous_prices(securities, rng, columns["previous_prices"])
    cashflows = build_cashflows(positions, securities, rng, columns["cashflows"])

    run_all_checks(positions, previous_prices, cashflows, config)
    check_cashflow_coverage(cashflows, config)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = {"positions": positions, "previous_prices": previous_prices, "cashflows": cashflows}
    for name, data in files.items():
        save_workbook(data, OUTPUT_DIR / f"{name}.xlsx", name)

    # Re-read the saved files and check them again, as the engine will see them.
    saved = {name: read_workbook(OUTPUT_DIR / f"{name}.xlsx", name) for name in files}
    results = run_all_checks(saved["positions"], saved["previous_prices"],
                             saved["cashflows"], config)

    print_summary(saved["positions"], saved["previous_prices"], saved["cashflows"], config)
    print("\n--- Invariants (checked before saving and on the saved files) ---")
    print("\n".join(results))
    print("\nFiles written:")
    for name in files:
        print(f"  {(OUTPUT_DIR / f'{name}.xlsx').relative_to(REPO_ROOT)}")
    print(f"\nData fingerprint: {fingerprint(saved)}")


if __name__ == "__main__":
    main()
