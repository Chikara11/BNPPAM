from __future__ import annotations

import pandas as pd

from dq.context import Context
from dq.loaders import InputData

# Status of the previous price of a position (output_spec.md MoveStatus).
USABLE = "Usable"
MISSING_ISIN = "Missing ISIN"
NO_PRICE_HISTORY = "No price history"
NO_USABLE_PRICE = "No usable previous price"
CONFLICTING_PRICES = "Conflicting previous prices"


def price_change_pct(market_price: pd.Series, previous_price: pd.Series) -> pd.Series:
    # CLAUDE.md rule 7: in this order, rounded to 6 decimals before any comparison.
    return ((market_price - previous_price) / previous_price * 100).round(6)


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
