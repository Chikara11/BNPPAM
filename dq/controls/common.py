from __future__ import annotations

import pandas as pd

from dq.analytics import first_occurrence, held_isins, valid_isin
from dq.config import COLUMN_TYPES, INPUT_FILES
from dq.context import Context
from dq.exceptions import combine_exceptions, make_exceptions
from dq.loaders import InputData, empty_rows, truly_missing

# Rule 2: these columns have their own "missing" control, not MISSING_VALUE.
DEDICATED_MISSING_CONTROLS = {"positions": ("ISIN", "MarketPrice", "Rating")}

KIND_NAMES = {"text": "a text", "number": "a number", "date": "a date"}

PRICE_FIELDS = {"positions": ("MarketPrice", "BookPrice"), "previous_prices": ("PreviousPrice",)}


def evaluated_rows(data: InputData, file: str, ctx: Context) -> pd.Series:
    # Rows the non-duplicate controls look at: not an empty row (rule 11),
    # first occurrence of a duplicate key (rule 12) and, for prices, only
    # securities held (rule 1, data_spec.md section 2).
    frame = getattr(data, file)
    scope = ~empty_rows(data, file) & first_occurrence(data, file, ctx)
    if file == "previous_prices":
        scope &= frame["ISIN"].isin(held_isins(data, ctx))
    return scope


def check_missing_value(data: InputData, ctx: Context) -> pd.DataFrame:
    found = []
    for file in INPUT_FILES:
        frame = getattr(data, file)
        scope = evaluated_rows(data, file, ctx)
        for column in ctx.config.required_columns[file]:
            if column in DEDICATED_MISSING_CONTROLS.get(file, ()):
                continue
            column_scope = scope
            if file == "previous_prices" and column == "ISIN":
                # A price row without ISIN is not held: this is its only exception.
                column_scope = ~empty_rows(data, file)
            missing = column_scope & truly_missing(data, file, column)
            found.append(make_exceptions(
                frame[missing], "MISSING_VALUE", ctx, file, column,
                comment=f"Required value {column} is empty.",
            ))
    return combine_exceptions(found)


def check_invalid_value(data: InputData, ctx: Context) -> pd.DataFrame:
    # Rules 9, 10, 11 and the ISIN format of rule 1. Empty cells are never
    # reported here: they belong to a MISSING_* control (rule 2).
    return combine_exceptions(
        empty_row_exceptions(data, ctx)
        + parse_issue_exceptions(data, ctx)
        + isin_format_exceptions(data, ctx)
        + allowed_value_exceptions(data, ctx)
        + date_exceptions(data, ctx)
        + amount_sign_exceptions(data, ctx)
    )


def check_invalid_price(data: InputData, ctx: Context) -> pd.DataFrame:
    # Rules 3 and 8: a present price <= 0, one exception per field.
    found = []
    for file, columns in PRICE_FIELDS.items():
        frame = getattr(data, file)
        scope = evaluated_rows(data, file, ctx)
        for column in columns:
            invalid = scope & frame[column].notna() & (frame[column] <= 0)
            rows = frame[invalid]
            found.append(make_exceptions(
                rows, "INVALID_PRICE", ctx, file, column, observed=rows[column],
                comment=f"{column} must be greater than 0.",
            ))
    return combine_exceptions(found)


def empty_row_exceptions(data: InputData, ctx: Context) -> list[pd.DataFrame]:
    return [make_exceptions(getattr(data, file)[empty_rows(data, file)], "INVALID_VALUE", ctx,
                            file, "row", comment="Empty row")
            for file in INPUT_FILES]


def parse_issue_exceptions(data: InputData, ctx: Context) -> list[pd.DataFrame]:
    found = []
    issues = data.parse_issues
    for file in INPUT_FILES:
        frame = getattr(data, file)
        scope = evaluated_rows(data, file, ctx)
        for column in COLUMN_TYPES[file]:
            raw = issues[(issues["File"] == file) & (issues["Column"] == column)]
            if raw.empty:
                continue
            column_scope = ~empty_rows(data, file) if column == "ISIN" else scope
            rows = frame[column_scope & frame["SourceRow"].isin(raw["SourceRow"])]
            kind = KIND_NAMES[COLUMN_TYPES[file][column]]
            found.append(make_exceptions(
                rows, "INVALID_VALUE", ctx, file, column,
                observed=rows["SourceRow"].map(raw.set_index("SourceRow")["RawValue"]),
                comment=f"Cannot be read: {column} must be {kind} cell.",
            ))
    return found


def isin_format_exceptions(data: InputData, ctx: Context) -> list[pd.DataFrame]:
    found = []
    for file in INPUT_FILES:
        frame = getattr(data, file)
        badly_formatted = frame["ISIN"].notna() & ~valid_isin(frame, ctx)
        rows = frame[badly_formatted]
        found.append(make_exceptions(
            rows, "INVALID_VALUE", ctx, file, "ISIN", observed=rows["ISIN"],
            comment=f"ISIN does not match the expected format {ctx.config.isin_pattern}: "
                    f"no ISIN-based control runs on this row.",
        ))
    return found


def allowed_value_exceptions(data: InputData, ctx: Context) -> list[pd.DataFrame]:
    found = []
    for file in INPUT_FILES:
        frame = getattr(data, file)
        scope = evaluated_rows(data, file, ctx)
        for column, allowed in ctx.config.allowed_values.items():
            if column not in frame.columns:
                continue
            outside = scope & frame[column].notna() & ~frame[column].isin(allowed)
            rows = frame[outside]
            found.append(make_exceptions(
                rows, "INVALID_VALUE", ctx, file, column, observed=rows[column],
                comment=f"Not an allowed value for {column}. Allowed: {', '.join(allowed)}.",
            ))
    return found


def date_exceptions(data: InputData, ctx: Context) -> list[pd.DataFrame]:
    reference = f"{ctx.reference_date:%Y-%m-%d}"
    positions, prices = data.positions, data.previous_prices
    late_maturity = (evaluated_rows(data, "positions", ctx)
                     & (positions["MaturityDate"] <= ctx.reference_date))
    future_price = (evaluated_rows(data, "previous_prices", ctx)
                    & (prices["PreviousDate"] >= ctx.reference_date))
    found = [
        make_exceptions(positions[late_maturity], "INVALID_VALUE", ctx, "positions",
                        "MaturityDate", observed=positions.loc[late_maturity, "MaturityDate"],
                        comment=f"MaturityDate is on or before the reference date ({reference})."),
        make_exceptions(prices[future_price], "INVALID_VALUE", ctx, "previous_prices",
                        "PreviousDate", observed=prices.loc[future_price, "PreviousDate"],
                        comment=f"PreviousDate is on or after the reference date ({reference})."),
        cashflow_after_maturity_exceptions(data, ctx),
    ]
    return found


def cashflow_after_maturity_exceptions(data: InputData, ctx: Context) -> pd.DataFrame:
    # Only on cashflows that match a position (rule 9): an unmatched cashflow
    # has no maturity to compare with.
    key = ctx.config.keys["cashflow_to_position"]
    positions = data.positions
    usable = (evaluated_rows(data, "positions", ctx) & valid_isin(positions, ctx)
              & positions[list(key)].notna().all(axis=1))
    maturities = (positions[usable].drop_duplicates(list(key))
                  .set_index(list(key))["MaturityDate"])
    cashflows = data.cashflows
    maturity = pd.Series(
        [maturities.get(tuple(values)) for values in cashflows[list(key)].itertuples(index=False)],
        index=cashflows.index, dtype="datetime64[us]")
    after = (evaluated_rows(data, "cashflows", ctx) & valid_isin(cashflows, ctx)
             & (cashflows["CashflowDate"] > maturity))
    rows = cashflows[after]
    comments = [f"CashflowDate is after the MaturityDate of its position ({date:%Y-%m-%d})."
                for date in maturity[after]]
    return make_exceptions(rows, "INVALID_VALUE", ctx, "cashflows", "CashflowDate",
                           observed=rows["CashflowDate"],
                           comment=pd.Series(comments, index=rows.index, dtype=object))


def amount_sign_exceptions(data: InputData, ctx: Context) -> list[pd.DataFrame]:
    # data_spec.md section 3: Coupon and Principal are inflows, Fee an outflow.
    # An empty Amount is MISSING_VALUE; an unknown CashflowType has no sign rule.
    cashflows = data.cashflows
    sign = cashflows["CashflowType"].map(dict(ctx.config.amount_sign))
    amount = cashflows["Amount"]
    wrong = (evaluated_rows(data, "cashflows", ctx) & amount.notna()
             & (((sign == "positive") & (amount <= 0)) | ((sign == "negative") & (amount >= 0))))
    rows = cashflows[wrong]
    comments = [f"A {cashflow_type} amount must be "
                f"{'greater' if expected == 'positive' else 'less'} than 0."
                for cashflow_type, expected in zip(rows["CashflowType"], sign[wrong])]
    return [make_exceptions(rows, "INVALID_VALUE", ctx, "cashflows", "Amount",
                            observed=rows["Amount"],
                            comment=pd.Series(comments, index=rows.index, dtype=object))]
