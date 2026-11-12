from __future__ import annotations

import datetime
from typing import Any

import pandas as pd

from dq.context import Context
from dq.schema import EXCEPTION_COLUMNS, conform, empty_exceptions


def make_exceptions(rows: pd.DataFrame, control_id: str, ctx: Context, file: str, field: str,
                    *, observed: pd.Series | None = None,
                    comment: str | pd.Series) -> pd.DataFrame:
    if rows.empty:
        return empty_exceptions()
    settings = ctx.config.controls[control_id]
    exceptions = pd.DataFrame({
        "ExceptionID": None,  # assigned by the engine after sorting
        "Severity": settings.severity,
        "ControlID": control_id,
        "AnomalyType": settings.label,
        "Source": file,
        "Portfolio": column_or_empty(rows, "Portfolio"),
        "ISIN": column_or_empty(rows, "ISIN"),
        "SecurityName": column_or_empty(rows, "SecurityName"),
        "Field": field,
        "ObservedValue": None if observed is None else observed.map(as_text),
        "Comment": comment,
        "SourceRow": rows["SourceRow"],
    }, index=rows.index)
    return conform(exceptions.reset_index(drop=True), EXCEPTION_COLUMNS)


def column_or_empty(rows: pd.DataFrame, column: str) -> pd.Series:
    if column in rows.columns:
        return rows[column].astype(object).where(rows[column].notna(), None)
    return pd.Series(None, index=rows.index, dtype=object)


def as_text(value: Any) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, datetime.datetime) and value == value.replace(hour=0, minute=0,
                                                                        second=0, microsecond=0):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))  # 5000000.0 -> "5000000"
    return str(value)
