from __future__ import annotations

import pandas as pd

from dq.analytics import duplicate_candidates
from dq.config import COLUMN_TYPES
from dq.context import Context
from dq.controls.common import evaluated_rows
from dq.exceptions import make_duplicate_exceptions, make_exceptions
from dq.loaders import InputData, truly_missing


def check_missing_isin(data: InputData, ctx: Context) -> pd.DataFrame:
    """MISSING_ISIN: positions whose ISIN is empty.

    Returns one exception per row (exception rule 1). A non-text ISIN is a
    parse issue (rule 10) and an empty row is reported by INVALID_VALUE
    (rule 11), not here.
    """
    missing = evaluated_rows(data, "positions", ctx) & truly_missing(data, "positions", "ISIN")
    return make_exceptions(
        data.positions[missing], "MISSING_ISIN", ctx, "positions", "ISIN",
        comment="ISIN is empty: the position cannot be identified, so no other "
                "ISIN-based control runs on this row.",
    )


def check_duplicate_position(data: InputData, ctx: Context) -> pd.DataFrame:
    """DUPLICATE_POSITION: same position_unique key on several rows.

    Returns one exception per duplicated key (exception rule 5). Only complete
    keys with a valid ISIN are compared: an empty key column is reported by
    MISSING_ISIN or MISSING_VALUE, a bad ISIN by INVALID_VALUE (rule 1).
    """
    candidates = duplicate_candidates(data, "positions", ctx)
    return make_duplicate_exceptions(
        data.positions[candidates], ctx.config.keys["position_unique"], "DUPLICATE_POSITION",
        ctx, "positions", note=describe_position_differences,
    )


def describe_position_differences(group: pd.DataFrame) -> str:
    # Only the first row is controlled (rule 12): name what the others change.
    differing = [column for column in COLUMN_TYPES["positions"]
                 if group[column].nunique(dropna=False) > 1]
    rows = f"Rows differ on {', '.join(differing)}." if differing else "Rows are identical."
    return f"Each row with valid values is included in totals. {rows}"


def check_missing_market_price(data: InputData, ctx: Context) -> pd.DataFrame:
    """MISSING_MARKET_PRICE: positions whose MarketPrice is empty.

    Returns one exception per row (exception rule 2). A price <= 0 is
    INVALID_PRICE (rule 3), an unreadable one INVALID_VALUE (rule 10).
    """
    missing = (evaluated_rows(data, "positions", ctx)
               & truly_missing(data, "positions", "MarketPrice"))
    return make_exceptions(
        data.positions[missing], "MISSING_MARKET_PRICE", ctx, "positions", "MarketPrice",
        comment="Current market price is empty: no market value and no price movement "
                "can be computed.",
    )


def check_invalid_nominal(data: InputData, ctx: Context) -> pd.DataFrame:
    """INVALID_NOMINAL: positions whose Nominal is present and <= 0.

    Returns one exception per row. An empty Nominal is MISSING_VALUE, not
    invalid (exception rule 3).
    """
    nominal = data.positions["Nominal"]
    invalid = evaluated_rows(data, "positions", ctx) & nominal.notna() & (nominal <= 0)
    rows = data.positions[invalid]
    return make_exceptions(
        rows, "INVALID_NOMINAL", ctx, "positions", "Nominal", observed=rows["Nominal"],
        comment="Nominal must be greater than 0.",
    )


def check_missing_rating(data: InputData, ctx: Context) -> pd.DataFrame:
    """MISSING_RATING: empty Rating on an asset type of rating_required_for.

    Returns one exception per row (exception rule 2). CLO Equity may be
    unrated; an empty or unknown AssetType is reported by MISSING_VALUE or
    INVALID_VALUE, not here.
    """
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
