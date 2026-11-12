from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Any

import pandas as pd

from dq.context import Context
from dq.schema import EXCEPTION_COLUMNS, conform, empty_exceptions

# An Excel cell holds at most 32,767 characters: a key repeated on thousands
# of rows would have its comment cut silently in the report.
MAX_ROWS_IN_COMMENT = 20


def make_exceptions(rows: pd.DataFrame, control_id: str, ctx: Context, file: str, field: str,
                    *, observed: pd.Series | None = None,
                    comment: str | pd.Series) -> pd.DataFrame:
    """Build one exception per row of `rows` in the common exception format.

    Severity, Dimension and AnomalyType come from config.yaml; Portfolio, ISIN
    and SourceRow from the row (SecurityName and AssetType are added by the
    engine); `observed` (aligned on rows) becomes ObservedValue as text.
    Returns output_spec.md section 1.
    """
    if rows.empty:
        return empty_exceptions()
    settings = ctx.config.controls[control_id]
    exceptions = pd.DataFrame({
        "ExceptionID": None,  # assigned by the engine after sorting
        "Severity": settings.severity,
        "ControlID": control_id,
        "Dimension": settings.dimension,
        "AnomalyType": settings.label,
        "Source": file,
        "Portfolio": column_or_empty(rows, "Portfolio"),
        "ISIN": column_or_empty(rows, "ISIN"),
        # filled by the engine, from the position (one place for the 3 files)
        "SecurityName": None,
        "AssetType": None,
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


def make_duplicate_exceptions(frame: pd.DataFrame, key: tuple[str, ...], control_id: str,
                              ctx: Context, file: str,
                              note: str | Callable[[pd.DataFrame], str] = "") -> pd.DataFrame:
    # One exception per duplicated key (rule 5), raised on its first row.
    # note is added to the Comment; a function gets the rows of the key.
    duplicated = frame[frame.duplicated(list(key), keep=False)]
    if duplicated.empty:
        return empty_exceptions()
    first_labels, observed, comments = [], [], []
    for _, group in duplicated.groupby(list(key), sort=False, dropna=False):
        group = group.sort_values("SourceRow")
        source_rows = short_list([str(row) for row in group["SourceRow"]])
        text = note(group) if callable(note) else note
        first_labels.append(group.index[0])
        observed.append("+".join(as_text(value) for value in group.iloc[0][list(key)]))
        comments.append(f"Same {' + '.join(key)} on {len(group)} rows (Excel rows "
                        f"{source_rows}).{' ' + text if text else ''}")
    rows = frame.loc[first_labels]
    return make_exceptions(rows, control_id, ctx, file, "+".join(key),
                           observed=pd.Series(observed, index=rows.index),
                           comment=pd.Series(comments, index=rows.index))


def short_list(items: list[str]) -> str:
    # The first MAX_ROWS_IN_COMMENT items, then how many are left out.
    shown = ", ".join(items[:MAX_ROWS_IN_COMMENT])
    left_out = len(items) - MAX_ROWS_IN_COMMENT
    return f"{shown} (+{left_out} more)" if left_out > 0 else shown


def combine_exceptions(frames: list[pd.DataFrame]) -> pd.DataFrame:
    found = [frame for frame in frames if not frame.empty]
    if not found:
        return empty_exceptions()
    return conform(pd.concat(found, ignore_index=True), EXCEPTION_COLUMNS)
