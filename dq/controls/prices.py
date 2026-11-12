from __future__ import annotations

import pandas as pd

from dq.analytics import duplicate_candidates, price_move_table, valid_isin
from dq.context import Context
from dq.controls.common import evaluated_rows
from dq.exceptions import make_duplicate_exceptions, make_exceptions, short_list
from dq.loaders import InputData


def check_no_price_history(data: InputData, ctx: Context) -> pd.DataFrame:
    """NO_PRICE_HISTORY: held security with no previous_prices row at all.

    Returns one exception per position. Rows that exist but cannot be used are
    reported by their own date or value control; a missing or badly formatted
    ISIN skips the check (exception rule 1).
    """
    positions = data.positions
    priced = data.previous_prices["ISIN"].dropna()
    new = (evaluated_rows(data, "positions", ctx) & valid_isin(positions, ctx)  # rule 1
           & ~positions["ISIN"].isin(priced))
    return make_exceptions(
        positions[new], "NO_PRICE_HISTORY", ctx, "positions", "ISIN",
        comment="No previous price for this security (new security): no price movement "
                "is computed.",
    )


def check_price_move_above_threshold(data: InputData, ctx: Context) -> pd.DataFrame:
    """PRICE_MOVE_ABOVE_THRESHOLD: |PriceChangePct| strictly above the threshold.

    Returns one exception per position, on MarketPrice. The movement comes from
    analytics.price_move_table (shared with price_movements) and exists only
    when both prices are present and > 0 (exception rule 7).
    """
    moves = price_move_table(data, ctx)
    alert = evaluated_rows(data, "positions", ctx) & moves["Breach"].fillna(False).astype(bool)
    rows, move = data.positions[alert], moves[alert]
    comments = [
        f"Price moved from {before:.2f} on {date:%Y-%m-%d} to {after:.2f}: {pct:+.2f} %, "
        f"above the {limit:g} % threshold for {asset_type}."
        for before, date, after, pct, limit, asset_type in zip(
            move["PreviousPrice"], move["PreviousDate"], move["MarketPrice"],
            move["PriceChangePct"], move["ThresholdPct"], move["AssetType"])
    ]
    return make_exceptions(
        rows, "PRICE_MOVE_ABOVE_THRESHOLD", ctx, "positions", "MarketPrice",
        observed=move["PriceChangePct"], comment=pd.Series(comments, index=rows.index, dtype=object),
    )


def check_duplicate_previous_price(data: InputData, ctx: Context) -> pd.DataFrame:
    """DUPLICATE_PREVIOUS_PRICE: same previous_price_unique key on several rows.

    Returns one exception per duplicated key (exception rule 5), on securities
    held only: prices of securities not held are ignored (data_spec.md
    section 2). The Comment says whether the prices differ (rule 7).
    """
    candidates = duplicate_candidates(data, "previous_prices", ctx)
    return make_duplicate_exceptions(data.previous_prices[candidates],
                                     ctx.config.keys["previous_price_unique"],
                                     "DUPLICATE_PREVIOUS_PRICE", ctx, "previous_prices",
                                     note=describe_prices)


def describe_prices(group: pd.DataFrame) -> str:
    values = group["PreviousPrice"]
    if values.nunique(dropna=False) == 1:
        return "Same price on every row."
    listed = short_list(["empty" if pd.isna(value) else f"{value:g}" for value in values])
    return f"Prices differ ({listed})."
