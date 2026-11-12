import dataclasses
import datetime
import io

import openpyxl
import pytest

from dq.config import with_overrides
from dq.engine import run
from dq.report import write_report
from dq.schema import (DATA_QUALITY_COLUMNS, EXCEPTION_COLUMNS, PRICE_MOVEMENT_COLUMNS,
                       UPCOMING_CASHFLOW_COLUMNS)
from helpers import CONFIG

GENERATED_AT = datetime.datetime(2026, 11, 12, 9, 30, 0)
SHEETS = ["Executive Summary", "Exceptions", "Price Movements", "Cashflows 30D",
          "Data Quality", "Parameters"]
TABLES = [("Exceptions", "exceptions", EXCEPTION_COLUMNS),
          ("Price Movements", "price_movements", PRICE_MOVEMENT_COLUMNS),
          ("Cashflows 30D", "upcoming_cashflows", UPCOMING_CASHFLOW_COLUMNS),
          ("Data Quality", "data_quality", DATA_QUALITY_COLUMNS)]
PERCENT_COLUMNS = ("PriceChangePct", "ThresholdPct", "CompletenessPct")


def written(results) -> openpyxl.Workbook:
    buffer = io.BytesIO()
    write_report(results, buffer, generated_at=GENERATED_AT)
    buffer.seek(0)
    return openpyxl.load_workbook(buffer)


@pytest.fixture(scope="module")
def workbook(input_results):
    return written(input_results)


def column_values(sheet, name: str) -> list:
    header = [cell.value for cell in sheet[1]]
    column = header.index(name) + 1
    return [sheet.cell(row=row, column=column).value for row in range(2, sheet.max_row + 1)]


def cell_of(sheet, name: str, row: int):
    header = [cell.value for cell in sheet[1]]
    return sheet.cell(row=row, column=header.index(name) + 1)


def test_report_has_the_six_sheets_in_order(workbook):
    assert workbook.sheetnames == SHEETS


def test_report_cashflow_sheet_is_named_after_the_horizon(input_data):
    results = run(input_data, with_overrides(CONFIG, {"cashflow_horizon_days": 60}))
    assert written(results).sheetnames[3] == "Cashflows 60D"


@pytest.mark.parametrize("sheet_name, output, columns", TABLES)
def test_report_table_has_the_schema_headers_and_every_row(workbook, input_results,
                                                           sheet_name, output, columns):
    sheet = workbook[sheet_name]
    expected = [f"{name} %" if name in PERCENT_COLUMNS else name for name in columns]
    assert [cell.value for cell in sheet[1]] == expected
    assert sheet.max_row - 1 == len(getattr(input_results, output))


def test_report_values_are_copied_from_the_results(workbook, input_results):
    sheet = workbook["Exceptions"]
    exceptions = input_results.exceptions
    assert column_values(sheet, "ExceptionID") == exceptions["ExceptionID"].tolist()
    assert column_values(sheet, "Severity") == exceptions["Severity"].astype(str).tolist()
    moves = workbook["Price Movements"]
    assert column_values(moves, "SourceRow") == input_results.price_movements["SourceRow"].tolist()


@pytest.mark.parametrize("sheet_name, column, number_format", [
    ("Price Movements", "Nominal", "#,##0.00"),
    ("Price Movements", "MarketValue", "#,##0.00"),
    ("Price Movements", "MarketPrice", "0.00"),
    ("Price Movements", "PriceChangePct %", "0.00"),
    ("Price Movements", "PreviousDate", "YYYY-MM-DD"),
    ("Cashflows 30D", "CashflowDate", "YYYY-MM-DD"),
    ("Cashflows 30D", "Amount", "#,##0.00"),
    ("Data Quality", "CompletenessPct %", "0.00"),
])
def test_report_columns_have_the_documented_number_format(workbook, sheet_name, column,
                                                          number_format):
    assert cell_of(workbook[sheet_name], column, 2).number_format == number_format


def test_report_severity_cell_only_is_coloured(workbook, input_results):
    sheet = workbook["Exceptions"]
    tints = {"Critical": "FFC7CE", "Warning": "FFD8A8", "Information": "BDD7EE"}
    for row, severity in enumerate(input_results.exceptions["Severity"], start=2):
        assert cell_of(sheet, "Severity", row).fill.fgColor.rgb.endswith(tints[severity])
        assert cell_of(sheet, "ControlID", row).fill.fill_type is None


def test_report_breach_rows_are_highlighted(workbook, input_results):
    sheet = workbook["Price Movements"]
    breaches = input_results.price_movements["Breach"].fillna(False).tolist()
    filled = [cell_of(sheet, "ISIN", row).fill.fill_type == "solid"
              for row in range(2, sheet.max_row + 1)]
    assert filled == breaches
    assert sum(filled) == 3


@pytest.mark.parametrize("sheet_name", SHEETS[1:])
def test_report_table_header_is_frozen_and_filtered(workbook, sheet_name):
    sheet = workbook[sheet_name]
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref == sheet.dimensions


def test_report_formula_like_text_is_stored_as_plain_text(input_results):
    exceptions = input_results.exceptions.copy()
    exceptions.loc[0, "ObservedValue"] = "=1+1"
    results = dataclasses.replace(input_results, exceptions=exceptions)
    cell = cell_of(written(results)["Exceptions"], "ObservedValue", 2)
    assert (cell.value, cell.data_type) == ("=1+1", "s")


def test_report_executive_summary_shows_the_run_and_every_summary_row(workbook, input_results):
    sheet = workbook["Executive Summary"]
    assert [sheet["A1"].value, sheet["B1"].value] == ["Reference date",
                                                      datetime.datetime(2026, 11, 11)]
    assert [sheet["A2"].value, sheet["B2"].value] == ["Generated at", GENERATED_AT]
    rows = [(row[0].value, row[1].value, row[2].value) for row in sheet.iter_rows(min_row=5)]
    for item in input_results.summary.itertuples(index=False):
        currency = item.Currency if isinstance(item.Currency, str) else None  # NaN -> empty cell
        assert (item.Item, currency, item.Value) in rows
    sections = [name for name, currency, value in rows if name and value is None]
    assert sections == input_results.summary["Section"].unique().tolist()
    ignored = "Position rows not at the reference date (ignored)"
    assert (ignored, None, 0) in rows


def test_report_executive_summary_formats_amounts_and_counts(workbook):
    formats = {row[0].value: row[2].number_format
               for row in workbook["Executive Summary"].iter_rows(min_row=5) if row[2].value}
    assert formats["Market value"] == "#,##0.00"
    assert formats["Positions"] == "0"


def test_report_parameters_list_the_config_of_the_run(workbook):
    sheet = workbook["Parameters"]
    parameters = dict(zip(column_values(sheet, "Parameter"), column_values(sheet, "Value")))
    assert parameters["reference_date"] == "null"
    assert parameters["price_move_thresholds_pct.CLO Debt"] == "5.0"
    assert parameters["rating_required_for"] == "CLO Debt, ABS"
    assert parameters["keys.position_unique"] == "ValuationDate, Portfolio, ISIN"
    for control_id, settings in CONFIG.controls.items():
        assert parameters[f"controls.{control_id}.severity"] == settings.severity
        assert parameters[f"controls.{control_id}.enabled"] == "true"


def test_report_parameters_show_the_overrides_used(input_data):
    config = with_overrides(CONFIG, {"price_move_thresholds_pct": {"ABS": 4.0}})
    sheet = written(run(input_data, config))["Parameters"]
    parameters = dict(zip(column_values(sheet, "Parameter"), column_values(sheet, "Value")))
    assert parameters["price_move_thresholds_pct.ABS"] == "4.0"


def test_report_clean_data_has_an_empty_exceptions_sheet(clean_results):
    sheet = written(clean_results)["Exceptions"]
    assert sheet.max_row == 1
    assert [cell.value for cell in sheet[1]] == list(EXCEPTION_COLUMNS)


def test_report_is_written_to_a_file_path(input_results, tmp_path):
    path = tmp_path / "report.xlsx"
    write_report(input_results, path, generated_at=GENERATED_AT)
    assert openpyxl.load_workbook(path).sheetnames == SHEETS
