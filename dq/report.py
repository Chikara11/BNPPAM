from __future__ import annotations

import datetime
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import Cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
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
# Titles and note of the Data Quality sheet, also used by the app's tab.
DIMENSION_BLOCK_TITLE = "Checks by dimension"
COLUMN_BLOCK_TITLE = "Completeness by column"
PLAUSIBILITY_NOTE = ("Plausibility = review alerts (e.g. large price moves), not necessarily "
                     "data errors.")
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
NOTE_FONT = Font(italic=True)
HEADER_FILL = PatternFill(fill_type="solid", start_color="D9D9D9")
# Tints also used by app.py, so the report and the app look the same.
SEVERITY_TINTS = {"Critical": "FFC7CE", "Warning": "FFD8A8", "Information": "BDD7EE"}
BREACH_TINT = "FFF2CC"  # light yellow, distinct from the severity tints
SEVERITY_FILLS = {severity: PatternFill(fill_type="solid", start_color=tint)
                  for severity, tint in SEVERITY_TINTS.items()}
BREACH_FILL = PatternFill(fill_type="solid", start_color=BREACH_TINT)
UNMATCHED_TINT = SEVERITY_TINTS["Warning"]  # an unmatched cashflow is a Warning exception
UNMATCHED_FILL = PatternFill(fill_type="solid", start_color=UNMATCHED_TINT)
WRAPPED = Alignment(wrap_text=True, vertical="top")
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
    moves, upcoming = results.price_movements, results.upcoming_cashflows
    highlight_rows(write_table(workbook, PRICE_MOVEMENTS_SHEET, moves),
                   moves["Breach"].fillna(False), BREACH_FILL)
    highlight_rows(write_table(workbook,
                               CASHFLOWS_SHEET.format(days=results.config.cashflow_horizon_days),
                               upcoming),
                   ~upcoming["MatchedPosition"].astype(bool), UNMATCHED_FILL)
    write_data_quality(workbook, results)
    write_table(workbook, PARAMETERS_SHEET, parameters_table(results.config))
    workbook.save(path_or_buffer)


def write_table(workbook: Workbook, title: str, frame: pd.DataFrame) -> Worksheet:
    sheet = workbook.create_sheet(title)
    write_frame(sheet, frame, 1)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    fit_columns(sheet)
    return sheet


def write_frame(sheet: Worksheet, frame: pd.DataFrame, first_row: int) -> str:
    # One header row, then the rows of the frame as they are: no calculation.
    # Returns the range written, e.g. "A5:H27".
    for column, name in enumerate(frame.columns, start=1):
        style_header(write_cell(sheet, first_row, column, header_name(name)))
    formats = [COLUMN_FORMATS.get(name) for name in frame.columns]
    for row, values in enumerate(frame.itertuples(index=False), start=first_row + 1):
        for column, (value, number_format) in enumerate(zip(values, formats), start=1):
            write_cell(sheet, row, column, value, number_format)
    last_row = first_row + len(frame)
    return f"A{first_row}:{get_column_letter(len(frame.columns))}{last_row}"


def write_data_quality(workbook: Workbook, results: Results) -> None:
    # Two blocks on one sheet. Excel allows one autofilter per sheet: it goes
    # on the column table; no frozen header, since the sheet has two.
    sheet = workbook.create_sheet(DATA_QUALITY_SHEET)
    write_cell(sheet, 1, 1, DIMENSION_BLOCK_TITLE).font = HEADER_FONT
    block = results.dimension_quality
    write_frame(sheet, block, 2)
    controls = block.columns.get_loc("Controls") + 1
    for row in range(3, 3 + len(block)):  # long lists of control IDs wrap in the cell
        sheet.cell(row=row, column=controls).alignment = WRAPPED
    note_row = 2 + len(results.dimension_quality) + 1  # header on row 2, then the rows
    write_cell(sheet, note_row, 1, PLAUSIBILITY_NOTE).font = NOTE_FONT
    write_cell(sheet, note_row + 2, 1, COLUMN_BLOCK_TITLE).font = HEADER_FONT
    sheet.auto_filter.ref = write_frame(sheet, results.data_quality, note_row + 3)
    fit_columns(sheet, skip_rows={1, note_row, note_row + 2})


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


def highlight_rows(sheet: Worksheet, selected: pd.Series, fill: PatternFill) -> None:
    # selected is aligned on the table rows (header on row 1).
    for row, chosen in enumerate(selected, start=2):
        if chosen:
            for cell in sheet[row]:
                cell.fill = fill


def fit_columns(sheet: Worksheet, skip_rows: set[int] = frozenset()) -> None:
    # skip_rows: titles and notes that may run over the next columns.
    for cells in sheet.columns:
        lengths = [DATE_WIDTH if isinstance(cell.value, datetime.datetime) else len(str(cell.value))
                   for cell in cells if cell.value is not None and cell.row not in skip_rows]
        width = min(max(lengths, default=0) + 2, MAX_COLUMN_WIDTH)
        sheet.column_dimensions[cells[0].column_letter].width = width
