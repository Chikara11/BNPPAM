from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import pandas as pd

from dq.config import Config
from dq.loaders import InputData, InputSchemaError


@dataclass(frozen=True)
class Context:
    config: Config
    reference_date: pd.Timestamp
    horizon_end: pd.Timestamp
    ignored_position_rows: int  # valid ValuationDate different from the reference date


def build_context(data: InputData, config: Config) -> Context:
    """Build the Context of a run: config, reference date and horizon end.

    Reference date = reference_date of the config, else the latest valid
    ValuationDate; InputSchemaError if there is neither (CLAUDE.md "Reference
    date"). Also counts the position rows at another date, which are ignored.
    """
    reference_date = resolve_reference_date(data.positions, config)
    dates = data.positions["ValuationDate"]
    ignored = int((dates.notna() & (dates != reference_date)).sum())
    return Context(
        config=config,
        reference_date=reference_date,
        horizon_end=reference_date + pd.Timedelta(days=config.cashflow_horizon_days),
        ignored_position_rows=ignored,
    )


def resolve_reference_date(positions: pd.DataFrame, config: Config) -> pd.Timestamp:
    if config.reference_date is not None:
        return config.reference_date
    dates = positions["ValuationDate"].dropna()
    if dates.empty:
        raise InputSchemaError("The positions file has no valid ValuationDate, so there is no "
                               "reference date. Fix the file or set reference_date in "
                               "config.yaml.")
    return dates.max()


def positions_at_reference_date(data: InputData, ctx: Context) -> InputData:
    """Return `data` with only the positions dated at the reference date.

    Rows with an empty or unparseable ValuationDate are kept, so that the
    controls report them instead of dropping them silently (CLAUDE.md "Data
    policy": never silently repair).
    """
    dates = data.positions["ValuationDate"]
    keep = dates.isna() | (dates == ctx.reference_date)
    return dataclasses.replace(data, positions=data.positions.loc[keep])
