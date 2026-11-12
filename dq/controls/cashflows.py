from __future__ import annotations

import pandas as pd

from dq.analytics import (duplicate_candidates, key_tuples, matches_a_position,
                          matching_positions, valid_isin)
from dq.context import Context
from dq.controls.common import evaluated_rows
from dq.exceptions import make_duplicate_exceptions, make_exceptions
from dq.loaders import InputData


def check_unmatched_cashflow(data: InputData, ctx: Context) -> pd.DataFrame:
    # Rule 4: Portfolio + ISIN must exist in the positions; never a row join.
    # An empty Portfolio or ISIN is reported by MISSING_VALUE, a bad ISIN or a
    # Portfolio outside the allowed list by INVALID_VALUE, not here.
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
    candidates = duplicate_candidates(data, "cashflows", ctx)
    rows = with_security_name(data, data.cashflows[candidates], ctx)
    return make_duplicate_exceptions(rows, ctx.config.keys["cashflow_unique"],
                                     "DUPLICATE_CASHFLOW", ctx, "cashflows")


def check_past_forecast_cashflow(data: InputData, ctx: Context) -> pd.DataFrame:
    # "Past-dated" = strictly before the reference date. A Status outside the
    # allowed values (e.g. " Forecast") is reported by INVALID_VALUE only.
    cashflows = data.cashflows
    past = (evaluated_rows(data, "cashflows", ctx) & (cashflows["Status"] == "Forecast")
            & (cashflows["CashflowDate"] < ctx.reference_date))
    rows = with_security_name(data, cashflows[past], ctx)
    return make_exceptions(
        rows, "PAST_FORECAST_CASHFLOW", ctx, "cashflows", "CashflowDate",
        observed=rows["CashflowDate"],
        comment=f"Dated before the reference date ({ctx.reference_date:%Y-%m-%d}) but still "
                f"Forecast: it should be Paid or re-dated.",
    )


def check_currency_mismatch(data: InputData, ctx: Context) -> pd.DataFrame:
    # Rule 6: only on cashflows that match a position. A currency outside the
    # allowed list is reported by INVALID_VALUE only, so it is not compared.
    key = ctx.config.keys["cashflow_to_position"]
    allowed = set(ctx.config.allowed_values["Currency"])
    cashflows = data.cashflows
    positions = matching_positions(data, ctx)
    currencies_by_key: dict[tuple, set] = {}
    for values, currency in zip(key_tuples(positions, key), positions["Currency"]):
        if currency in allowed:
            currencies_by_key.setdefault(values, set()).add(currency)
    expected = [currencies_by_key.get(values, set()) for values in key_tuples(cashflows, key)]
    different = pd.Series(
        [currency in allowed and bool(currencies) and currency not in currencies
         for currency, currencies in zip(cashflows["Currency"], expected)],
        index=cashflows.index)
    mismatch = matchable(data, ctx) & matches_a_position(data, ctx) & different
    rows = with_security_name(data, cashflows[mismatch], ctx)
    comments = [f"Cashflow in {currency}, position in {', '.join(sorted(currencies))}."
                for currency, currencies, flagged in zip(cashflows["Currency"], expected, mismatch)
                if flagged]
    return make_exceptions(
        rows, "CURRENCY_MISMATCH", ctx, "cashflows", "Currency", observed=rows["Currency"],
        comment=pd.Series(comments, index=rows.index, dtype=object),
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
    key = ctx.config.keys["cashflow_to_position"]
    names = (matching_positions(data, ctx)
             .drop_duplicates(list(key))
             .set_index(list(key))["SecurityName"])
    looked_up = [names.get(values) for values in key_tuples(rows, key)]
    return rows.assign(SecurityName=pd.Series(looked_up, index=rows.index, dtype=object))


def key_text(rows: pd.DataFrame, key: tuple[str, ...]) -> pd.Series | None:
    return rows[list(key)].astype(str).agg("+".join, axis=1) if not rows.empty else None
