from __future__ import annotations

import pandas as pd

from dq.analytics import (duplicate_candidates, matches_a_position, position_values,
                          valid_isin)
from dq.context import Context
from dq.controls.common import evaluated_rows
from dq.exceptions import make_duplicate_exceptions, make_exceptions
from dq.loaders import InputData
from dq.schema import STATUS_FORECAST


def check_unmatched_cashflow(data: InputData, ctx: Context) -> pd.DataFrame:
    """UNMATCHED_CASHFLOW: no position at the reference date with this Portfolio + ISIN.

    Returns one exception per cashflow. Matching is a membership test, never a
    row join (exception rule 4). An empty, badly formatted or non-allowed key
    value is reported by MISSING_VALUE or INVALID_VALUE, not here.
    """
    key = ctx.config.keys["cashflow_to_position"]
    cashflows = data.cashflows
    unmatched = (matchable(data, ctx) & allowed_portfolio(cashflows, ctx)
                 & ~matches_a_position(data, ctx))
    rows = with_security_name(data, cashflows[unmatched], ctx)
    return make_exceptions(
        rows, "UNMATCHED_CASHFLOW", ctx, "cashflows", "+".join(key),
        observed=key_text(rows, key),
        comment=f"No position at the reference date with this {' + '.join(key)}.",
    )


def check_duplicate_cashflow(data: InputData, ctx: Context) -> pd.DataFrame:
    """DUPLICATE_CASHFLOW: same cashflow_unique key on several rows.

    Returns one exception per duplicated key (exception rule 5). Rows with an
    incomplete key or a bad ISIN are not compared (rules 1 and 12).
    """
    candidates = duplicate_candidates(data, "cashflows", ctx)
    rows = with_security_name(data, data.cashflows[candidates], ctx)
    return make_duplicate_exceptions(rows, ctx.config.keys["cashflow_unique"],
                                     "DUPLICATE_CASHFLOW", ctx, "cashflows")


def check_past_forecast_cashflow(data: InputData, ctx: Context) -> pd.DataFrame:
    """PAST_FORECAST_CASHFLOW: Status Forecast and CashflowDate before the reference date.

    Returns one exception per cashflow. Past-dated = strictly before the
    reference date (CLAUDE.md "Reference date"); a Status outside the allowed
    values (e.g. " Forecast") is reported by INVALID_VALUE only.
    """
    cashflows = data.cashflows
    past = (evaluated_rows(data, "cashflows", ctx) & (cashflows["Status"] == STATUS_FORECAST)
            & (cashflows["CashflowDate"] < ctx.reference_date))
    rows = with_security_name(data, cashflows[past], ctx)
    return make_exceptions(
        rows, "PAST_FORECAST_CASHFLOW", ctx, "cashflows", "CashflowDate",
        observed=rows["CashflowDate"],
        comment=f"Dated before the reference date ({ctx.reference_date:%Y-%m-%d}) but still "
                f"Forecast: it should be Paid or re-dated.",
    )


def check_currency_mismatch(data: InputData, ctx: Context) -> pd.DataFrame:
    """CURRENCY_MISMATCH: cashflow currency differs from its position currency.

    Returns one exception per cashflow. Only matched cashflows are compared
    (exception rule 6), with the first occurrence of a duplicated position
    (rule 12); a currency outside the allowed list is INVALID_VALUE only.
    """
    allowed = ctx.config.allowed_values["Currency"]
    cashflows = data.cashflows
    currency = cashflows["Currency"]
    position_currency = position_values(data, cashflows, "Currency", ctx)
    mismatch = (evaluated_rows(data, "cashflows", ctx) & currency.isin(allowed)
                & position_currency.isin(allowed) & (currency != position_currency))
    rows = with_security_name(data, cashflows[mismatch], ctx)
    return make_exceptions(
        rows, "CURRENCY_MISMATCH", ctx, "cashflows", "Currency", observed=rows["Currency"],
        comment="Cashflow in " + rows["Currency"] + ", position in "
                + position_currency[mismatch] + ".",
    )


def matchable(data: InputData, ctx: Context) -> pd.Series:
    # Evaluated rows (rules 11, 12) with a complete key and a valid ISIN (rule 1).
    key = ctx.config.keys["cashflow_to_position"]
    cashflows = data.cashflows
    return (evaluated_rows(data, "cashflows", ctx) & valid_isin(cashflows, ctx)
            & cashflows[list(key)].notna().all(axis=1))


def allowed_portfolio(cashflows: pd.DataFrame, ctx: Context) -> pd.Series:
    allowed = ctx.config.allowed_values.get("Portfolio")
    if allowed is None:
        return pd.Series(True, index=cashflows.index)
    return cashflows["Portfolio"].isin(allowed)


def with_security_name(data: InputData, rows: pd.DataFrame, ctx: Context) -> pd.DataFrame:
    # Cashflows have no SecurityName: take it from the matching position,
    # empty when there is none (output_spec.md section 1).
    return rows.assign(SecurityName=position_values(data, rows, "SecurityName", ctx))


def key_text(rows: pd.DataFrame, key: tuple[str, ...]) -> pd.Series | None:
    return rows[list(key)].astype(str).agg("+".join, axis=1) if not rows.empty else None
