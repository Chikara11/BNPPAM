from pathlib import Path

import pandas as pd

from dq.config import COLUMN_TYPES, load_config
from dq.context import build_context
from dq.engine import run
from dq.loaders import InputData, empty_parse_issues

# Small hand-built inputs for the unit tests, with the loader's columns and dtypes.

CONFIG = load_config(Path(__file__).resolve().parent.parent / "config.yaml")

POSITION = {
    "ValuationDate": "2026-11-11", "Portfolio": "CLO_EURO_01", "ISIN": "SYN000000001",
    "SecurityName": "Aurora CLO 2023-1 B", "AssetType": "CLO Debt", "Currency": "EUR",
    "Nominal": 5000000.0, "BookPrice": 99.2, "MarketPrice": 98.75, "Rating": "AA",
    "MaturityDate": "2036-04-15",
}
PREVIOUS_PRICE = {
    "ISIN": "SYN000000001", "PreviousDate": "2026-11-04", "PreviousPrice": 99.1,
    "PriceSource": "Pricing Agent",
}
CASHFLOW = {
    "Portfolio": "CLO_EURO_01", "ISIN": "SYN000000001", "CashflowDate": "2026-11-25",
    "CashflowType": "Coupon", "Currency": "EUR", "Amount": 75000.0, "Status": "Confirmed",
}


def frame(file: str, rows: list[dict]) -> pd.DataFrame:
    # Same columns and dtypes as the loader, SourceRow from Excel row 2.
    result = pd.DataFrame(rows, columns=list(COLUMN_TYPES[file]))
    for column, kind in COLUMN_TYPES[file].items():
        if kind == "date":
            result[column] = pd.to_datetime(result[column]).astype("datetime64[us]")
        else:
            result[column] = result[column].astype("float64" if kind == "number" else object)
    result["SourceRow"] = pd.array(range(2, len(result) + 2), dtype="Int64")
    return result


def make_data(positions: list[dict] | None = None, previous_prices: list[dict] | None = None,
              cashflows: list[dict] | None = None,
              parse_issues: pd.DataFrame | None = None) -> InputData:
    return InputData(
        positions=frame("positions", [POSITION] if positions is None else positions),
        previous_prices=frame("previous_prices",
                              [PREVIOUS_PRICE] if previous_prices is None else previous_prices),
        cashflows=frame("cashflows", [CASHFLOW] if cashflows is None else cashflows),
        parse_issues=empty_parse_issues() if parse_issues is None else parse_issues,
    )


def position(**changes) -> dict:
    return {**POSITION, **changes}


def check(control, data: InputData, config=CONFIG) -> pd.DataFrame:
    return control(data, build_context(data, config))


def parse_issue(file: str, source_row: int, column: str, raw: str) -> pd.DataFrame:
    return pd.DataFrame({"File": [file], "SourceRow": pd.array([source_row], dtype="Int64"),
                         "Column": [column], "RawValue": [raw]})


EMPTY_POSITION = {column: None for column in POSITION}


def price(**changes) -> dict:
    return {**PREVIOUS_PRICE, **changes}


def cashflow(**changes) -> dict:
    return {**CASHFLOW, **changes}


def raised(data: InputData, config=CONFIG) -> list[tuple]:
    # Every exception of a full engine run, as (ControlID, Source, Field).
    exceptions = run(data, config).exceptions
    return sorted(zip(exceptions["ControlID"], exceptions["Source"], exceptions["Field"]))
