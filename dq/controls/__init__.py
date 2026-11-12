from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from dq.context import Context
from dq.controls import positions
from dq.loaders import InputData

# Every control of config.yaml must be here and the reverse (engine.check_registry).
CONTROL_REGISTRY: dict[str, Callable[[InputData, Context], pd.DataFrame]] = {
    "MISSING_ISIN": positions.check_missing_isin,
}
