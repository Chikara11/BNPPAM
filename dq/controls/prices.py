from __future__ import annotations

import pandas as pd

from dq.analytics import USABLE, previous_price_by_position, price_change_pct
from dq.context import Context
from dq.exceptions import make_duplicate_exceptions, make_exceptions
from dq.loaders import InputData


def check_no_price_history(data: InputData, ctx: Context) -> pd.DataFrame:
    # Only when the ISIN has no previous_prices row at all. Rows that exist
    # but cannot be used are reported by their own date or value control.
    positions = data.positions
    priced = data.previous_prices["ISIN"].dropna()
    new = positions["ISIN"].notna() & ~positions["ISIN"].isin(priced)  # rule 1
    return make_exceptions(
        positions[new], "NO_PRICE_HISTORY", ctx, "positions", "ISIN",
        comment="No previous price for this security (new security): no price movement "
                "is computed.",
    )


def check_price_move_above_threshold(data: InputData, ctx: Context) -> pd.DataFrame:
    positions = data.positions
    previous = previous_price_by_position(data, ctx)
    market_price = positions["MarketPrice"]
    previous_price = previous["PreviousPrice"]
    threshold = positions["AssetType"].map(ctx.config.price_move_thresholds_pct)
    # Rule 7: no movement when a price is missing or <= 0, or when the
    # previous price is unusable; no threshold for an unknown AssetType.
    computable = ((previous["PreviousPriceStatus"] == USABLE)
                  & (market_price > 0) & (previous_price > 0) & threshold.notna())
    change = price_change_pct(market_price, previous_price)
    alert = computable & (change.abs() > threshold)
    rows = positions[alert]
    comments = [
        f"Price moved from {before:.2f} on {date:%Y-%m-%d} to {after:.2f}: {pct:+.2f} %, "
        f"above the {limit:g} % threshold for {asset_type}."
        for before, date, after, pct, limit, asset_type in zip(
            previous_price[alert], previous.loc[alert, "PreviousDate"], market_price[alert],
            change[alert], threshold[alert], rows["AssetType"])
    ]
    return make_exceptions(
        rows, "PRICE_MOVE_ABOVE_THRESHOLD", ctx, "positions", "MarketPrice",
        observed=change[alert], comment=pd.Series(comments, index=rows.index, dtype=object),
    )


def check_duplicate_previous_price(data: InputData, ctx: Context) -> pd.DataFrame:
    # Prices of securities not held are ignored (data_spec.md section 2).
    key = ctx.config.keys["previous_price_unique"]
    prices = data.previous_prices
    held = prices["ISIN"].isin(data.positions["ISIN"].dropna())
    complete = prices[list(key)].notna().all(axis=1)
    return make_duplicate_exceptions(prices[held & complete], key, "DUPLICATE_PREVIOUS_PRICE",
                                     ctx, "previous_prices", note=describe_prices)


def describe_prices(group: pd.DataFrame) -> str:
    values = group["PreviousPrice"]
    if values.nunique(dropna=False) == 1:
        return "Same price on every row."
    listed = ", ".join("empty" if pd.isna(value) else f"{value:g}" for value in values)
    return f"Prices differ ({listed})."
