import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from dq.config import load_config, with_overrides
from dq.context import build_context, positions_at_reference_date
from dq.exceptions import make_exceptions
from dq.loaders import InputSchemaError, load_inputs
from dq.schema import EXCEPTION_COLUMNS, conform, empty_exceptions

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config(REPO_ROOT / "config.yaml")
INPUTS = REPO_ROOT / "data" / "inputs"
A01_SOURCE_ROW = 21  # the position whose ISIN was emptied (manifest A01)


@pytest.fixture(scope="module")
def data():
    return load_inputs(INPUTS / "positions.xlsx", INPUTS / "previous_prices.xlsx",
                       INPUTS / "cashflows.xlsx", CONFIG)


def row_at(data, source_row: int) -> pd.DataFrame:
    return data.positions[data.positions["SourceRow"] == source_row]


def test_reference_date_is_the_latest_valuation_date(data):
    ctx = build_context(data, CONFIG)
    assert ctx.reference_date == pd.Timestamp("2026-11-11")
    assert ctx.horizon_end == pd.Timestamp("2026-12-11")
    assert ctx.ignored_position_rows == 0
    assert len(positions_at_reference_date(data, ctx).positions) == 52


def test_reference_date_from_config_overrides_the_data(data):
    ctx = build_context(data, with_overrides(CONFIG, {"reference_date": "2026-11-12"}))
    assert ctx.reference_date == pd.Timestamp("2026-11-12")
    # Q2: no position at that date is not an error, every row is ignored
    assert ctx.ignored_position_rows == 52
    assert positions_at_reference_date(data, ctx).positions.empty


def test_horizon_follows_the_config(data):
    ctx = build_context(data, with_overrides(CONFIG, {"cashflow_horizon_days": 60}))
    assert ctx.horizon_end == pd.Timestamp("2027-01-10")


def test_no_valid_valuation_date_and_no_config_date_stops(data):
    # Q1: there is no reference date to work with
    positions = data.positions.assign(ValuationDate=pd.NaT)
    with pytest.raises(InputSchemaError, match="no valid ValuationDate"):
        build_context(dataclasses.replace(data, positions=positions), CONFIG)


def test_empty_valuation_date_is_kept_not_ignored(data):
    positions = data.positions.copy()
    positions.loc[positions["SourceRow"] == 2, "ValuationDate"] = pd.NaT
    positions.loc[positions["SourceRow"] == 3, "ValuationDate"] = pd.Timestamp("2026-11-04")
    changed = dataclasses.replace(data, positions=positions)
    ctx = build_context(changed, CONFIG)
    kept = positions_at_reference_date(changed, ctx).positions["SourceRow"]
    assert ctx.ignored_position_rows == 1
    assert 2 in kept.tolist() and 3 not in kept.tolist()


def test_exception_is_built_from_the_row_and_the_config(data):
    rows = row_at(data, A01_SOURCE_ROW)
    exceptions = make_exceptions(rows, "MISSING_ISIN", build_context(data, CONFIG),
                                 "positions", "ISIN", comment="ISIN is empty.")
    assert len(exceptions) == 1
    exception = exceptions.iloc[0]
    assert exception["SourceRow"] == A01_SOURCE_ROW
    assert exception["Portfolio"] == "CLO_EURO_01"
    assert exception["ISIN"] is None
    assert exception["Severity"] == "Critical"
    assert exception["AnomalyType"] == "Missing ISIN"
    assert (exception["Source"], exception["Field"]) == ("positions", "ISIN")
    assert exceptions.dtypes.equals(empty_exceptions().dtypes)


def test_severity_comes_from_the_config(data):
    config = with_overrides(CONFIG, {"controls": {"MISSING_ISIN": {"severity": "Warning"}}})
    exceptions = make_exceptions(row_at(data, A01_SOURCE_ROW), "MISSING_ISIN",
                                 build_context(data, config), "positions", "ISIN", comment="x")
    assert exceptions["Severity"].tolist() == ["Warning"]


def test_observed_value_is_written_as_text(data):
    rows = data.positions.head(1)
    ctx = build_context(data, CONFIG)
    nominal = make_exceptions(rows, "INVALID_NOMINAL", ctx, "positions", "Nominal",
                              observed=rows["Nominal"], comment="x")
    maturity = make_exceptions(rows, "INVALID_VALUE", ctx, "positions", "MaturityDate",
                               observed=rows["MaturityDate"], comment="x")
    assert nominal["ObservedValue"].iloc[0] == str(int(rows["Nominal"].iloc[0]))
    assert maturity["ObservedValue"].iloc[0] == rows["MaturityDate"].iloc[0].strftime("%Y-%m-%d")


def test_no_row_gives_a_typed_empty_frame(data):
    exceptions = make_exceptions(data.positions.iloc[0:0], "MISSING_ISIN",
                                 build_context(data, CONFIG), "positions", "ISIN", comment="x")
    assert exceptions.empty
    assert list(exceptions.columns) == list(EXCEPTION_COLUMNS)
    assert exceptions.dtypes.equals(empty_exceptions().dtypes)


def test_unknown_severity_is_rejected(data):
    exceptions = make_exceptions(row_at(data, A01_SOURCE_ROW), "MISSING_ISIN",
                                 build_context(data, CONFIG), "positions", "ISIN", comment="x")
    broken = exceptions.astype({"Severity": object}).assign(Severity="High")
    with pytest.raises(AssertionError, match="High"):
        conform(broken, EXCEPTION_COLUMNS)
