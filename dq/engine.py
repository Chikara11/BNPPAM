from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from dq import analytics
from dq.config import Config
from dq.context import Context, build_context
from dq.controls import CONTROL_REGISTRY
from dq.exceptions import combine_exceptions
from dq.loaders import InputData

SORT_COLUMNS = ["Severity", "ControlID", "Portfolio", "ISIN", "SourceRow", "Field"]


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Results:
    reference_date: pd.Timestamp
    config: Config
    exceptions: pd.DataFrame
    price_movements: pd.DataFrame
    upcoming_cashflows: pd.DataFrame
    data_quality: pd.DataFrame
    dimension_quality: pd.DataFrame
    summary: pd.DataFrame


def run(data: InputData, config: Config) -> Results:
    """Run the engine on loaded inputs: the only entry point.

    Checks the registry, builds the context (one valuation date), runs the
    enabled controls in config.yaml order, sorts and numbers the exceptions,
    then computes the analytics. Returns Results.
    """
    check_registry(config)
    ctx = build_context(data, config)
    exceptions = assign_exception_ids(sort_exceptions(run_controls(data, ctx)))
    return Results(
        reference_date=ctx.reference_date,
        config=config,
        exceptions=exceptions,
        price_movements=analytics.price_movements(data, ctx),
        upcoming_cashflows=analytics.upcoming_cashflows(data, ctx),
        data_quality=analytics.data_quality(data, ctx),
        dimension_quality=analytics.dimension_quality(exceptions, ctx),
        summary=analytics.summary(exceptions, data, ctx),
    )


def check_registry(config: Config) -> None:
    not_registered = [control for control in config.controls if control not in CONTROL_REGISTRY]
    not_configured = [control for control in CONTROL_REGISTRY if control not in config.controls]
    problems = []
    if not_registered:
        problems.append(f"in config.yaml but not registered: {', '.join(not_registered)}")
    if not_configured:
        problems.append(f"registered but not in config.yaml: {', '.join(not_configured)}")
    if problems:
        raise RegistryError("config.yaml and CONTROL_REGISTRY do not match: "
                            + "; ".join(problems) + ".")


def run_controls(data: InputData, ctx: Context) -> pd.DataFrame:
    # config.yaml order = run order
    return combine_exceptions([CONTROL_REGISTRY[control_id](data, ctx)
                               for control_id, settings in ctx.config.controls.items()
                               if settings.enabled])


def sort_exceptions(exceptions: pd.DataFrame) -> pd.DataFrame:
    # Severity is an ordered category: Critical, Warning, Information.
    return exceptions.sort_values(SORT_COLUMNS, na_position="last",
                                  kind="stable").reset_index(drop=True)


def assign_exception_ids(exceptions: pd.DataFrame) -> pd.DataFrame:
    ids = [f"EXC-{number:04d}" for number in range(1, len(exceptions) + 1)]
    return exceptions.assign(ExceptionID=pd.Series(ids, index=exceptions.index, dtype=object))
