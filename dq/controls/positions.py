from __future__ import annotations

import pandas as pd

from dq.context import Context
from dq.controls.common import duplicate_candidates, evaluated_rows
from dq.exceptions import make_duplicate_exceptions, make_exceptions
from dq.loaders import InputData, truly_missing


def check_missing_isin(data: InputData, ctx: Context) -> pd.DataFrame:
    # Rule 10: a non-text ISIN is a parse issue, reported by INVALID_VALUE.
    # Rule 11: an empty row is reported once, by INVALID_VALUE.
    missing = evaluated_rows(data, "positions", ctx) & truly_missing(data, "positions", "ISIN")
    return make_exceptions(
        data.positions[missing], "MISSING_ISIN", ctx, "positions", "ISIN",
        comment="ISIN is empty: the position cannot be identified, so no other "
                "ISIN-based control runs on this row.",
    )


def check_duplicate_position(data: InputData, ctx: Context) -> pd.DataFrame:
    # Only complete keys with a valid ISIN are compared: an empty key column is
    # reported by MISSING_ISIN or MISSING_VALUE, a bad ISIN by INVALID_VALUE.
    candidates = duplicate_candidates(data, "positions", ctx)
    return make_duplicate_exceptions(
        data.positions[candidates], ctx.config.keys["position_unique"], "DUPLICATE_POSITION",
        ctx, "positions", note="The position is counted twice in totals.",
    )


def check_missing_market_price(data: InputData, ctx: Context) -> pd.DataFrame:
    missing = (evaluated_rows(data, "positions", ctx)
               & truly_missing(data, "positions", "MarketPrice"))
    return make_exceptions(
        data.positions[missing], "MISSING_MARKET_PRICE", ctx, "positions", "MarketPrice",
        comment="Current market price is empty: no market value and no price movement "
                "can be computed.",
    )


def check_invalid_nominal(data: InputData, ctx: Context) -> pd.DataFrame:
    # Rule 3: an empty Nominal is MISSING_VALUE, only a present value <= 0 is invalid.
    nominal = data.positions["Nominal"]
    invalid = evaluated_rows(data, "positions", ctx) & nominal.notna() & (nominal <= 0)
    rows = data.positions[invalid]
    return make_exceptions(
        rows, "INVALID_NOMINAL", ctx, "positions", "Nominal", observed=rows["Nominal"],
        comment="Nominal must be greater than 0.",
    )


def check_missing_rating(data: InputData, ctx: Context) -> pd.DataFrame:
    # CLO Equity is not rated; an empty or unknown AssetType is reported by
    # MISSING_VALUE or INVALID_VALUE, not here.
    positions = data.positions
    missing = (evaluated_rows(data, "positions", ctx)
               & positions["AssetType"].isin(ctx.config.rating_required_for)
               & truly_missing(data, "positions", "Rating"))
    rows = positions[missing]
    return make_exceptions(
        rows, "MISSING_RATING", ctx, "positions", "Rating",
        comment="Rating is empty on " + rows["AssetType"].astype(str)
                + ", where a rating is expected.",
    )
