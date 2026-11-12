import dataclasses
from pathlib import Path

import pandas as pd
import pytest

from dq.config import config_from_dict, load_config, thaw, with_overrides
from dq.context import build_context, positions_at_reference_date
from dq.controls import CONTROL_REGISTRY
from dq.engine import RegistryError, assign_exception_ids, check_registry, run, sort_exceptions
from dq.exceptions import make_exceptions
from dq.loaders import InputSchemaError
from dq.schema import (DATA_QUALITY_COLUMNS, EXCEPTION_COLUMNS, PRICE_MOVEMENT_COLUMNS,
                       SUMMARY_COLUMNS, UPCOMING_CASHFLOW_COLUMNS, conform, empty_exceptions,
                       empty_frame)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config(REPO_ROOT / "config.yaml")
A01_SOURCE_ROW = 21  # the position whose ISIN was emptied (manifest A01)


@pytest.fixture(scope="module")
def data(input_data):
    return input_data


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


def exceptions_frame(rows: list[dict]) -> pd.DataFrame:
    filler = {"ExceptionID": None, "ControlID": "X", "AnomalyType": "x", "Source": "positions",
              "ISIN": "SYN000000001", "SecurityName": None, "ObservedValue": None,
              "Comment": "x", "Field": "F"}
    return conform(pd.DataFrame([{**filler, **row} for row in rows]), EXCEPTION_COLUMNS)


def test_real_config_needs_every_control_registered(real_config, data):
    # TEMPORARY: removed when the last control is registered.
    missing = [control for control in real_config.controls if control not in CONTROL_REGISTRY]
    assert len(missing) == 13
    with pytest.raises(RegistryError) as error:
        check_registry(real_config)
    assert all(control in str(error.value) for control in missing)
    with pytest.raises(RegistryError):
        run(data, real_config)


def test_registered_control_missing_from_the_config_is_an_error(real_config):
    raw = thaw(real_config.raw)
    raw["controls"] = {"DUPLICATE_POSITION": raw["controls"]["DUPLICATE_POSITION"]}
    with pytest.raises(RegistryError, match="not registered: DUPLICATE_POSITION.*"
                                            "not in config.yaml: MISSING_ISIN"):
        check_registry(config_from_dict(raw))


def test_run_on_inputs(input_results):
    exceptions = input_results.exceptions
    assert exceptions["ExceptionID"].tolist() == ["EXC-0001"]
    assert exceptions.loc[0, "ControlID"] == "MISSING_ISIN"
    assert exceptions.loc[0, "SourceRow"] == A01_SOURCE_ROW
    assert input_results.reference_date == pd.Timestamp("2026-11-11")


def test_outputs_have_the_documented_columns(input_results):
    assert input_results.exceptions.dtypes.equals(empty_exceptions().dtypes)
    for output, columns in [("price_movements", PRICE_MOVEMENT_COLUMNS),
                            ("upcoming_cashflows", UPCOMING_CASHFLOW_COLUMNS),
                            ("data_quality", DATA_QUALITY_COLUMNS),
                            ("summary", SUMMARY_COLUMNS)]:
        assert getattr(input_results, output).dtypes.equals(empty_frame(columns).dtypes)


def test_disabled_control_does_not_run(data, test_config):
    config = with_overrides(test_config, {"controls": {"MISSING_ISIN": {"enabled": False}}})
    exceptions = run(data, config).exceptions
    assert exceptions.empty
    assert exceptions.dtypes.equals(empty_exceptions().dtypes)


def test_same_inputs_give_the_same_exceptions(data, test_config, input_results):
    pd.testing.assert_frame_equal(run(data, test_config).exceptions, input_results.exceptions)


def test_empty_row_does_not_raise_missing_isin(data, test_config):
    # Rule 11: reported once by INVALID_VALUE (later), never by MISSING_ISIN.
    empty_row = pd.DataFrame({"SourceRow": pd.array([99], dtype="Int64")})
    positions = pd.concat([data.positions, empty_row], ignore_index=True)
    exceptions = run(dataclasses.replace(data, positions=positions), test_config).exceptions
    assert exceptions["SourceRow"].tolist() == [A01_SOURCE_ROW]


def test_unparseable_isin_does_not_raise_missing_isin(data, test_config):
    # Rule 10: a non-text ISIN is a parse issue, reported only by INVALID_VALUE.
    positions = data.positions.copy()
    positions.loc[positions["SourceRow"] == 2, "ISIN"] = None
    issue = pd.DataFrame({"File": ["positions"], "SourceRow": pd.array([2], dtype="Int64"),
                          "Column": ["ISIN"], "RawValue": ["123456789012"]})
    changed = dataclasses.replace(data, positions=positions,
                                  parse_issues=pd.concat([data.parse_issues, issue]))
    assert run(changed, test_config).exceptions["SourceRow"].tolist() == [A01_SOURCE_ROW]


def test_positions_outside_the_reference_date_are_not_controlled(data, test_config):
    positions = data.positions.copy()
    positions.loc[positions["SourceRow"] == A01_SOURCE_ROW, "ValuationDate"] = pd.Timestamp(
        "2026-11-04")
    assert run(dataclasses.replace(data, positions=positions), test_config).exceptions.empty


def test_exceptions_are_sorted_by_severity_then_keys():
    unsorted = exceptions_frame([
        {"Severity": "Information", "Portfolio": "A", "SourceRow": 2},
        {"Severity": "Critical", "Portfolio": None, "SourceRow": 2},
        {"Severity": "Critical", "Portfolio": "B", "SourceRow": 5, "Field": "MarketPrice"},
        {"Severity": "Critical", "Portfolio": "B", "SourceRow": 5, "Field": "BookPrice"},
        {"Severity": "Warning", "Portfolio": "A", "SourceRow": 3},
    ])
    ordered = sort_exceptions(unsorted)
    assert ordered["Severity"].tolist() == ["Critical"] * 3 + ["Warning", "Information"]
    assert ordered["Field"].tolist()[:2] == ["BookPrice", "MarketPrice"]
    assert pd.isna(ordered.loc[2, "Portfolio"])  # empty Portfolio sorts last


def test_exception_ids_follow_the_sorted_order():
    exceptions = exceptions_frame([{"Severity": "Warning", "Portfolio": "A", "SourceRow": row}
                                   for row in (2, 3, 4)])
    assert assign_exception_ids(exceptions)["ExceptionID"].tolist() == [
        "EXC-0001", "EXC-0002", "EXC-0003"]
