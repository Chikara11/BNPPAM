from __future__ import annotations

import pandas as pd

from dq.context import Context
from dq.exceptions import make_exceptions
from dq.loaders import InputData, empty_rows, truly_missing


def check_missing_isin(data: InputData, ctx: Context) -> pd.DataFrame:
    # Rule 10: a non-text ISIN is a parse issue, reported by INVALID_VALUE.
    # Rule 11: an empty row is reported once, by INVALID_VALUE.
    missing = truly_missing(data, "positions", "ISIN") & ~empty_rows(data, "positions")
    return make_exceptions(
        data.positions[missing], "MISSING_ISIN", ctx, "positions", "ISIN",
        comment="ISIN is empty: the position cannot be identified, so no other "
                "ISIN-based control runs on this row.",
    )
