from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from dq.config import Config
from dq.loaders import InputData, InputSchemaError


@dataclass(frozen=True)
class Context:
    config: Config
    reference_date: pd.Timestamp
    horizon_end: pd.Timestamp


def build_context(data: InputData, config: Config) -> Context:
    """Build the Context of a run: config, reference date and horizon end.

    Reference date = the single valid ValuationDate of the positions; it must
    equal reference_date of the config when that is set. InputSchemaError
    otherwise (CLAUDE.md "Reference date").
    """
    reference_date = resolve_reference_date(data.positions, config)
    return Context(
        config=config,
        reference_date=reference_date,
        horizon_end=reference_date + pd.Timedelta(days=config.cashflow_horizon_days),
    )


def resolve_reference_date(positions: pd.DataFrame, config: Config) -> pd.Timestamp:
    # One wrong ValuationDate would otherwise move the reference date or drop
    # positions from every total without an exception. Empty or unparseable
    # dates are not counted: MISSING_VALUE / INVALID_VALUE report them.
    counts = positions["ValuationDate"].dropna().value_counts().sort_index()
    if len(counts) != 1:
        found = ", ".join(f"{date:%Y-%m-%d} ({count} row{'s' if count > 1 else ''})"
                          for date, count in counts.items()) or "none"
        raise InputSchemaError(f"The positions file must carry exactly one valid "
                               f"ValuationDate; found: {found}. Processing stopped.")
    valuation_date = counts.index[0]
    if config.reference_date is not None and config.reference_date != valuation_date:
        raise InputSchemaError(f"reference_date in config.yaml ({config.reference_date:%Y-%m-%d}) "
                               f"differs from the ValuationDate of the positions "
                               f"({valuation_date:%Y-%m-%d}). Processing stopped.")
    return valuation_date
