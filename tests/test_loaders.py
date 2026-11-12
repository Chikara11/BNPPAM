import datetime
import importlib.util
import io
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from dq.config import COLUMN_TYPES, load_config
from dq.loaders import InputSchemaError, empty_rows, load_inputs, truly_missing
from dq.schema import PARSE_ISSUE_COLUMNS

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config(REPO_ROOT / "config.yaml")

VALID_ROWS = {
    "positions": {
        "ValuationDate": datetime.datetime(2026, 11, 11), "Portfolio": "CLO_EURO_01",
        "ISIN": "SYN000000001", "SecurityName": "Aurora CLO 2023-1 B",
        "AssetType": "CLO Debt", "Currency": "EUR", "Nominal": 5000000,
        "BookPrice": 99.2, "MarketPrice": 98.75, "Rating": "AA",
        "MaturityDate": datetime.datetime(2036, 4, 15),
    },
    "previous_prices": {
        "ISIN": "SYN000000001", "PreviousDate": datetime.datetime(2026, 11, 4),
        "PreviousPrice": 99.1, "PriceSource": "Pricing Agent",
    },
    "cashflows": {
        "Portfolio": "CLO_EURO_01", "ISIN": "SYN000000001",
        "CashflowDate": datetime.datetime(2026, 11, 25), "CashflowType": "Coupon",
        "Currency": "EUR", "Amount": 75000.0, "Status": "Confirmed",
    },
}


def write_sheet(path: Path, header: list, rows: list[list]) -> Path:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def write_inputs(tmp_path: Path, positions_rows: list[dict] | None = None,
                 header: list | None = None) -> dict:
    # One valid row per file; positions can be replaced by the test.
    paths = {}
    for file, valid in VALID_ROWS.items():
        rows = [valid] if file != "positions" or positions_rows is None else positions_rows
        columns = header if file == "positions" and header else list(valid)
        paths[file] = write_sheet(tmp_path / f"{file}.xlsx", columns,
                                  [[row.get(column) for column in columns] for row in rows])
    return paths


def load(paths: dict):
    return load_inputs(paths["positions"], paths["previous_prices"], paths["cashflows"], CONFIG)


def position(**changes) -> dict:
    return {**VALID_ROWS["positions"], **changes}


def test_column_types_match_the_generator():
    # The engine may not import scripts/, so the table is duplicated there.
    # This test makes any drift between the two copies fail.
    path = REPO_ROOT / "scripts" / "generate_clean_data.py"
    spec = importlib.util.spec_from_file_location("generate_clean_data", path)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert COLUMN_TYPES == generator.COLUMN_TYPES


def test_input_files_load_with_the_documented_dtypes():
    folder = REPO_ROOT / "data" / "inputs"
    data = load_inputs(folder / "positions.xlsx", folder / "previous_prices.xlsx",
                       folder / "cashflows.xlsx", CONFIG)
    assert (len(data.positions), len(data.previous_prices), len(data.cashflows)) == (52, 65, 210)
    assert data.parse_issues.empty
    positions = data.positions
    assert positions["ISIN"].dtype == object and positions["Rating"].dtype == object
    assert positions["Nominal"].dtype == "float64"
    assert positions["ValuationDate"].dtype == "datetime64[us]"
    assert data.cashflows["CashflowDate"].dtype == "datetime64[us]"
    assert positions["SourceRow"].dtype == "Int64"
    assert positions["SourceRow"].tolist() == list(range(2, 54))


def test_parse_issues_has_the_documented_columns(tmp_path):
    data = load(write_inputs(tmp_path))
    assert data.parse_issues.dtypes.astype(str).to_dict() == PARSE_ISSUE_COLUMNS


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_and_whitespace_cells_become_missing(tmp_path, value):
    data = load(write_inputs(tmp_path, [position(Rating=value)]))
    assert pd.isna(data.positions.loc[0, "Rating"])
    assert truly_missing(data, "positions", "Rating").tolist() == [True]
    assert data.parse_issues.empty


def test_leading_and_trailing_spaces_are_kept(tmp_path):
    data = load(write_inputs(tmp_path, [position(Currency=" EUR ")]))
    assert data.positions.loc[0, "Currency"] == " EUR "


@pytest.mark.parametrize("column, raw, raw_text", [
    ("Nominal", "5000000", "5000000"),
    ("MarketPrice", "98.5", "98.5"),
    ("Nominal", True, "True"),
    ("MaturityDate", 45000, "45000"),
    ("MaturityDate", "2036-04-15", "2036-04-15"),
    ("ISIN", 123456789012, "123456789012"),
    ("Portfolio", datetime.datetime(2026, 1, 1), "2026-01-01 00:00:00"),
])
def test_wrong_cell_type_is_a_parse_issue_not_a_missing_value(tmp_path, column, raw, raw_text):
    data = load(write_inputs(tmp_path, [position(**{column: raw})]))
    assert pd.isna(data.positions.loc[0, column])
    issue = data.parse_issues.iloc[0].to_dict()
    assert issue == {"File": "positions", "SourceRow": 2, "Column": column, "RawValue": raw_text}
    assert truly_missing(data, "positions", column).tolist() == [False]


def test_date_with_a_time_is_normalised_to_midnight(tmp_path):
    rows = [position(MaturityDate=datetime.datetime(2036, 4, 15, 13, 45))]
    data = load(write_inputs(tmp_path, rows))
    assert data.positions.loc[0, "MaturityDate"] == pd.Timestamp("2036-04-15")


def test_parse_issues_are_sorted_by_file_row_and_column(tmp_path):
    rows = [position(MarketPrice="x", Nominal="y"), position(ValuationDate="z")]
    data = load(write_inputs(tmp_path, rows))
    assert data.parse_issues[["SourceRow", "Column"]].values.tolist() == [
        [2, "Nominal"], [2, "MarketPrice"], [3, "ValuationDate"]]


def test_missing_required_columns_are_all_named(tmp_path):
    header = [c for c in VALID_ROWS["positions"] if c not in ("Nominal", "Rating")]
    with pytest.raises(InputSchemaError, match="positions.*Nominal, Rating"):
        load(write_inputs(tmp_path, [position()], header=header))


def test_column_names_are_case_sensitive(tmp_path):
    header = ["isin" if c == "ISIN" else c for c in VALID_ROWS["positions"]]
    with pytest.raises(InputSchemaError, match="ISIN"):
        load(write_inputs(tmp_path, [position()], header=header))


def test_duplicated_column_name_is_rejected(tmp_path):
    header = list(VALID_ROWS["positions"]) + ["ISIN"]
    with pytest.raises(InputSchemaError, match="duplicated.*ISIN"):
        load(write_inputs(tmp_path, [position()], header=header))


def test_extra_columns_are_kept(tmp_path):
    header = list(VALID_ROWS["positions"]) + ["Comment"]
    data = load(write_inputs(tmp_path, [position(Comment="note")], header=header))
    assert data.positions.loc[0, "Comment"] == "note"


def test_unreadable_file_names_the_file(tmp_path):
    paths = write_inputs(tmp_path)
    paths["cashflows"].write_text("not an excel file", encoding="utf-8")
    with pytest.raises(InputSchemaError, match="cashflows"):
        load(paths)


def test_missing_file_names_the_file(tmp_path):
    paths = write_inputs(tmp_path)
    paths["previous_prices"] = tmp_path / "nowhere.xlsx"
    with pytest.raises(InputSchemaError, match="previous_prices"):
        load(paths)


def test_header_only_file_loads_with_no_rows(tmp_path):
    data = load(write_inputs(tmp_path, []))
    assert data.positions.empty
    assert list(data.positions.columns) == list(VALID_ROWS["positions"]) + ["SourceRow"]


def test_file_like_objects_are_accepted(tmp_path):
    paths = write_inputs(tmp_path)
    buffers = {file: io.BytesIO(path.read_bytes()) for file, path in paths.items()}
    buffers["positions"].read()  # already consumed once, as after a Streamlit rerun
    assert len(load(buffers).positions) == 1


def test_empty_row_in_the_middle_keeps_the_excel_row_numbers(tmp_path):
    blank = {column: None for column in VALID_ROWS["positions"]}
    spaces = {column: "  " for column in VALID_ROWS["positions"]}
    data = load(write_inputs(tmp_path, [position(), blank, spaces, position()]))
    assert data.positions["SourceRow"].tolist() == [2, 3, 4, 5]
    assert empty_rows(data, "positions").tolist() == [False, True, True, False]


def test_row_with_only_an_unparseable_value_is_not_empty(tmp_path):
    only_bad = {column: None for column in VALID_ROWS["positions"]} | {"Nominal": "abc"}
    data = load(write_inputs(tmp_path, [only_bad]))
    assert empty_rows(data, "positions").tolist() == [False]
