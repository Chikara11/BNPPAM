from pathlib import Path

import pandas as pd
import pytest

from dq.config import COLUMN_TYPES, load_config, with_overrides
from dq.context import build_context
from dq.controls import positions as position_controls
from dq.loaders import InputData, empty_parse_issues

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


def test_missing_isin_fires():
    exceptions = check(position_controls.check_missing_isin, make_data([position(ISIN=None)]))
    assert exceptions[["ControlID", "Field", "SourceRow"]].values.tolist() == [
        ["MISSING_ISIN", "ISIN", 2]]


def test_missing_isin_does_not_fire_on_a_filled_isin():
    assert check(position_controls.check_missing_isin, make_data()).empty


def test_duplicate_position_fires_once_per_key():
    data = make_data([position(), position(), position(), position(ISIN="SYN000000002")])
    exceptions = check(position_controls.check_duplicate_position, data)
    assert len(exceptions) == 1
    exception = exceptions.iloc[0]
    assert exception["SourceRow"] == 2
    assert exception["Field"] == "ValuationDate+Portfolio+ISIN"
    assert exception["ObservedValue"] == "2026-11-11+CLO_EURO_01+SYN000000001"
    assert "Excel rows 2, 3, 4" in exception["Comment"]
    assert "counted twice" in exception["Comment"]


def test_same_isin_in_two_portfolios_is_not_a_duplicate():
    data = make_data([position(), position(Portfolio="CLO_GLOBAL_02")])
    assert check(position_controls.check_duplicate_position, data).empty


def test_rows_without_isin_are_not_compared_as_duplicates():
    # Rule 1: only MISSING_ISIN runs on a row without ISIN.
    data = make_data([position(ISIN=None), position(ISIN=None)])
    assert check(position_controls.check_duplicate_position, data).empty


def test_missing_market_price_fires():
    data = make_data([position(MarketPrice=None)])
    exceptions = check(position_controls.check_missing_market_price, data)
    assert exceptions[["ControlID", "Field"]].values.tolist() == [
        ["MISSING_MARKET_PRICE", "MarketPrice"]]


@pytest.mark.parametrize("market_price", [0.0, -1.5, 98.75])
def test_present_market_price_is_not_missing(market_price):
    # Rule 3: a value <= 0 is INVALID_PRICE, not missing.
    data = make_data([position(MarketPrice=market_price)])
    assert check(position_controls.check_missing_market_price, data).empty


def test_unparseable_market_price_is_not_missing():
    # Rule 10: reported only by INVALID_VALUE.
    data = make_data([position(MarketPrice=None)],
                     parse_issues=parse_issue("positions", 2, "MarketPrice", "n/a"))
    assert check(position_controls.check_missing_market_price, data).empty


def test_empty_row_is_not_a_missing_market_price():
    # Rule 11: an empty row is reported once, by INVALID_VALUE.
    data = make_data([position(), EMPTY_POSITION])
    assert check(position_controls.check_missing_market_price, data).empty


@pytest.mark.parametrize("nominal, observed", [(0.0, "0"), (-2000000.0, "-2000000"),
                                               (-0.5, "-0.5")])
def test_invalid_nominal_fires_on_zero_or_negative(nominal, observed):
    exceptions = check(position_controls.check_invalid_nominal,
                       make_data([position(Nominal=nominal)]))
    assert exceptions[["ControlID", "ObservedValue"]].values.tolist() == [
        ["INVALID_NOMINAL", observed]]


@pytest.mark.parametrize("nominal", [None, 0.01, 5000000.0])
def test_invalid_nominal_does_not_fire_on_empty_or_positive(nominal):
    # Rule 3: an empty Nominal is MISSING_VALUE.
    data = make_data([position(Nominal=nominal)])
    assert check(position_controls.check_invalid_nominal, data).empty


@pytest.mark.parametrize("asset_type", ["CLO Debt", "ABS"])
def test_missing_rating_fires_where_a_rating_is_expected(asset_type):
    data = make_data([position(AssetType=asset_type, Rating=None)])
    exceptions = check(position_controls.check_missing_rating, data)
    assert exceptions[["ControlID", "Severity", "Field"]].values.tolist() == [
        ["MISSING_RATING", "Warning", "Rating"]]


@pytest.mark.parametrize("asset_type", ["CLO Equity", None, "Loan"])
def test_missing_rating_does_not_fire_without_a_rated_asset_type(asset_type):
    # CLO Equity is not rated; an empty or unknown AssetType is another control's job.
    data = make_data([position(AssetType=asset_type, Rating=None)])
    assert check(position_controls.check_missing_rating, data).empty


def test_missing_rating_follows_the_config():
    config = with_overrides(CONFIG, {"rating_required_for": ["ABS"]})
    data = make_data([position(AssetType="CLO Debt", Rating=None)])
    assert check(position_controls.check_missing_rating, data, config).empty
