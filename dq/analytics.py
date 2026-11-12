from __future__ import annotations

import re

import numpy as np
import pandas as pd

from dq.config import COLUMN_TYPES, INPUT_FILES, SEVERITIES
from dq.context import Context
from dq.loaders import InputData, empty_rows, truly_missing
from dq.schema import (DATA_QUALITY_COLUMNS, PRICE_MOVEMENT_COLUMNS, SUMMARY_COLUMNS,
                       UPCOMING_CASHFLOW_COLUMNS, conform)

# MoveStatus vocabulary of output_spec.md section 2
COMPUTED = "Computed"
MISSING_ISIN = "Missing ISIN"
INVALID_ISIN = "Invalid ISIN"
NO_PRICE_HISTORY = "No price history"
MISSING_MARKET_PRICE = "Missing market price"
INVALID_MARKET_PRICE = "Invalid market price"
MISSING_PREVIOUS_PRICE = "Missing previous price"
INVALID_PREVIOUS_PRICE = "Invalid previous price"
CONFLICTING_PRICES = "Conflicting previous prices"
NO_USABLE_PRICE = "No usable previous price"
USABLE = "Usable"  # previous price found, not yet checked for > 0

NO_PORTFOLIO = "(none)"

DUPLICATE_KEYS = {"positions": "position_unique", "previous_prices": "previous_price_unique",
                  "cashflows": "cashflow_unique"}


def price_movements(data: InputData, ctx: Context) -> pd.DataFrame:
    # One row per position at the reference date (duplicates included, rule 4);
    # an empty row is not a position (rule 11).
    moves = price_move_table(data, ctx)[~empty_rows(data, "positions")]
    breach_rank = np.where(moves["Breach"].fillna(False), 0, np.where(moves["Breach"].isna(), 2, 1))
    ordered = moves.assign(_breach=breach_rank, _size=-moves["PriceChangePct"].abs()).sort_values(
        ["_breach", "_size", "Portfolio", "ISIN", "SourceRow"], na_position="last", kind="stable")
    return conform(ordered.drop(columns=["_breach", "_size"]).reset_index(drop=True),
                   PRICE_MOVEMENT_COLUMNS)


def upcoming_cashflows(data: InputData, ctx: Context) -> pd.DataFrame:
    # reference date <= CashflowDate <= reference date + horizon, both
    # inclusive, Status other than Paid. Every row counts, duplicates included.
    cashflows = data.cashflows
    in_window = in_horizon(data, ctx)
    rows = cashflows[in_window]
    key = ctx.config.keys["cashflow_to_position"]
    positions = matching_positions(data, ctx).drop_duplicates(list(key)).set_index(list(key))
    keys = key_tuples(rows, key)
    upcoming = pd.DataFrame({
        "CashflowDate": rows["CashflowDate"],
        "DaysFromReference": (rows["CashflowDate"] - ctx.reference_date).dt.days,
        "Portfolio": rows["Portfolio"],
        "ISIN": rows["ISIN"],
        "SecurityName": [positions["SecurityName"].get(values) for values in keys],
        "AssetType": [positions["AssetType"].get(values) for values in keys],
        "CashflowType": rows["CashflowType"],
        "Amount": rows["Amount"],
        "Currency": rows["Currency"],
        "Status": rows["Status"],
        "MatchedPosition": matches_a_position(data, ctx)[in_window],
        "SourceRow": rows["SourceRow"],
    }, index=rows.index)
    ordered = upcoming.sort_values(["CashflowDate", "Portfolio", "ISIN", "SourceRow"],
                                   na_position="last", kind="stable")
    return conform(ordered.reset_index(drop=True), UPCOMING_CASHFLOW_COLUMNS)


def data_quality(data: InputData, ctx: Context) -> pd.DataFrame:
    rows = []
    issues = data.parse_issues
    for file in INPUT_FILES:
        frame = getattr(data, file)
        for column in COLUMN_TYPES[file]:  # column order of data_spec.md
            if column not in frame.columns:
                continue
            not_applicable = not_applicable_cells(frame, file, column, ctx)
            applicable = len(frame) - int(not_applicable.sum())
            missing = int((truly_missing(data, file, column) & ~not_applicable).sum())
            parse_issues = int(((issues["File"] == file) & (issues["Column"] == column)
                                & issues["SourceRow"].isin(frame["SourceRow"])).sum())
            # a cell that cannot be read is not complete either
            completeness = (round((applicable - missing - parse_issues) / applicable * 100, 2)
                            if applicable else np.nan)
            rows.append({
                "Source": file, "Column": column,
                "Required": column in ctx.config.required_columns[file],
                "Rows": len(frame), "NotApplicable": len(frame) - applicable,
                "Missing": missing, "ParseIssues": parse_issues,
                "CompletenessPct": completeness,
            })
    return conform(pd.DataFrame(rows, columns=list(DATA_QUALITY_COLUMNS)), DATA_QUALITY_COLUMNS)


def summary(exceptions: pd.DataFrame, data: InputData, ctx: Context) -> pd.DataFrame:
    moves = price_movements(data, ctx)
    upcoming = upcoming_cashflows(data, ctx)
    rows = []

    def add(section: str, item: str, value: float, unit: str = "count",
            currency: str | None = None) -> None:
        rows.append({"Section": section, "Item": item, "Currency": currency,
                     "Value": float(value), "Unit": unit})

    add("Overview", "Positions", len(moves))
    add("Overview", "Portfolios", moves["Portfolio"].dropna().nunique())
    add("Overview", "Distinct ISINs", moves["ISIN"].dropna().nunique())
    # Duplicates stay in the totals (never repaired), so they are shown here.
    add("Overview", "Duplicated position rows (counted twice in totals)",
        (~first_occurrence(data, "positions", ctx)).sum())
    add("Overview", "Position rows not at the reference date (ignored)",
        ctx.ignored_position_rows)
    add("Overview", "Cashflows in the horizon", len(upcoming))
    add("Overview", "Duplicated cashflow rows in the horizon",
        (~first_occurrence(data, "cashflows", ctx) & in_horizon(data, ctx)).sum())

    for severity in SEVERITIES:
        add("Exceptions by severity", severity, (exceptions["Severity"] == severity).sum())
    for control_id, settings in ctx.config.controls.items():
        if settings.enabled:
            add("Exceptions by control", control_id, (exceptions["ControlID"] == control_id).sum())
    for portfolio in portfolios_to_report(exceptions, ctx):
        found = (exceptions["Portfolio"].isna() if portfolio == NO_PORTFOLIO
                 else exceptions["Portfolio"] == portfolio)
        add("Exceptions by portfolio", portfolio, found.sum())

    # Amounts are totalled per currency only: never summed across currencies.
    for currency, total in totals_by_currency(moves, "MarketValue", ctx):
        add("Market value by currency", "Market value", total, "amount", currency)
    for currency, total in totals_by_currency(upcoming, "Amount", ctx):
        add("Upcoming cashflows by currency", "Upcoming cashflows", total, "amount", currency)

    computed = int((moves["MoveStatus"] == COMPUTED).sum())
    add("Price moves", "Computed", computed)
    add("Price moves", "Breaches", moves["Breach"].fillna(False).sum())
    add("Price moves", "Not computable", len(moves) - computed)
    return conform(pd.DataFrame(rows, columns=list(SUMMARY_COLUMNS)), SUMMARY_COLUMNS)


def price_change_pct(market_price: pd.Series, previous_price: pd.Series) -> pd.Series:
    # CLAUDE.md rule 7: in this order, rounded to 6 decimals before any comparison.
    return ((market_price - previous_price) / previous_price * 100).round(6)


def market_value(nominal: pd.Series, market_price: pd.Series) -> pd.Series:
    # Nominal * MarketPrice / 100, only when both are present and > 0.
    return (nominal * market_price / 100).where((nominal > 0) & (market_price > 0))


def price_move_table(data: InputData, ctx: Context) -> pd.DataFrame:
    # Every position row, aligned on data.positions (not sorted, empty rows kept).
    # The only price movement implementation: PRICE_MOVE_ABOVE_THRESHOLD uses it too.
    positions = data.positions
    previous = previous_price_by_position(data, ctx)
    market_price, previous_price = positions["MarketPrice"], previous["PreviousPrice"]
    # The first reason that applies wins, in this order (rule 7).
    price_status = previous["PreviousPriceStatus"]
    reasons = [
        (positions["ISIN"].isna(), MISSING_ISIN),
        (~valid_isin(positions, ctx), INVALID_ISIN),
        (market_price.isna(), MISSING_MARKET_PRICE),
        (market_price <= 0, INVALID_MARKET_PRICE),
        (price_status == NO_PRICE_HISTORY, NO_PRICE_HISTORY),
        (price_status == NO_USABLE_PRICE, NO_USABLE_PRICE),
        (price_status == CONFLICTING_PRICES, CONFLICTING_PRICES),
        (previous_price.isna(), MISSING_PREVIOUS_PRICE),
        (previous_price <= 0, INVALID_PREVIOUS_PRICE),
    ]
    status = pd.Series(COMPUTED, index=positions.index, dtype=object)
    for applies, reason in reversed(reasons):
        status[applies] = reason
    computed = status == COMPUTED
    change = price_change_pct(market_price, previous_price).where(computed)
    threshold = positions["AssetType"].map(dict(ctx.config.price_move_thresholds_pct)).astype(
        "float64")
    breach = pd.Series(pd.NA, index=positions.index, dtype="boolean")
    judged = computed & threshold.notna()  # no threshold for an unknown AssetType
    breach[judged] = change[judged].abs() > threshold[judged]
    return pd.DataFrame({
        "Portfolio": positions["Portfolio"], "ISIN": positions["ISIN"],
        "SecurityName": positions["SecurityName"], "AssetType": positions["AssetType"],
        "Currency": positions["Currency"], "Rating": positions["Rating"],
        "Nominal": positions["Nominal"],
        "PreviousDate": previous["PreviousDate"], "PreviousPrice": previous_price,
        "MarketPrice": market_price, "PriceChangePct": change, "ThresholdPct": threshold,
        "Breach": breach, "MoveStatus": status,
        "MarketValue": market_value(positions["Nominal"], market_price),
        "SourceRow": positions["SourceRow"],
    }, index=positions.index)


def latest_previous_prices(data: InputData, ctx: Context) -> pd.DataFrame:
    # One row per ISIN found in previous_prices: the price on the latest
    # PreviousDate strictly before the reference date, or why there is none.
    prices = data.previous_prices[data.previous_prices["ISIN"].notna()]
    usable = prices[prices["PreviousDate"].notna()
                    & (prices["PreviousDate"] < ctx.reference_date)]
    latest_date = usable.groupby("ISIN")["PreviousDate"].transform("max")
    on_latest = usable[usable["PreviousDate"] == latest_date]
    latest = on_latest.groupby("ISIN").agg(
        PreviousDate=("PreviousDate", "first"),
        PreviousPrice=("PreviousPrice", "first"),
        DistinctPrices=("PreviousPrice", lambda values: values.nunique(dropna=False)),
    )
    result = pd.DataFrame(index=pd.Index(prices["ISIN"].unique(), name="ISIN"))
    # explicit dtypes: an empty previous_prices file gives untyped columns
    result = result.join(latest).astype({"PreviousDate": "datetime64[us]",
                                         "PreviousPrice": "float64"})
    result["PreviousPriceStatus"] = pd.Series(USABLE, index=result.index, dtype=object)
    result.loc[result["PreviousDate"].isna(), "PreviousPriceStatus"] = NO_USABLE_PRICE
    conflicting = result["DistinctPrices"] > 1
    result.loc[conflicting, "PreviousPriceStatus"] = CONFLICTING_PRICES
    result.loc[conflicting, "PreviousPrice"] = float("nan")  # no price is chosen
    return result.drop(columns="DistinctPrices")


def previous_price_by_position(data: InputData, ctx: Context) -> pd.DataFrame:
    # Aligned on the positions: PreviousDate, PreviousPrice, PreviousPriceStatus.
    positions = data.positions
    latest = latest_previous_prices(data, ctx)
    # reindex rather than Series.map: map fails on an empty datetime lookup
    result = latest.reindex(positions["ISIN"].to_numpy()).astype(
        {"PreviousDate": "datetime64[us]", "PreviousPrice": "float64",
         "PreviousPriceStatus": object})
    result.index = positions.index
    result.loc[result["PreviousPriceStatus"].isna(), "PreviousPriceStatus"] = NO_PRICE_HISTORY
    result.loc[positions["ISIN"].isna(), "PreviousPriceStatus"] = MISSING_ISIN
    return result


def valid_isin(frame: pd.DataFrame, ctx: Context) -> pd.Series:
    pattern = re.compile(ctx.config.isin_pattern)
    return frame["ISIN"].map(
        lambda value: isinstance(value, str) and pattern.fullmatch(value) is not None
    ).astype(bool)


def held_isins(data: InputData, ctx: Context) -> pd.Series:
    positions = data.positions
    return positions.loc[valid_isin(positions, ctx), "ISIN"]


def duplicate_candidates(data: InputData, file: str, ctx: Context) -> pd.Series:
    # Rows the duplicate control compares: complete key and a usable ISIN
    # (rule 1). Rule 12 hides the repeats of these rows from other controls.
    frame = getattr(data, file)
    key = ctx.config.keys[DUPLICATE_KEYS[file]]
    complete = frame[list(key)].notna().all(axis=1)
    if file == "previous_prices":
        return complete & frame["ISIN"].isin(held_isins(data, ctx))
    return complete & valid_isin(frame, ctx)


def first_occurrence(data: InputData, file: str, ctx: Context) -> pd.Series:
    # False on the extra rows of a duplicated key (rule 12).
    frame = getattr(data, file)
    key = ctx.config.keys[DUPLICATE_KEYS[file]]
    candidates = duplicate_candidates(data, file, ctx)
    repeated = frame[candidates].duplicated(list(key), keep="first")
    return ~repeated.reindex(frame.index, fill_value=False)


def in_horizon(data: InputData, ctx: Context) -> pd.Series:
    # reference date <= CashflowDate <= reference date + horizon, Status not Paid.
    cashflows = data.cashflows
    dates = cashflows["CashflowDate"]
    return (~empty_rows(data, "cashflows") & dates.notna()
            & (dates >= ctx.reference_date) & (dates <= ctx.horizon_end)
            & (cashflows["Status"] != "Paid"))


def matching_positions(data: InputData, ctx: Context) -> pd.DataFrame:
    # Every position at the reference date with a complete key and a valid
    # ISIN, duplicates included: matching is a membership test (rule 4).
    key = ctx.config.keys["cashflow_to_position"]
    positions = data.positions
    return positions[valid_isin(positions, ctx) & positions[list(key)].notna().all(axis=1)]


def matches_a_position(data: InputData, ctx: Context) -> pd.Series:
    key = ctx.config.keys["cashflow_to_position"]
    held = pd.MultiIndex.from_frame(matching_positions(data, ctx)[list(key)])
    cashflow_keys = pd.MultiIndex.from_frame(data.cashflows[list(key)])
    return pd.Series(cashflow_keys.isin(held), index=data.cashflows.index)


def key_tuples(frame: pd.DataFrame, key: tuple[str, ...]) -> list[tuple]:
    return [tuple(values) for values in frame[list(key)].itertuples(index=False)]


def not_applicable_cells(frame: pd.DataFrame, file: str, column: str,
                         ctx: Context) -> pd.Series:
    # A rating is not expected outside rating_required_for (CLO Equity).
    if file == "positions" and column == "Rating":
        asset_type = frame["AssetType"]
        return asset_type.notna() & ~asset_type.isin(ctx.config.rating_required_for)
    return pd.Series(False, index=frame.index)


def portfolios_to_report(exceptions: pd.DataFrame, ctx: Context) -> list[str]:
    # Every portfolio of config.yaml (zeros included), then any other
    # portfolio found in the exceptions, then (none).
    known = list(ctx.config.allowed_values.get("Portfolio", ()))
    others = sorted(set(exceptions["Portfolio"].dropna()) - set(known))
    return known + others + [NO_PORTFOLIO]


def totals_by_currency(frame: pd.DataFrame, column: str,
                       ctx: Context) -> list[tuple[str, float]]:
    # Currencies in config.yaml order, then any other value found.
    totals = frame.dropna(subset=["Currency"]).groupby("Currency")[column].sum()
    known = [currency for currency in ctx.config.allowed_values.get("Currency", ())
             if currency in totals.index]
    others = sorted(set(totals.index) - set(known))
    return [(currency, totals[currency]) for currency in known + others]
