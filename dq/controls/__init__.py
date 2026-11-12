from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from dq.context import Context
from dq.controls import cashflows, positions, prices
from dq.loaders import InputData

# Every control of config.yaml must be here and the reverse (engine.check_registry).
CONTROL_REGISTRY: dict[str, Callable[[InputData, Context], pd.DataFrame]] = {
    "MISSING_ISIN": positions.check_missing_isin,
    "DUPLICATE_POSITION": positions.check_duplicate_position,
    "MISSING_MARKET_PRICE": positions.check_missing_market_price,
    "INVALID_NOMINAL": positions.check_invalid_nominal,
    "MISSING_RATING": positions.check_missing_rating,
    "PRICE_MOVE_ABOVE_THRESHOLD": prices.check_price_move_above_threshold,
    "NO_PRICE_HISTORY": prices.check_no_price_history,
    "DUPLICATE_PREVIOUS_PRICE": prices.check_duplicate_previous_price,
    "UNMATCHED_CASHFLOW": cashflows.check_unmatched_cashflow,
    "DUPLICATE_CASHFLOW": cashflows.check_duplicate_cashflow,
    "PAST_FORECAST_CASHFLOW": cashflows.check_past_forecast_cashflow,
    "CURRENCY_MISMATCH": cashflows.check_currency_mismatch,
}
