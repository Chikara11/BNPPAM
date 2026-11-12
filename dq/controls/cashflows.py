from __future__ import annotations

import pandas as pd

from dq.context import Context
from dq.exceptions import make_duplicate_exceptions, make_exceptions
from dq.loaders import InputData


def check_unmatched_cashflow(data: InputData, ctx: Context) -> pd.DataFrame:
    # Rule 4: Portfolio + ISIN must exist in the positions; never a row join.
    # An empty Portfolio or ISIN is reported by MISSING_VALUE, not here.
    key = ctx.config.keys["cashflow_to_position"]
    cashflows = data.cashflows
    unmatched = has_key(cashflows, key) & ~matches_a_position(data, key)
    rows = with_security_name(data, cashflows[unmatched], key)
    return make_exceptions(
        rows, "UNMATCHED_CASHFLOW", ctx, "cashflows", "+".join(key),
        observed=key_text(rows, key),
        comment=f"No position at the reference date with this {' + '.join(key)}.",
    )


def check_duplicate_cashflow(data: InputData, ctx: Context) -> pd.DataFrame:
    key = ctx.config.keys["cashflow_unique"]
    cashflows = data.cashflows
    rows = with_security_name(data, cashflows[has_key(cashflows, key)],
                              ctx.config.keys["cashflow_to_position"])
    return make_duplicate_exceptions(rows, key, "DUPLICATE_CASHFLOW", ctx, "cashflows")


def check_past_forecast_cashflow(data: InputData, ctx: Context) -> pd.DataFrame:
    # "Past-dated" = strictly before the reference date. A Status outside the
    # allowed values (e.g. " Forecast") is reported by INVALID_VALUE only.
    cashflows = data.cashflows
    past = (cashflows["Status"] == "Forecast") & (cashflows["CashflowDate"] < ctx.reference_date)
    rows = with_security_name(data, cashflows[past], ctx.config.keys["cashflow_to_position"])
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
    currencies_by_key: dict[tuple, set] = {}
    for values, currency in zip(key_tuples(positions_with_key(data, key), key),
                                positions_with_key(data, key)["Currency"]):
        if currency in allowed:
            currencies_by_key.setdefault(values, set()).add(currency)
    expected = [currencies_by_key.get(values, set()) for values in key_tuples(cashflows, key)]
    mismatch = pd.Series(
        [currency in allowed and bool(currencies) and currency not in currencies
         for currency, currencies in zip(cashflows["Currency"], expected)],
        index=cashflows.index) & has_key(cashflows, key) & matches_a_position(data, key)
    rows = with_security_name(data, cashflows[mismatch], key)
    comments = [f"Cashflow in {currency}, position in {', '.join(sorted(currencies))}."
                for currency, currencies, flagged in zip(cashflows["Currency"], expected, mismatch)
                if flagged]
    return make_exceptions(
        rows, "CURRENCY_MISMATCH", ctx, "cashflows", "Currency", observed=rows["Currency"],
        comment=pd.Series(comments, index=rows.index, dtype=object),
    )


def has_key(frame: pd.DataFrame, key: tuple[str, ...]) -> pd.Series:
    return frame[list(key)].notna().all(axis=1)


def positions_with_key(data: InputData, key: tuple[str, ...]) -> pd.DataFrame:
    return data.positions[has_key(data.positions, key)]


def matches_a_position(data: InputData, key: tuple[str, ...]) -> pd.Series:
    held = pd.MultiIndex.from_frame(positions_with_key(data, key)[list(key)])
    cashflow_keys = pd.MultiIndex.from_frame(data.cashflows[list(key)])
    return pd.Series(cashflow_keys.isin(held), index=data.cashflows.index)


def with_security_name(data: InputData, rows: pd.DataFrame, key: tuple[str, ...]) -> pd.DataFrame:
    # Cashflows have no SecurityName: take it from the matching position,
    # empty when there is none (output_spec.md section 1).
    names = (positions_with_key(data, key)
             .drop_duplicates(list(key))
             .set_index(list(key))["SecurityName"])
    looked_up = [names.get(values) for values in key_tuples(rows, key)]
    return rows.assign(SecurityName=pd.Series(looked_up, index=rows.index, dtype=object))


def key_tuples(frame: pd.DataFrame, key: tuple[str, ...]) -> list[tuple]:
    return [tuple(values) for values in frame[list(key)].itertuples(index=False)]


def key_text(rows: pd.DataFrame, key: tuple[str, ...]) -> pd.Series:
    return rows[list(key)].astype(str).agg("+".join, axis=1) if not rows.empty else None
