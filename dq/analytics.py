from __future__ import annotations

import re

import numpy as np
import pandas as pd

from dq.config import COLUMN_TYPES, INPUT_FILES, SEVERITIES
from dq.context import Context
from dq.loaders import InputData, empty_rows, parse_issues_of, truly_missing
from dq.schema import (COMPUTED, CONFLICTING_PRICES, DATA_QUALITY_COLUMNS,
                       DIMENSION_QUALITY_COLUMNS, DUPLICATE_ROW, INVALID_ISIN,
                       INVALID_MARKET_PRICE, INVALID_PREVIOUS_PRICE, MISSING_ISIN,
                       MISSING_MARKET_PRICE, MISSING_PREVIOUS_PRICE, NO_PORTFOLIO,
                       NO_PRICE_HISTORY, NO_USABLE_PRICE, PRICE_MOVEMENT_COLUMNS,
                       SECTION_BY_CONTROL, SECTION_BY_DIMENSION, SECTION_BY_PORTFOLIO,
                       SECTION_BY_SEVERITY,
                       SECTION_MARKET_VALUE, SECTION_OVERVIEW, SECTION_PRICE_MOVES,
                       SECTION_UPCOMING_CASHFLOWS, STATUS_PAID, SUMMARY_COLUMNS,
                       UPCOMING_CASHFLOW_COLUMNS, conform)

# Internal only, never in an output: a previous price was found but is not
# yet checked for > 0.
USABLE = "Usable"

# A cashflow with one of these values missing or invalid is never totalled.
TOTALLED_CASHFLOW_COLUMNS = ("Status", "Amount", "Currency", "CashflowType")

DUPLICATE_KEYS = {"positions": "position_unique", "previous_prices": "previous_price_unique",
                  "cashflows": "cashflow_unique"}


def price_movements(data: InputData, ctx: Context) -> pd.DataFrame:
    """One row per position at the reference date, with its price movement.

    Returns output_spec.md section 2: PriceChangePct and Breach when computable
    (exception rule 7), else the reason in MoveStatus. Duplicated positions are
    kept (rule 4), empty rows are not positions (rule 11).
    """
    moves = price_move_table(data, ctx)[~empty_rows(data, "positions")]
    breach_rank = np.where(moves["Breach"].fillna(False), 0, np.where(moves["Breach"].isna(), 2, 1))
    ordered = moves.assign(_breach=breach_rank, _size=-moves["PriceChangePct"].abs()).sort_values(
        ["_breach", "_size", "Portfolio", "ISIN", "SourceRow"], na_position="last", kind="stable")
    return conform(ordered.drop(columns=["_breach", "_size"]).reset_index(drop=True),
                   PRICE_MOVEMENT_COLUMNS)


def upcoming_cashflows(data: InputData, ctx: Context) -> pd.DataFrame:
    """Cashflows from the reference date to reference date + horizon, not Paid.

    Returns output_spec.md section 3, both bounds inclusive (CLAUDE.md
    "Reference date"). Every row counts, duplicates included; SecurityName and
    AssetType come from the matching position, empty when unmatched.
    """
    cashflows = data.cashflows
    in_window = in_horizon(data, ctx)
    rows = cashflows[in_window]
    upcoming = pd.DataFrame({
        "CashflowDate": rows["CashflowDate"],
        "DaysFromReference": (rows["CashflowDate"] - ctx.reference_date).dt.days,
        "Portfolio": rows["Portfolio"],
        "ISIN": rows["ISIN"],
        "SecurityName": position_values(data, rows, "SecurityName", ctx),
        "AssetType": position_values(data, rows, "AssetType", ctx),
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
    """Completeness per input file and column.

    Returns output_spec.md section 4. A Rating outside rating_required_for is
    NotApplicable, not Missing; an unreadable cell is not complete either.
    """
    rows = []
    for file in INPUT_FILES:
        frame = getattr(data, file)
        for column in COLUMN_TYPES[file]:  # column order of data_spec.md
            if column not in frame.columns:
                continue
            not_applicable = not_applicable_cells(frame, file, column, ctx)
            applicable = len(frame) - int(not_applicable.sum())
            missing = int((truly_missing(data, file, column) & ~not_applicable).sum())
            # only the rows analysed (positions: at the reference date)
            parse_issues = int(parse_issues_of(data, file, column)["SourceRow"]
                               .isin(frame["SourceRow"]).sum())
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
    """Headline figures of the run, in long format.

    Returns output_spec.md section 5. Amounts are totalled per currency, never
    across currencies (CLAUDE.md "Financial assumptions"); duplicated rows are
    shown in the Overview, never removed.
    """
    moves = price_movements(data, ctx)
    upcoming = upcoming_cashflows(data, ctx)
    rows = []

    def add(section: str, item: str, value: float, unit: str = "count",
            currency: str | None = None) -> None:
        rows.append({"Section": section, "Item": item, "Currency": currency,
                     "Value": float(value), "Unit": unit})

    add(SECTION_OVERVIEW, "Positions", len(moves))
    add(SECTION_OVERVIEW, "Portfolios", moves["Portfolio"].dropna().nunique())
    add(SECTION_OVERVIEW, "Distinct ISINs", moves["ISIN"].dropna().nunique())
    # Duplicates stay in the totals (never repaired), so they are shown here.
    add(SECTION_OVERVIEW, "Duplicated position rows (counted twice in totals)",
        (~first_occurrence(data, "positions", ctx)).sum())
    add(SECTION_OVERVIEW, "Cashflows in the horizon", len(upcoming))
    add(SECTION_OVERVIEW, "Duplicated cashflow rows in the horizon",
        (~first_occurrence(data, "cashflows", ctx) & in_horizon(data, ctx)).sum())
    totalled = upcoming["SourceRow"].isin(
        data.cashflows.loc[totalled_cashflows(data, ctx), "SourceRow"])
    add(SECTION_OVERVIEW, "Cashflows excluded from totals", (~totalled).sum())

    for severity in SEVERITIES:
        add(SECTION_BY_SEVERITY, severity, (exceptions["Severity"] == severity).sum())
    for control_id, settings in ctx.config.controls.items():
        if settings.enabled:
            add(SECTION_BY_CONTROL, control_id, (exceptions["ControlID"] == control_id).sum())
    for dimension in ctx.config.dimensions:
        add(SECTION_BY_DIMENSION, dimension, (exceptions["Dimension"] == dimension).sum())
    for portfolio in portfolios_to_report(exceptions, ctx):
        found = (exceptions["Portfolio"].isna() if portfolio == NO_PORTFOLIO
                 else exceptions["Portfolio"] == portfolio)
        add(SECTION_BY_PORTFOLIO, portfolio, found.sum())

    # Amounts are totalled per currency only: never summed across currencies.
    for currency, total in totals_by_currency(moves, "MarketValue", ctx):
        add(SECTION_MARKET_VALUE, "Market value", total, "amount", currency)
    for currency, total in totals_by_currency(upcoming[totalled], "Amount", ctx):
        add(SECTION_UPCOMING_CASHFLOWS, "Upcoming cashflows", total, "amount", currency)

    computed = int((moves["MoveStatus"] == COMPUTED).sum())
    add(SECTION_PRICE_MOVES, "Computed", computed)
    add(SECTION_PRICE_MOVES, "Breaches", moves["Breach"].fillna(False).sum())
    add(SECTION_PRICE_MOVES, "Not computable", len(moves) - computed)
    return conform(pd.DataFrame(rows, columns=list(SUMMARY_COLUMNS)), SUMMARY_COLUMNS)


def dimension_quality(exceptions: pd.DataFrame, ctx: Context) -> pd.DataFrame:
    """Exceptions per data quality dimension, with their severity split.

    Returns output_spec.md section 4b: one row per dimension of config.yaml, in
    that order, zeros included. Controls lists the enabled controls of the
    dimension (comma-separated), empty when none is enabled.
    """
    rows = []
    for dimension in ctx.config.dimensions:
        controls = [control_id for control_id, settings in ctx.config.controls.items()
                    if settings.enabled and settings.dimension == dimension]
        found = exceptions[exceptions["Dimension"] == dimension]
        severities = found["Severity"].astype(str)
        rows.append({"Dimension": dimension, "Controls": ", ".join(controls) or None,
                     "Exceptions": len(found),
                     **{severity: int((severities == severity).sum()) for severity in SEVERITIES}})
    return conform(pd.DataFrame(rows, columns=list(DIMENSION_QUALITY_COLUMNS)),
                   DIMENSION_QUALITY_COLUMNS)


def price_change_pct(market_price: pd.Series, previous_price: pd.Series) -> pd.Series:
    # CLAUDE.md rule 7: in this order, rounded to 6 decimals before any comparison.
    return ((market_price - previous_price) / previous_price * 100).round(6)


def market_value(nominal: pd.Series, market_price: pd.Series,
                 allowed_currency: pd.Series) -> pd.Series:
    # Nominal * MarketPrice / 100, only when both are present and > 0 and the
    # currency is allowed: an invalid value never enters a total.
    valid = (nominal > 0) & (market_price > 0) & allowed_currency
    return (nominal * market_price / 100).where(valid)


def price_move_table(data: InputData, ctx: Context) -> pd.DataFrame:
    # Every position row, aligned on data.positions (not sorted, empty rows kept).
    # The only price movement implementation: PRICE_MOVE_ABOVE_THRESHOLD uses it too.
    positions = data.positions
    previous = previous_price_by_position(data, ctx)
    market_price, previous_price = positions["MarketPrice"], previous["PreviousPrice"]
    # The first reason that applies wins, in this order (rule 7).
    price_status = previous["PreviousPriceStatus"]
    reasons = [
        (~first_occurrence(data, "positions", ctx), DUPLICATE_ROW),  # rule 12
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
        "MarketValue": market_value(positions["Nominal"], market_price,
                                    positions["Currency"].isin(allowed_currencies(ctx))),
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
    # ASCII: in Python, \d also matches other scripts' digits (e.g. full-width).
    pattern = re.compile(ctx.config.isin_pattern, re.ASCII)
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
            & (cashflows["Status"] != STATUS_PAID))


def outside_allowed(frame: pd.DataFrame, column: str, ctx: Context) -> pd.Series:
    # Present but not in allowed_values; an empty cell is a MISSING_* case (rule 2).
    allowed = ctx.config.allowed_values.get(column)
    if allowed is None:
        return pd.Series(False, index=frame.index)
    return frame[column].notna() & ~frame[column].isin(allowed)


def wrong_amount_sign(cashflows: pd.DataFrame, ctx: Context) -> pd.Series:
    # data_spec.md section 3: Coupon and Principal are inflows, Fee an outflow.
    # An empty Amount is MISSING_VALUE; an unknown CashflowType has no sign rule.
    sign = cashflows["CashflowType"].map(dict(ctx.config.amount_sign))
    amount = cashflows["Amount"]
    return amount.notna() & (((sign == "positive") & (amount <= 0))
                             | ((sign == "negative") & (amount >= 0)))


def totalled_cashflows(data: InputData, ctx: Context) -> pd.Series:
    # A cashflow enters the currency totals only when the values that make an
    # amount (Status, Amount, Currency, CashflowType) are present and valid:
    # the same rule as an invalid Nominal or price. The row stays listed.
    cashflows = data.cashflows
    unusable = wrong_amount_sign(cashflows, ctx)
    for column in TOTALLED_CASHFLOW_COLUMNS:
        unusable |= cashflows[column].isna() | outside_allowed(cashflows, column, ctx)
    return ~unusable


def matching_positions(data: InputData, ctx: Context) -> pd.DataFrame:
    # One position per Portfolio + ISIN that a cashflow can match: valid ISIN
    # and complete key (rule 1), first occurrence of a duplicated position
    # (rule 12; the duplicate control reports the other rows). Matching is a
    # membership test, never a row join (rule 4).
    key = list(ctx.config.keys["cashflow_to_position"])
    positions = data.positions
    usable = (first_occurrence(data, "positions", ctx) & valid_isin(positions, ctx)
              & positions[key].notna().all(axis=1))
    return positions[usable].drop_duplicates(key)


def matches_a_position(data: InputData, ctx: Context) -> pd.Series:
    key = list(ctx.config.keys["cashflow_to_position"])
    held = pd.MultiIndex.from_frame(matching_positions(data, ctx)[key])
    cashflow_keys = pd.MultiIndex.from_frame(data.cashflows[key])
    return pd.Series(cashflow_keys.isin(held), index=data.cashflows.index)


def position_values(data: InputData, rows: pd.DataFrame, column: str,
                    ctx: Context) -> pd.Series:
    # The value of `column` on the position each cashflow row matches,
    # aligned on rows; empty (NaN / NaT) when the row matches no position.
    key = list(ctx.config.keys["cashflow_to_position"])
    lookup = matching_positions(data, ctx).set_index(key)[column]
    # reindex rather than Series.map: map fails on an empty datetime lookup
    found = lookup.reindex(pd.MultiIndex.from_frame(rows[key]))
    return pd.Series(found.to_numpy(), index=rows.index, dtype=lookup.dtype)


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


def allowed_currencies(ctx: Context) -> tuple[str, ...]:
    return ctx.config.allowed_values.get("Currency", ())


def totals_by_currency(frame: pd.DataFrame, column: str,
                       ctx: Context) -> list[tuple[str, float]]:
    # Allowed currencies only, in config.yaml order: an amount in an unknown
    # or empty currency (reported by INVALID_VALUE / MISSING_VALUE) never
    # enters a total; its row stays in the table.
    totals = frame.groupby("Currency")[column].sum()
    return [(currency, totals[currency]) for currency in allowed_currencies(ctx)
            if currency in totals.index]
