from __future__ import annotations

import datetime
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import Cell
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from dq.config import Config
from dq.engine import Results

# Presentation only, used by this module alone, so these constants live here
# and not in schema.py. Sheet names and number formats are those of
# docs/output_spec.md section 6; they are defined nowhere else.
SUMMARY_SHEET = "Executive Summary"
EXCEPTIONS_SHEET = "Exceptions"
PRICE_MOVEMENTS_SHEET = "Price Movements"
CASHFLOWS_SHEET = "Cashflows {days}D"  # days = cashflow_horizon_days of the run
DATA_QUALITY_SHEET = "Data Quality"
PARAMETERS_SHEET = "Parameters"

AMOUNT_FORMAT = "#,##0.00"
PRICE_FORMAT = "0.00"
PERCENT_FORMAT = "0.00"
COUNT_FORMAT = "0"
DATE_FORMAT = "YYYY-MM-DD"
TIMESTAMP_FORMAT = "YYYY-MM-DD HH:MM:SS"

PERCENT_COLUMNS = ("PriceChangePct", "ThresholdPct", "CompletenessPct")  # header + " %"
COLUMN_FORMATS = {
    "Nominal": AMOUNT_FORMAT, "MarketValue": AMOUNT_FORMAT, "Amount": AMOUNT_FORMAT,
    "PreviousPrice": PRICE_FORMAT, "MarketPrice": PRICE_FORMAT,
    "PreviousDate": DATE_FORMAT, "CashflowDate": DATE_FORMAT,
    **{column: PERCENT_FORMAT for column in PERCENT_COLUMNS},
}
UNIT_FORMATS = {"count": COUNT_FORMAT, "amount": AMOUNT_FORMAT, "pct": PERCENT_FORMAT}

HEADER_FONT = Font(bold=True)
HEADER_FILL = PatternFill(fill_type="solid", start_color="D9D9D9")
SEVERITY_FILLS = {
    "Critical": PatternFill(fill_type="solid", start_color="FFC7CE"),     # light red
    "Warning": PatternFill(fill_type="solid", start_color="FFD8A8"),      # light orange
    "Information": PatternFill(fill_type="solid", start_color="BDD7EE"),  # light blue
}
BREACH_FILL = PatternFill(fill_type="solid", start_color="FFF2CC")  # light yellow
MAX_COLUMN_WIDTH = 60
DATE_WIDTH = 10


def write_report(results: Results, path_or_buffer: str | Path | IO[bytes], *,
                 generated_at: datetime.datetime | None = None) -> None:
    """Write the Excel report of a run: the 6 sheets of output_spec.md section 6.

    Every value comes from `results`; the report only formats (architecture.md
    principle 1). `generated_at` is the run timestamp of the Executive Summary
    (default: now). Text cells are stored as plain text, never as formulas.
    """
    generated_at = generated_at or datetime.datetime.now().replace(microsecond=0)
    workbook = Workbook()
    workbook.remove(workbook.active)
    write_executive_summary(workbook, results, generated_at)
    colour_severities(write_table(workbook, EXCEPTIONS_SHEET, results.exceptions),
                      results.exceptions)
    highlight_breaches(write_table(workbook, PRICE_MOVEMENTS_SHEET, results.price_movements),
                       results.price_movements)
    write_table(workbook, CASHFLOWS_SHEET.format(days=results.config.cashflow_horizon_days),
                results.upcoming_cashflows)
    write_table(workbook, DATA_QUALITY_SHEET, results.data_quality)
    write_table(workbook, PARAMETERS_SHEET, parameters_table(results.config))
    workbook.save(path_or_buffer)


def write_table(workbook: Workbook, title: str, frame: pd.DataFrame) -> Worksheet:
    # One header row, then the rows of the frame as they are: no calculation.
    sheet = workbook.create_sheet(title)
    for column, name in enumerate(frame.columns, start=1):
        style_header(write_cell(sheet, 1, column, header_name(name)))
    formats = [COLUMN_FORMATS.get(name) for name in frame.columns]
    for row, values in enumerate(frame.itertuples(index=False), start=2):
        for column, (value, number_format) in enumerate(zip(values, formats), start=1):
            write_cell(sheet, row, column, value, number_format)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    fit_columns(sheet)
    return sheet


def write_executive_summary(workbook: Workbook, results: Results,
                            generated_at: datetime.datetime) -> None:
    # Reference date and timestamp, then one block per summary section, in
    # the order of output_spec.md section 5.
    sheet = workbook.create_sheet(SUMMARY_SHEET)
    write_cell(sheet, 1, 1, "Reference date").font = HEADER_FONT
    write_cell(sheet, 1, 2, results.reference_date, DATE_FORMAT)
    write_cell(sheet, 2, 1, "Generated at").font = HEADER_FONT
    write_cell(sheet, 2, 2, generated_at, TIMESTAMP_FORMAT)
    row = 4
    for column, name in enumerate(("Item", "Currency", "Value"), start=1):
        style_header(write_cell(sheet, row, column, name))
    for section, items in results.summary.groupby("Section", sort=False):
        row += 2
        write_cell(sheet, row, 1, section).font = HEADER_FONT
        for item in items.itertuples(index=False):
            row += 1
            write_cell(sheet, row, 1, item.Item)
            write_cell(sheet, row, 2, item.Currency)
            write_cell(sheet, row, 3, item.Value, UNIT_FORMATS[item.Unit])
    fit_columns(sheet)


def parameters_table(config: Config) -> pd.DataFrame:
    # The config used for the run, one row per setting, for audit.
    return pd.DataFrame(parameter_rows(config.raw), columns=["Parameter", "Value"])


def parameter_rows(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, Mapping):
        return [row for key, item in value.items()
                for row in parameter_rows(item, f"{path}.{key}" if path else str(key))]
    return [(path, parameter_text(value))]


def parameter_text(value: Any) -> str:
    # YAML spelling, so that the sheet reads like config.yaml.
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple):
        return ", ".join(parameter_text(item) for item in value)
    return str(value)


def write_cell(sheet: Worksheet, row: int, column: int, value: Any,
               number_format: str | None = None) -> Cell:
    cell = sheet.cell(row=row, column=column, value=excel_value(value))
    if isinstance(cell.value, str):
        cell.data_type = "s"  # openpyxl would store "=1+1" from the data as a formula
    if number_format:
        cell.number_format = number_format
    return cell


def excel_value(value: Any) -> Any:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item"):
        return value.item()  # numpy scalar -> Python value
    return value


def header_name(column: str) -> str:
    return f"{column} %" if column in PERCENT_COLUMNS else column


def style_header(cell: Cell) -> None:
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL


def colour_severities(sheet: Worksheet, exceptions: pd.DataFrame) -> None:
    column = exceptions.columns.get_loc("Severity") + 1
    for row, severity in enumerate(exceptions["Severity"], start=2):
        sheet.cell(row=row, column=column).fill = SEVERITY_FILLS[severity]


def highlight_breaches(sheet: Worksheet, moves: pd.DataFrame) -> None:
    # Not computable (empty Breach) is not highlighted.
    for row, breach in enumerate(moves["Breach"].fillna(False), start=2):
        if breach:
            for cell in sheet[row]:
                cell.fill = BREACH_FILL


def fit_columns(sheet: Worksheet) -> None:
    for cells in sheet.columns:
        lengths = [DATE_WIDTH if isinstance(cell.value, datetime.datetime) else len(str(cell.value))
                   for cell in cells if cell.value is not None]
        width = min(max(lengths, default=0) + 2, MAX_COLUMN_WIDTH)
        sheet.column_dimensions[cells[0].column_letter].width = width
