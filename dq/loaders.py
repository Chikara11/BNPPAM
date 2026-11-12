from __future__ import annotations

import datetime
import numbers
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import numpy as np
import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException

from dq.config import COLUMN_TYPES, INPUT_FILES, Config
from dq.schema import PARSE_ISSUE_COLUMNS, empty_frame

Source = str | Path | IO[bytes]

XLSX_SIGNATURE = b"PK\x03\x04"  # an .xlsx file is a zip archive


class InputSchemaError(Exception):
    pass


@dataclass(frozen=True)
class InputData:
    positions: pd.DataFrame
    previous_prices: pd.DataFrame
    cashflows: pd.DataFrame
    parse_issues: pd.DataFrame


def load_inputs(positions: Source, previous_prices: Source, cashflows: Source,
                config: Config) -> InputData:
    """Read the 3 Excel files (paths or uploaded files) and convert their types.

    Returns InputData: the 3 DataFrames with SourceRow, plus parse_issues for
    the cells that failed conversion (exception rule 10). A missing required
    column raises InputSchemaError naming the file and the columns.
    """
    sources = {"positions": positions, "previous_prices": previous_prices,
               "cashflows": cashflows}
    frames, issues = {}, []
    for file in INPUT_FILES:
        raw = read_raw(sources[file], file)
        check_required_columns(raw, file, config.required_columns[file])
        frames[file], file_issues = convert_file(raw, file)
        issues.append(file_issues)
    return InputData(**frames, parse_issues=sort_parse_issues(pd.concat(issues)))


def read_raw(source: Source, file: str) -> pd.DataFrame:
    name = source_name(source, file)
    check_xlsx_signature(source, file, name)
    try:
        # openpyxl only: pandas would pick another engine (not installed) for
        # an .xls file and fail with an ImportError
        with pd.ExcelFile(source, engine="openpyxl") as workbook:
            check_single_visible_sheet(workbook, file, name)
            # header=None: the header row is read as data so that a duplicated
            # column name can be detected before pandas renames it "X.1"
            sheet = pd.read_excel(workbook, sheet_name=0, header=None, dtype=object)
    # KeyError: a zip archive that is not a workbook (no [Content_Types].xml)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, InvalidFileException) as error:
        raise InputSchemaError(f"The {file} file ({name}) cannot be read: it is damaged or "
                               f"is not an Excel workbook. Save it again as .xlsx from "
                               f"Excel.") from error
    if sheet.empty:
        raise InputSchemaError(f"The {file} file ({name}) is empty: no header row.")
    header = [header_name(value, position) for position, value in enumerate(sheet.iloc[0])]
    duplicated = sorted({name for name in header if header.count(name) > 1})
    if duplicated:
        raise InputSchemaError(f"The {file} file ({name}) has duplicated column name(s) "
                               f"in its header: {', '.join(duplicated)}.")
    data = sheet.iloc[1:].copy()
    data.columns = header
    data["SourceRow"] = pd.array(data.index + 1, dtype="Int64")  # header = Excel row 1
    return data.reset_index(drop=True)


def check_xlsx_signature(source: Source, file: str, name: str) -> None:
    # An .xls, a .csv or a password-protected workbook (an encrypted container,
    # not a zip) does not start like an .xlsx file: say so in plain words.
    try:
        start = first_bytes(source, len(XLSX_SIGNATURE))
    except OSError as error:
        raise InputSchemaError(f"The {file} file ({name}) cannot be opened: "
                               f"{error.strerror or error}.") from error
    if start != XLSX_SIGNATURE:
        raise InputSchemaError(f"The {file} file ({name}) is not an .xlsx workbook: it may be "
                               f"an .xls or .csv file, or password-protected. Save it as .xlsx "
                               f"without a password.")


def first_bytes(source: Source, count: int) -> bytes:
    if isinstance(source, (str, Path)):
        with open(source, "rb") as handle:
            return handle.read(count)
    source.seek(0)  # an uploaded file may already have been read once
    start = source.read(count)
    source.seek(0)
    return start


def check_single_visible_sheet(workbook: pd.ExcelFile, file: str, name: str) -> None:
    # data_spec.md "File format": one sheet. pandas reads the first sheet even
    # when it is hidden, so a stale hidden sheet would replace the visible data.
    sheets = [workbook.book[title] for title in workbook.book.sheetnames]  # chartsheets too
    if len(sheets) == 1 and sheets[0].sheet_state == "visible":
        return
    found = ", ".join(sheet.title if sheet.sheet_state == "visible"
                      else f"{sheet.title} ({sheet.sheet_state})" for sheet in sheets)
    raise InputSchemaError(f"The {file} file ({name}) must have exactly one visible sheet; "
                           f"found: {found}. Processing stopped.")


def check_required_columns(raw: pd.DataFrame, file: str, required: tuple[str, ...]) -> None:
    missing = [column for column in required if column not in raw.columns]
    if missing:
        raise InputSchemaError(f"The {file} file is missing required column(s): "
                               f"{', '.join(missing)}. Processing stopped.")


def convert_file(raw: pd.DataFrame, file: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    converted = raw.copy()
    issues = []
    for column in raw.columns.drop("SourceRow"):
        kind = COLUMN_TYPES[file].get(column)
        values, failed = convert_column(raw[column], kind)
        converted[column] = values
        issues.append(pd.DataFrame({
            "File": file,
            "SourceRow": raw.loc[failed, "SourceRow"],
            "Column": column,
            "RawValue": raw.loc[failed, column].map(str),
        }))
    return converted, pd.concat(issues) if issues else empty_frame(PARSE_ISSUE_COLUMNS)


def convert_column(raw: pd.Series, kind: str | None) -> tuple[pd.Series, pd.Series]:
    blank = raw.map(is_blank).astype(bool)
    if kind is None:
        values = pd.Series([None if is_empty else value
                            for value, is_empty in zip(raw, blank)],
                           index=raw.index, dtype="object")
        return values, pd.Series(False, index=raw.index)
    accepted = raw.map(ACCEPTS[kind]).astype(bool) & ~blank
    failed = ~accepted & ~blank
    keep = [value if ok else None for value, ok in zip(raw, accepted)]
    if kind == "text":
        values = pd.Series(keep, index=raw.index, dtype="object")
    elif kind == "number":
        values = pd.Series([np.nan if value is None else float(value) for value in keep],
                           index=raw.index, dtype="float64")
    else:
        values = pd.Series([pd.NaT if value is None else pd.Timestamp(value) for value in keep],
                           index=raw.index).astype("datetime64[us]").dt.normalize()
    return values, failed


def is_blank(value: Any) -> bool:
    if isinstance(value, str):
        return not value.strip()
    return value is None or bool(pd.isna(value))


def is_number_cell(value: Any) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def is_date_cell(value: Any) -> bool:
    return isinstance(value, datetime.date)  # also covers datetime and Timestamp


ACCEPTS = {
    "text": lambda value: isinstance(value, str),
    "number": is_number_cell,
    "date": is_date_cell,
}


def parse_issues_of(data: InputData, file: str, column: str) -> pd.DataFrame:
    issues = data.parse_issues
    return issues[(issues["File"] == file) & (issues["Column"] == column)]


def truly_missing(data: InputData, file: str, column: str) -> pd.Series:
    """Bool Series aligned on the file: the cell is empty and has no parse issue.

    A value that could not be read is reported by INVALID_VALUE, never by a
    MISSING_* control (exception rule 10).
    """
    frame = getattr(data, file)
    unparsed = parse_issues_of(data, file, column)["SourceRow"]
    return frame[column].isna() & ~frame["SourceRow"].isin(unparsed)


def empty_rows(data: InputData, file: str) -> pd.Series:
    """Bool Series aligned on the file: every column of the row is empty.

    Such a row is reported once, by INVALID_VALUE, and skipped by every other
    control (exception rule 11).
    """
    frame = getattr(data, file)
    issues = data.parse_issues
    unparsed = issues.loc[issues["File"] == file, "SourceRow"]
    all_empty = frame.drop(columns="SourceRow").isna().all(axis=1)
    return all_empty & ~frame["SourceRow"].isin(unparsed)


def sort_parse_issues(issues: pd.DataFrame) -> pd.DataFrame:
    if issues.empty:
        return empty_frame(PARSE_ISSUE_COLUMNS)
    file_rank = issues["File"].map(INPUT_FILES.index)
    column_rank = [column_order(file, column) for file, column in zip(issues["File"],
                                                                         issues["Column"])]
    ordered = issues.assign(_file=file_rank, _column=column_rank).sort_values(
        ["_file", "SourceRow", "_column"], kind="stable")
    return ordered.drop(columns=["_file", "_column"]).reset_index(drop=True).astype(
        PARSE_ISSUE_COLUMNS)


def column_order(file: str, column: str) -> int:
    known = list(COLUMN_TYPES[file])
    return known.index(column) if column in known else len(known)


def header_name(value: Any, position: int) -> str:
    if is_blank(value):
        return f"Unnamed: {position}"
    return value if isinstance(value, str) else str(value)


def source_name(source: Source, file: str) -> str:
    if isinstance(source, (str, Path)):
        return str(source)
    return getattr(source, "name", f"uploaded {file} file")
