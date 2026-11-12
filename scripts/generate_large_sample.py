"""Generate the large production-like sample (seed 43).

Same portfolios, rules and invariants as the test fixture (docs/data_spec.md),
10 times more securities. Writes the clean base to data/large_clean/, then
injects anomalies weighted like a production feed into data/large/ and writes
the oracle docs/large_manifest.xlsx (one row per expected control and case).
Expected exceptions follow CLAUDE.md, section "Exception rules".
Run: python scripts/generate_large_sample.py
"""

from itertools import count

import numpy as np
import pandas as pd

from generate_clean_data import (
    CONFIG_PATH,
    REPO_ROOT,
    SECURITY_LAYOUT,
    VALUATION_DATE,
    build_clean_files,
    fingerprint,
    load_config,
    make_isin,
    print_summary,
    read_workbook,
    save_workbook,
    write_and_verify,
)
from inject_anomalies import (
    FILE_NAMES,
    LATEST_PRICE_DATE,
    OLDER_PRICE_DATE,
    MANIFEST_COLUMNS,
    check_one_case_per_security,
    empty_isin,
    find_cashflow,
    find_position,
    find_price,
    inject_price_case,
    insert_after,
    manifest_row,
    require,
    save_manifest,
    set_position_field,
    verify_differences,
)

SEED = 43
SCALE = 10  # securities per layout line, times the fixture
FIRST_ISIN_NUMBER = 1_000_001  # SYN001000001...: never mistaken for a fixture ISIN
SHARED_SECURITY_COUNT = 20  # USD CLO Debt of CLO_GLOBAL_02 also held in MULTI_USD_04
UNKNOWN_ISIN_NUMBER = 1_999_901  # SYN001999901...: securities never held
CLEAN_DIR = REPO_ROOT / "data" / "large_clean"
INPUT_DIR = REPO_ROOT / "data" / "large"
MANIFEST_PATH = REPO_ROOT / "docs" / "large_manifest.xlsx"

LAYOUT = [(portfolio, asset_type, currency, count * SCALE)
          for portfolio, asset_type, currency, count in SECURITY_LAYOUT]

# Agreed single-anomaly cases per control, weighted like a production feed:
# frequent operational issues, rare critical ones.
SINGLE_CASES = {
    "PAST_FORECAST_CASHFLOW": 10, "PRICE_MOVE_ABOVE_THRESHOLD": 10, "MISSING_RATING": 8,
    "MISSING_MARKET_PRICE": 6, "UNMATCHED_CASHFLOW": 6, "NO_PRICE_HISTORY": 5,
    "DUPLICATE_CASHFLOW": 5, "MISSING_VALUE": 5, "INVALID_VALUE": 5, "DUPLICATE_POSITION": 4,
    "MISSING_ISIN": 3, "INVALID_NOMINAL": 3, "INVALID_PRICE": 3, "CURRENCY_MISMATCH": 3,
    "DUPLICATE_PREVIOUS_PRICE": 3,
}
PRICE_MOVE_MIX = ["CLO Debt"] * 4 + ["ABS"] * 3 + ["CLO Equity"] * 3
MOVE_RANGES_PCT = {"CLO Debt": (6.0, 9.0), "ABS": (5.5, 8.0), "CLO Equity": (16.0, 25.0)}
INVALID_NOMINALS = [0.0, -1_000_000.0, -250_000.0]
DUPLICATE_PRICE_DIFFERS = [False, False, True]  # the third copy carries another price
UNKNOWN_CURRENCY = "CHF"
TOP_RATED = {"AAA", "AA"}  # previous price range 97-101, so 100.00 is a valid price
MID_RATED = {"A", "BBB"}  # CLO Debt range 93-100

# Near-boundary price cases, no exception expected (exception rule 7):
# (case, asset type, ratings, previous price, market price).
BOUNDARY_MOVES = [
    ("B01", "CLO Debt", TOP_RATED, 100.00, 95.10),     # -4.9 %
    ("B02", "ABS", TOP_RATED, 100.00, 104.90),         # +4.9 %
    ("B03", "CLO Equity", None, 80.00, 91.92),         # +14.9 %
    ("B04", "CLO Equity", None, 80.00, 68.08),         # -14.9 %
    ("B05", "CLO Debt", TOP_RATED, 100.00, 105.00),    # exactly +5 %
    ("B06", "ABS", TOP_RATED, 100.00, 95.00),          # exactly -5 %
    ("B07", "CLO Equity", None, 80.00, 68.00),         # exactly -15 %
    ("B08", "CLO Debt", MID_RATED, 93.00, 97.65),      # raw 5.000000000000006 %
]


def severity(config: dict, control: str) -> str:
    return config["controls"][control]["severity"]


def held_once(clean: dict) -> pd.DataFrame:
    # Positions of securities held in one portfolio only: a case on a shared
    # security would change several positions.
    positions = clean["positions"]
    return positions[~positions["ISIN"].duplicated(keep=False)]


def pick_position(clean: dict, rng: np.random.Generator, used: set,
                  asset_types: list | None = None, ratings: set | None = None,
                  no_older_price: bool = False) -> pd.Series:
    eligible = held_once(clean)
    eligible = eligible[~eligible["ISIN"].isin(used)]
    if no_older_price:
        # A price case resets the VD-7 price: without a VD-14 price, the
        # history stays plausible (no jump between two previous prices).
        prices = clean["previous_prices"]
        older = prices.loc[prices["PreviousDate"] == OLDER_PRICE_DATE, "ISIN"]
        eligible = eligible[~eligible["ISIN"].isin(older)]
    if asset_types is not None:
        eligible = eligible[eligible["AssetType"].isin(asset_types)]
    if ratings is not None:
        eligible = eligible[eligible["Rating"].isin(ratings)]
    require(not eligible.empty, "pick", f"no free position for {asset_types} {ratings}")
    position = eligible.loc[rng.choice(eligible.index)]
    used.add(position["ISIN"])  # one case per security
    return position


def pick_cashflow(clean: dict, rng: np.random.Generator, used: set, status: str | None = None,
                  cashflow_type: str | None = None, after: pd.Timestamp | None = None
                  ) -> pd.Series:
    positions = held_once(clean).set_index("ISIN")
    cashflows = clean["cashflows"]
    eligible = cashflows[cashflows["ISIN"].isin(positions.index) & ~cashflows["ISIN"].isin(used)]
    if status is not None:
        eligible = eligible[eligible["Status"] == status]
    if cashflow_type is not None:
        eligible = eligible[eligible["CashflowType"] == cashflow_type]
    if after is not None:
        eligible = eligible[eligible["CashflowDate"] > after]
    require(not eligible.empty, "pick", f"no free cashflow ({status}, {cashflow_type})")
    cashflow = eligible.loc[rng.choice(eligible.index)].copy()
    cashflow["AssetType"] = positions.loc[cashflow["ISIN"], "AssetType"]
    used.add(cashflow["ISIN"])
    return cashflow


def latest_price(clean: dict, isin: str) -> float:
    prices = clean["previous_prices"]
    found = prices[(prices["ISIN"] == isin) & (prices["PreviousDate"] == LATEST_PRICE_DATE)]
    return float(found["PreviousPrice"].iloc[0])


def free_date(data: dict, cashflow: pd.Series, candidates: list) -> pd.Timestamp:
    # A new CashflowDate that gives no second row with the same cashflow key
    # (which would add a DUPLICATE_CASHFLOW).
    cashflows = data["cashflows"]
    same = cashflows[(cashflows["Portfolio"] == cashflow["Portfolio"])
                     & (cashflows["ISIN"] == cashflow["ISIN"])
                     & (cashflows["CashflowType"] == cashflow["CashflowType"])]
    free = [date for date in candidates if not (same["CashflowDate"] == date).any()]
    require(bool(free), cashflow["ISIN"], "no free CashflowDate for this cashflow")
    return free[0]


def past_dates(rng: np.random.Generator) -> list:
    return [VALUATION_DATE - pd.Timedelta(days=int(days))
            for days in rng.permutation(np.arange(1, 31))]


# -- Manifest rows -------------------------------------------------------------
def position_row(config: dict, case_id: str, position: pd.Series, field: str, original,
                 injected, control: str, description: str, isin: str | None = None) -> dict:
    return manifest_row(
        config, case_id, "positions", position["Portfolio"], position["AssetType"],
        position["Currency"], position["ISIN"] if isin is None else isin, field, original,
        injected, control, severity(config, control), 1, description,
        [("positions", position["Portfolio"], position["ISIN"])])


def cashflow_row(config: dict, case_id: str, cashflow: pd.Series, field: str, original,
                 injected, control: str, description: str, portfolio: str | None = None,
                 isin: str | None = None, touches: list | None = None, expected: int = 1
                 ) -> dict:
    touched = touches or [("cashflows", cashflow["Portfolio"], cashflow["ISIN"])]
    return manifest_row(
        config, case_id, "cashflows", portfolio or cashflow["Portfolio"], cashflow["AssetType"],
        cashflow["Currency"], isin or cashflow["ISIN"], field, original, injected, control,
        severity(config, control) if expected else "", expected, description, touched)


def price_row(config: dict, case_id: str, position: pd.Series, field: str, original,
              injected, control: str, description: str) -> dict:
    # Exceptions on previous_prices rows have no Portfolio (the file has none).
    return manifest_row(
        config, case_id, "previous_prices", "", position["AssetType"], position["Currency"],
        position["ISIN"], field, original, injected, control, severity(config, control), 1,
        f"{description} Security held in {position['Portfolio']}.",
        [("previous_prices", None, position["ISIN"])])


# -- Edits ---------------------------------------------------------------------
def set_position(data: dict, case_id: str, position: pd.Series, field: str, value):
    return set_position_field(data, case_id, position["Portfolio"], position["ISIN"],
                              position["AssetType"], position["Currency"], field, value)


def set_cashflow(data: dict, case_id: str, cashflow: pd.Series, field: str, value):
    # Located by its clean key; returns the original value.
    cashflows = data["cashflows"]
    label = find_cashflow(cashflows, case_id, cashflow["Portfolio"], cashflow["ISIN"],
                          cashflow["CashflowDate"], cashflow["CashflowType"])
    original = cashflows.loc[label, field]
    cashflows.loc[label, field] = value
    return original


def set_latest_price(data: dict, case_id: str, isin: str, field: str, value):
    prices = data["previous_prices"]
    label = find_price(prices, case_id, isin, LATEST_PRICE_DATE)
    original = prices.loc[label, field]
    prices.loc[label, field] = value
    return original


def price_case(data: dict, config: dict, case_id: str, position: pd.Series, previous: float,
               market: float, alert: bool, description: str, ratings: set | None = None) -> dict:
    field, original, injected, pct = inject_price_case(
        data, config, case_id, position["Portfolio"], position["ISIN"], position["AssetType"],
        position["Currency"], ratings, previous, market, expect_alert=alert)
    control = "PRICE_MOVE_ABOVE_THRESHOLD"
    return manifest_row(
        config, case_id, "previous_prices", position["Portfolio"], position["AssetType"],
        position["Currency"], position["ISIN"], field, original, injected, control,
        severity(config, control) if alert else "", int(alert),
        f"{description} ({pct}). No VD-14 price: the reset VD-7 price is the only history.",
        [("positions", position["Portfolio"], position["ISIN"]),
         ("previous_prices", None, position["ISIN"])])


# -- Single-anomaly cases --------------------------------------------------------
def price_move_cases(data: dict, config: dict, clean: dict, rng, used: set, ids) -> list:
    rows = []
    for asset_type in PRICE_MOVE_MIX:
        position = pick_position(clean, rng, used, [asset_type], no_older_price=True)
        low, high = MOVE_RANGES_PCT[asset_type]
        move = float(rng.uniform(low, high)) * float(rng.choice([-1, 1])) / 100
        previous = latest_price(clean, position["ISIN"])
        market = round(previous * (1 + move), 2)
        rows.append(price_case(data, config, next(ids), position, previous, market, True,
                               f"{asset_type} moved beyond its threshold"))
    return rows


def position_cases(data: dict, config: dict, clean: dict, rng, used: set, ids) -> list:
    rated = list(config["rating_required_for"])
    edits = ([("MISSING_RATING", "Rating", None, rated, "Rating emptied where one is expected.")]
             * SINGLE_CASES["MISSING_RATING"]
             + [("MISSING_MARKET_PRICE", "MarketPrice", float("nan"), None,
                 "MarketPrice emptied: no market value and no price movement.")]
             * SINGLE_CASES["MISSING_MARKET_PRICE"]
             + [("INVALID_NOMINAL", "Nominal", value, None,
                 "Nominal present and <= 0 (exception rule 3).") for value in INVALID_NOMINALS])
    rows = []
    for control, field, value, asset_types, description in edits:
        position = pick_position(clean, rng, used, asset_types)
        case_id = next(ids)
        original = set_position(data, case_id, position, field, value)
        rows.append(position_row(config, case_id, position, field, original, value, control,
                                 description))
    return rows


def missing_isin_and_duplicate_cases(data: dict, config: dict, clean: dict, rng, used: set,
                                     ids) -> list:
    rows = []
    for _ in range(SINGLE_CASES["MISSING_ISIN"]):
        position = pick_position(clean, rng, used)
        rows.append(missing_isin_row(data, config, next(ids), position))
    for _ in range(SINGLE_CASES["DUPLICATE_POSITION"]):
        position, case_id = pick_position(clean, rng, used), next(ids)
        positions = data["positions"]
        label = find_position(positions, case_id, position["Portfolio"], position["ISIN"],
                              position["AssetType"], position["Currency"])
        data["positions"] = insert_after(positions, label, positions.loc[label].to_dict())
        rows.append(position_row(config, case_id, position, "(entire row)", "1 row",
                                 "2 identical rows", "DUPLICATE_POSITION",
                                 "Exact copy of the position row: counted twice in totals "
                                 "(exception rules 4 and 5)."))
    return rows


def missing_isin_row(data: dict, config: dict, case_id: str, position: pd.Series) -> dict:
    original, prices, flows = empty_isin(data, case_id, position["Portfolio"], position["ISIN"],
                                         position["AssetType"], position["Currency"])
    row = position_row(config, case_id, position, "ISIN", original, None, "MISSING_ISIN",
                       f"ISIN emptied (clean {original}); its {prices} previous price(s) and "
                       f"{flows} cashflow(s) deleted so that they do not become unmatched "
                       f"(exception rule 1).", isin="")
    row["_touches"] = [("positions", position["Portfolio"], original),
                       ("previous_prices", None, original),
                       ("cashflows", position["Portfolio"], original)]
    return row


def price_history_cases(data: dict, config: dict, clean: dict, rng, used: set, ids) -> list:
    rows = []
    for _ in range(SINGLE_CASES["NO_PRICE_HISTORY"]):
        position, case_id = pick_position(clean, rng, used), next(ids)
        prices = data["previous_prices"]
        deleted = prices["ISIN"] == position["ISIN"]
        data["previous_prices"] = prices[~deleted].reset_index(drop=True)
        row = position_row(config, case_id, position, "(all price rows)",
                           f"{deleted.sum()} row(s)", "deleted", "NO_PRICE_HISTORY",
                           "Every previous price deleted: a new security without history.")
        row["File"], row["_touches"] = "previous_prices", [("previous_prices", None,
                                                            position["ISIN"])]
        rows.append(row)
    for differs in DUPLICATE_PRICE_DIFFERS:
        position, case_id = pick_position(clean, rng, used), next(ids)
        prices = data["previous_prices"]
        label = find_price(prices, case_id, position["ISIN"], LATEST_PRICE_DATE)
        copy = prices.loc[label].to_dict()
        if differs:
            copy["PreviousPrice"] = round(copy["PreviousPrice"] + 0.50, 2)
        data["previous_prices"] = insert_after(prices, label, copy)
        rows.append(price_row(
            config, case_id, position, "ISIN+PreviousDate", "1 row", "2 rows",
            "DUPLICATE_PREVIOUS_PRICE",
            "Latest price duplicated with another price: no movement computed (rule 7)."
            if differs else "Latest price duplicated, same price: movement still computed."))
    return rows


def cashflow_cases(data: dict, config: dict, clean: dict, rng, used: set, ids) -> list:
    rows = []
    for _ in range(SINGLE_CASES["PAST_FORECAST_CASHFLOW"]):
        cashflow, case_id = pick_cashflow(clean, rng, used, status="Forecast"), next(ids)
        date = free_date(data, cashflow, past_dates(rng))
        original = set_cashflow(data, case_id, cashflow, "CashflowDate", date)
        rows.append(cashflow_row(config, case_id, cashflow, "CashflowDate", original, date,
                                 "PAST_FORECAST_CASHFLOW",
                                 "Moved before the reference date, Status still Forecast."))
    for _ in range(SINGLE_CASES["DUPLICATE_CASHFLOW"]):
        cashflow, case_id = pick_cashflow(clean, rng, used), next(ids)
        cashflows = data["cashflows"]
        label = find_cashflow(cashflows, case_id, cashflow["Portfolio"], cashflow["ISIN"],
                              cashflow["CashflowDate"], cashflow["CashflowType"])
        data["cashflows"] = insert_after(cashflows, label, cashflows.loc[label].to_dict())
        rows.append(cashflow_row(config, case_id, cashflow, "(entire row)", "1 row",
                                 "2 identical rows", "DUPLICATE_CASHFLOW",
                                 "Exact copy of the cashflow row (exception rule 5)."))
    allowed = list(config["allowed_values"]["Currency"])
    for _ in range(SINGLE_CASES["CURRENCY_MISMATCH"]):
        cashflow, case_id = pick_cashflow(clean, rng, used), next(ids)
        currency = str(rng.choice([value for value in allowed if value != cashflow["Currency"]]))
        original = set_cashflow(data, case_id, cashflow, "Currency", currency)
        rows.append(cashflow_row(config, case_id, cashflow, "Currency", original, currency,
                                 "CURRENCY_MISMATCH", "Cashflow currency changed (allowed "
                                 "value) on a matched cashflow (exception rule 6)."))
    return rows


def unmatched_cases(data: dict, config: dict, clean: dict, rng, used: set, ids) -> list:
    half = SINGLE_CASES["UNMATCHED_CASHFLOW"] // 2
    rows = []
    for number in range(UNKNOWN_ISIN_NUMBER, UNKNOWN_ISIN_NUMBER + half):
        source, case_id = pick_cashflow(clean, rng, used, status="Forecast"), next(ids)
        isin = make_isin(number)
        cashflows = data["cashflows"]
        label = find_cashflow(cashflows, case_id, source["Portfolio"], source["ISIN"],
                              source["CashflowDate"], source["CashflowType"])
        data["cashflows"] = insert_after(cashflows, label,
                                         {**cashflows.loc[label].to_dict(), "ISIN": isin})
        row = cashflow_row(config, case_id, source, "(new row)", None, f"cashflow on {isin}",
                           "UNMATCHED_CASHFLOW", "Cashflow on a security that is not held.",
                           isin=isin, touches=[("cashflows", source["Portfolio"], isin)])
        row["AssetType"] = ""
        rows.append(row)
    portfolios = list(config["allowed_values"]["Portfolio"])
    for _ in range(SINGLE_CASES["UNMATCHED_CASHFLOW"] - half):
        cashflow, case_id = pick_cashflow(clean, rng, used), next(ids)
        wrong = str(rng.choice([value for value in portfolios if value != cashflow["Portfolio"]]))
        original = set_cashflow(data, case_id, cashflow, "Portfolio", wrong)
        rows.append(cashflow_row(
            config, case_id, cashflow, "Portfolio", original, wrong, "UNMATCHED_CASHFLOW",
            f"Booked to {wrong}, which does not hold the security.", portfolio=wrong,
            touches=[("cashflows", cashflow["Portfolio"], cashflow["ISIN"]),
                     ("cashflows", wrong, cashflow["ISIN"])]))
    return rows


def missing_value_cases(data: dict, config: dict, clean: dict, rng, used: set, ids) -> list:
    control, rows = "MISSING_VALUE", []
    for field, empty in [("BookPrice", float("nan")), ("MaturityDate", pd.NaT),
                         ("SecurityName", None)]:
        position, case_id = pick_position(clean, rng, used), next(ids)
        original = set_position(data, case_id, position, field, empty)
        rows.append(position_row(config, case_id, position, field, original, None, control,
                                 f"Required value {field} emptied."))
    position, case_id = pick_position(clean, rng, used), next(ids)
    original = set_latest_price(data, case_id, position["ISIN"], "PriceSource", None)
    rows.append(price_row(config, case_id, position, "PriceSource", original, None, control,
                          "PriceSource of the latest price emptied."))
    cashflow, case_id = pick_cashflow(clean, rng, used), next(ids)
    original = set_cashflow(data, case_id, cashflow, "Amount", float("nan"))
    rows.append(cashflow_row(config, case_id, cashflow, "Amount", original, None, control,
                             "Cashflow Amount emptied."))
    require(len(rows) == SINGLE_CASES[control], control, "case count")
    return rows


def invalid_value_cases(data: dict, config: dict, clean: dict, rng, used: set, ids) -> list:
    control, rows = "INVALID_VALUE", []
    position, case_id = pick_position(clean, rng, used), next(ids)
    data["positions"] = data["positions"].astype({"Nominal": object})  # holds a text cell
    text = f"{position['Nominal']:,.0f}"
    original = set_position(data, case_id, position, "Nominal", text)
    rows.append(position_row(config, case_id, position, "Nominal", original, text, control,
                             "Nominal typed as text: unreadable (exception rule 10)."))
    cashflow, case_id = pick_cashflow(clean, rng, used), next(ids)
    bad = cashflow["ISIN"].lower()
    set_cashflow(data, case_id, cashflow, "ISIN", bad)
    rows.append(cashflow_row(config, case_id, cashflow, "ISIN", cashflow["ISIN"], bad, control,
                             "ISIN not matching isin_pattern: no ISIN-based control "
                             "(exception rule 1).", isin=bad,
                             touches=[("cashflows", cashflow["Portfolio"], cashflow["ISIN"]),
                                      ("cashflows", cashflow["Portfolio"], bad)]))
    cashflow, case_id = pick_cashflow(clean, rng, used, cashflow_type="Fee"), next(ids)
    original = set_cashflow(data, case_id, cashflow, "Amount", -cashflow["Amount"])
    rows.append(cashflow_row(config, case_id, cashflow, "Amount", original, -original, control,
                             "Fee with a positive amount (amount_sign)."))
    position, case_id = pick_position(clean, rng, used), next(ids)
    require(UNKNOWN_CURRENCY not in config["allowed_values"]["Currency"], case_id, "currency")
    original = set_position(data, case_id, position, "Currency", UNKNOWN_CURRENCY)
    rows.append(position_row(config, case_id, position, "Currency", original, UNKNOWN_CURRENCY,
                             control, "Currency code outside the allowed list."))
    rows.append(matured_row(data, config, next(ids), pick_position(clean, rng, used)))
    require(len(rows) == SINGLE_CASES[control], control, "case count")
    return rows


def matured_row(data: dict, config: dict, case_id: str, position: pd.Series) -> dict:
    # A matured bond has no cashflow after its maturity: those rows are
    # deleted, otherwise each would also be an invalid date (rule 9).
    maturity = VALUATION_DATE - pd.Timedelta(days=30)
    original = set_position(data, case_id, position, "MaturityDate", maturity)
    cashflows = data["cashflows"]
    later = ((cashflows["Portfolio"] == position["Portfolio"])
             & (cashflows["ISIN"] == position["ISIN"]) & (cashflows["CashflowDate"] > maturity))
    data["cashflows"] = cashflows[~later].reset_index(drop=True)
    row = position_row(config, case_id, position, "MaturityDate", original, maturity,
                       "INVALID_VALUE", f"MaturityDate before the reference date; its "
                       f"{later.sum()} cashflow(s) after that date deleted.")
    row["_touches"].append(("cashflows", position["Portfolio"], position["ISIN"]))
    return row


def invalid_price_cases(data: dict, config: dict, clean: dict, rng, used: set, ids) -> list:
    control, rows = "INVALID_PRICE", []
    for field, value in [("MarketPrice", -1.50), ("BookPrice", 0.0)]:
        position, case_id = pick_position(clean, rng, used), next(ids)
        original = set_position(data, case_id, position, field, value)
        rows.append(position_row(config, case_id, position, field, original, value, control,
                                 f"{field} present and <= 0 (exception rules 3 and 8)."))
    position, case_id = pick_position(clean, rng, used), next(ids)
    original = set_latest_price(data, case_id, position["ISIN"], "PreviousPrice", 0.0)
    rows.append(price_row(config, case_id, position, "PreviousPrice", original, 0.0, control,
                          "Latest PreviousPrice 0: no movement, no fallback (rules 7, 8)."))
    require(len(rows) == SINGLE_CASES[control], control, "case count")
    return rows


# -- Two problems on one row, and near-boundary cases ----------------------------
def two_problem_cases(data: dict, config: dict, clean: dict, rng, used: set) -> list:
    rows = []
    for case_id, edits in [
        ("D01", [("Nominal", -500_000.0, "INVALID_NOMINAL"), ("Rating", None, "MISSING_RATING")]),
        ("D02", [("MarketPrice", float("nan"), "MISSING_MARKET_PRICE"),
                 ("BookPrice", 0.0, "INVALID_PRICE")]),
    ]:
        position = pick_position(clean, rng, used, list(config["rating_required_for"]))
        for field, value, control in edits:
            original = set_position(data, case_id, position, field, value)
            rows.append(position_row(config, case_id, position, field, original, value, control,
                                     "Two problems on one position row: two exceptions."))
    position = pick_position(clean, rng, used)
    original = set_position(data, "D03", position, "Nominal", 0.0)
    rows.append(missing_isin_row(data, config, "D03", position))
    rows.append({**position_row(config, "D03", position, "Nominal", original, 0.0,
                                "INVALID_NOMINAL", "Same row: ISIN empty and Nominal 0 "
                                "(exception rule 1, two problems)."),
                 "ISIN": "", "_touches": rows[-1]["_touches"]})
    rows += past_forecast_pair(data, config, "D04", pick_cashflow(clean, rng, used, "Forecast"),
                               rng, "Currency")
    rows += past_forecast_pair(data, config, "D05",
                               pick_cashflow(clean, rng, used, "Forecast", "Fee"), rng, "Amount")
    return rows


def past_forecast_pair(data: dict, config: dict, case_id: str, cashflow: pd.Series, rng,
                       field: str) -> list:
    # A Forecast cashflow moved into the past, plus a second problem on the same
    # row. The row is found by its key, which includes neither Currency nor Amount.
    if field == "Currency":
        allowed = config["allowed_values"]["Currency"]
        value = str(rng.choice([other for other in allowed if other != cashflow["Currency"]]))
        control = "CURRENCY_MISMATCH"
    else:
        value, control = -cashflow["Amount"], "INVALID_VALUE"
    original = set_cashflow(data, case_id, cashflow, field, value)
    date = free_date(data, cashflow, past_dates(rng))
    original_date = set_cashflow(data, case_id, cashflow, "CashflowDate", date)
    note = "Two problems on one cashflow row: two exceptions."
    return [cashflow_row(config, case_id, cashflow, "CashflowDate", original_date, date,
                         "PAST_FORECAST_CASHFLOW", note),
            cashflow_row(config, case_id, cashflow, field, original, value, control, note)]


def boundary_cases(data: dict, config: dict, clean: dict, rng, used: set) -> list:
    rows = []
    for case_id, asset_type, ratings, previous, market in BOUNDARY_MOVES:
        position = pick_position(clean, rng, used, [asset_type], ratings, no_older_price=True)
        rows.append(price_case(data, config, case_id, position, previous, market, False,
                               f"Near-boundary move on {asset_type}, no alert expected",
                               ratings))
    horizon_end = VALUATION_DATE + pd.Timedelta(days=config["cashflow_horizon_days"])
    for case_id, date, what in [("B09", VALUATION_DATE, "on the reference date: not past-dated"),
                                ("B10", horizon_end, "on the last day of the horizon")]:
        cashflow = pick_cashflow(clean, rng, used, "Forecast", after=horizon_end)
        date = free_date(data, cashflow, [date])
        original = set_cashflow(data, case_id, cashflow, "CashflowDate", date)
        rows.append(cashflow_row(config, case_id, cashflow, "CashflowDate", original, date,
                                 "PAST_FORECAST_CASHFLOW", f"Forecast cashflow {what}; in the "
                                 f"upcoming cashflows, no exception expected.", expected=0))
    return rows


# -- Checks, files and report ---------------------------------------------------------
def check_case_counts(manifest: pd.DataFrame) -> None:
    single = manifest[manifest["AnomalyID"].str.startswith("L")]
    counts = single.groupby("ExpectedControl")["ExpectedExceptionCount"].sum().to_dict()
    require(counts == SINGLE_CASES, "manifest", f"single cases {counts} != agreed {SINGLE_CASES}")
    double = manifest[manifest["AnomalyID"].str.startswith("D")]
    require(double["AnomalyID"].nunique() == 5 and len(double) == 10, "manifest", "D cases")
    boundary = manifest[manifest["AnomalyID"].str.startswith("B")]
    require(len(boundary) == 10 and (boundary["ExpectedExceptionCount"] == 0).all(),
            "manifest", "B cases")


def inject(clean: dict, config: dict, rng: np.random.Generator) -> tuple:
    data = {name: frame.copy() for name, frame in clean.items()}
    used, ids = set(), (f"L{number:03d}" for number in count(1))
    rows = []
    for cases in (price_move_cases, position_cases, missing_isin_and_duplicate_cases,
                  price_history_cases, cashflow_cases, unmatched_cases, missing_value_cases,
                  invalid_value_cases, invalid_price_cases):
        rows += cases(data, config, clean, rng, used, ids)
    rows += two_problem_cases(data, config, clean, rng, used)
    rows += boundary_cases(data, config, clean, rng, used)
    manifest = pd.DataFrame(rows)
    check_one_case_per_security(manifest)
    check_case_counts(manifest)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in FILE_NAMES:
        save_workbook(data[name], INPUT_DIR / f"{name}.xlsx", name)
    save_manifest(manifest[MANIFEST_COLUMNS], MANIFEST_PATH)
    inputs = {name: read_workbook(INPUT_DIR / f"{name}.xlsx", name, lenient_numbers=True)
              for name in FILE_NAMES}
    return manifest, inputs, verify_differences(clean, inputs, manifest)


def print_injection(clean: dict, inputs: dict, manifest: pd.DataFrame,
                    differences: pd.DataFrame) -> None:
    expected = manifest[manifest["ExpectedExceptionCount"] > 0]
    print("\n--- Large sample: injected anomalies ---")
    print("Rows (clean -> inputs): " + "  ".join(
        f"{name} {len(clean[name])} -> {len(inputs[name])}" for name in FILE_NAMES))
    by_control = expected.groupby("ExpectedControl", sort=False).agg(
        Severity=("ExpectedSeverity", "first"), Exceptions=("ExpectedExceptionCount", "sum"))
    print(by_control.sort_values("Exceptions", ascending=False, kind="stable").to_string())
    print("By severity: " + ", ".join(
        f"{level} {total}" for level, total in
        expected.groupby("ExpectedSeverity")["ExpectedExceptionCount"].sum().items()))
    ids = manifest["AnomalyID"]
    print(f"Expected exceptions: {expected['ExpectedExceptionCount'].sum()}  "
          f"(single {ids.str.startswith('L').sum()} cases, two-problem "
          f"{ids[ids.str.startswith('D')].nunique()} rows, no-alert "
          f"{(manifest['ExpectedExceptionCount'] == 0).sum()} cases)")
    print(f"Rows changed, added or deleted, all owned by a case: {differences['Rows'].sum()}")


def main() -> None:
    config = load_config(CONFIG_PATH)
    rng = np.random.default_rng(SEED)
    files = build_clean_files(rng, config, LAYOUT, FIRST_ISIN_NUMBER, SHARED_SECURITY_COUNT)
    saved, results = write_and_verify(files, CLEAN_DIR, config)

    print_summary(saved["positions"], saved["previous_prices"], saved["cashflows"], config,
                  SEED)
    print("\n--- Invariants (checked before saving and on the saved files) ---")
    print("\n".join(results))
    print(f"\nClean base written to {CLEAN_DIR.relative_to(REPO_ROOT)}/")
    print(f"Clean data fingerprint: {fingerprint(saved)}")

    manifest, inputs, differences = inject(saved, config, rng)
    print_injection(saved, inputs, manifest, differences)
    saved_manifest = pd.read_excel(MANIFEST_PATH, dtype=object)
    print(f"Files written to {INPUT_DIR.relative_to(REPO_ROOT)}/ and "
          f"{MANIFEST_PATH.relative_to(REPO_ROOT)}")
    print(f"Data fingerprint (inputs + manifest): "
          f"{fingerprint({**inputs, 'large_manifest': saved_manifest})}")


if __name__ == "__main__":
    main()
