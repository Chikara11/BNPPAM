import dataclasses

import pandas as pd
import pytest

from dq.config import config_from_dict, thaw, with_overrides
from dq.context import build_context, positions_at_reference_date
from dq.engine import RegistryError, assign_exception_ids, check_registry, run, sort_exceptions
from dq.exceptions import make_exceptions
from dq.loaders import InputSchemaError
from dq.schema import EXCEPTION_COLUMNS, conform, empty_exceptions
from helpers import CONFIG, parse_issue

A01_SOURCE_ROW = 21  # the position whose ISIN was emptied (manifest A01)


def row_at(input_data, source_row: int) -> pd.DataFrame:
    return input_data.positions[input_data.positions["SourceRow"] == source_row]


def test_reference_date_is_the_latest_valuation_date(input_data):
    ctx = build_context(input_data, CONFIG)
    assert ctx.reference_date == pd.Timestamp("2026-11-11")
    assert ctx.horizon_end == pd.Timestamp("2026-12-11")
    assert ctx.ignored_position_rows == 0
    assert len(positions_at_reference_date(input_data, ctx).positions) == 52


def test_reference_date_from_config_overrides_the_data(input_data):
    ctx = build_context(input_data, with_overrides(CONFIG, {"reference_date": "2026-11-12"}))
    assert ctx.reference_date == pd.Timestamp("2026-11-12")
    # Q2: no position at that date is not an error, every row is ignored
    assert ctx.ignored_position_rows == 52
    assert positions_at_reference_date(input_data, ctx).positions.empty


def test_horizon_follows_the_config(input_data):
    ctx = build_context(input_data, with_overrides(CONFIG, {"cashflow_horizon_days": 60}))
    assert ctx.horizon_end == pd.Timestamp("2027-01-10")


def test_no_valid_valuation_date_and_no_config_date_stops(input_data):
    # Q1: there is no reference date to work with
    positions = input_data.positions.assign(ValuationDate=pd.NaT)
    with pytest.raises(InputSchemaError, match="no valid ValuationDate"):
        build_context(dataclasses.replace(input_data, positions=positions), CONFIG)


def test_empty_valuation_date_is_kept_not_ignored(input_data):
    positions = input_data.positions.copy()
    positions.loc[positions["SourceRow"] == 2, "ValuationDate"] = pd.NaT
    positions.loc[positions["SourceRow"] == 3, "ValuationDate"] = pd.Timestamp("2026-11-04")
    changed = dataclasses.replace(input_data, positions=positions)
    ctx = build_context(changed, CONFIG)
    kept = positions_at_reference_date(changed, ctx).positions["SourceRow"]
    assert ctx.ignored_position_rows == 1
    assert 2 in kept.tolist() and 3 not in kept.tolist()


def test_exception_is_built_from_the_row_and_the_config(input_data):
    rows = row_at(input_data, A01_SOURCE_ROW)
    exceptions = make_exceptions(rows, "MISSING_ISIN", build_context(input_data, CONFIG),
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


def test_severity_comes_from_the_config(input_data):
    config = with_overrides(CONFIG, {"controls": {"MISSING_ISIN": {"severity": "Warning"}}})
    exceptions = make_exceptions(row_at(input_data, A01_SOURCE_ROW), "MISSING_ISIN",
                                 build_context(input_data, config), "positions", "ISIN",
                                 comment="x")
    assert exceptions["Severity"].tolist() == ["Warning"]


def test_observed_value_is_written_as_text(input_data):
    rows = input_data.positions.head(1)
    ctx = build_context(input_data, CONFIG)
    nominal = make_exceptions(rows, "INVALID_NOMINAL", ctx, "positions", "Nominal",
                              observed=rows["Nominal"], comment="x")
    maturity = make_exceptions(rows, "INVALID_VALUE", ctx, "positions", "MaturityDate",
                               observed=rows["MaturityDate"], comment="x")
    assert nominal["ObservedValue"].iloc[0] == str(int(rows["Nominal"].iloc[0]))
    assert maturity["ObservedValue"].iloc[0] == rows["MaturityDate"].iloc[0].strftime("%Y-%m-%d")


def test_no_row_gives_a_typed_empty_frame(input_data):
    exceptions = make_exceptions(input_data.positions.iloc[0:0], "MISSING_ISIN",
                                 build_context(input_data, CONFIG), "positions", "ISIN",
                                 comment="x")
    assert exceptions.empty
    assert list(exceptions.columns) == list(EXCEPTION_COLUMNS)
    assert exceptions.dtypes.equals(empty_exceptions().dtypes)


def test_unknown_severity_is_rejected(input_data):
    exceptions = make_exceptions(row_at(input_data, A01_SOURCE_ROW), "MISSING_ISIN",
                                 build_context(input_data, CONFIG), "positions", "ISIN",
                                 comment="x")
    broken = exceptions.astype({"Severity": object}).assign(Severity="High")
    with pytest.raises(AssertionError, match="High"):
        conform(broken, EXCEPTION_COLUMNS)


def exceptions_frame(rows: list[dict]) -> pd.DataFrame:
    filler = {"ExceptionID": None, "ControlID": "X", "AnomalyType": "x", "Source": "positions",
              "ISIN": "SYN000000001", "SecurityName": None, "ObservedValue": None,
              "Comment": "x", "Field": "F"}
    return conform(pd.DataFrame([{**filler, **row} for row in rows]), EXCEPTION_COLUMNS)


@pytest.fixture(scope="module")
def isin_only_config():
    # Every other control disabled, so these engine tests only see MISSING_ISIN.
    return with_overrides(CONFIG, {"controls": {
        control_id: {"enabled": control_id == "MISSING_ISIN"}
        for control_id in CONFIG.controls}})


def test_real_config_matches_the_registry():
    check_registry(CONFIG)


def test_registered_control_missing_from_the_config_is_an_error():
    raw = thaw(CONFIG.raw)
    settings = raw["controls"]["MISSING_ISIN"]
    raw["controls"] = {"MISSING_ISIN": settings, "NOT_A_CONTROL": settings}
    with pytest.raises(RegistryError, match="not registered: NOT_A_CONTROL.*"
                                            "not in config.yaml: .*DUPLICATE_POSITION"):
        check_registry(config_from_dict(raw))


def test_run_on_inputs(input_data, isin_only_config):
    results = run(input_data, isin_only_config)
    exceptions = results.exceptions
    assert exceptions["ExceptionID"].tolist() == ["EXC-0001"]
    assert exceptions.loc[0, "ControlID"] == "MISSING_ISIN"
    assert exceptions.loc[0, "SourceRow"] == A01_SOURCE_ROW
    assert results.reference_date == pd.Timestamp("2026-11-11")


def test_exception_ids_are_sequential_on_inputs(input_results):
    count = len(input_results.exceptions)
    assert input_results.exceptions["ExceptionID"].tolist() == [
        f"EXC-{number:04d}" for number in range(1, count + 1)]


def test_disabled_control_does_not_run(input_data, isin_only_config):
    config = with_overrides(isin_only_config, {"controls": {"MISSING_ISIN": {"enabled": False}}})
    exceptions = run(input_data, config).exceptions
    assert exceptions.empty
    assert exceptions.dtypes.equals(empty_exceptions().dtypes)


def test_same_inputs_give_the_same_exceptions(input_data, input_results):
    pd.testing.assert_frame_equal(run(input_data, CONFIG).exceptions, input_results.exceptions)


def test_empty_row_does_not_raise_missing_isin(input_data, isin_only_config):
    # Rule 11: reported once by INVALID_VALUE, never by MISSING_ISIN.
    empty_row = pd.DataFrame({"SourceRow": pd.array([99], dtype="Int64")})
    positions = pd.concat([input_data.positions, empty_row], ignore_index=True)
    changed = dataclasses.replace(input_data, positions=positions)
    exceptions = run(changed, isin_only_config).exceptions
    assert exceptions["SourceRow"].tolist() == [A01_SOURCE_ROW]


def test_unparseable_isin_does_not_raise_missing_isin(input_data, isin_only_config):
    # Rule 10: a non-text ISIN is a parse issue, reported only by INVALID_VALUE.
    positions = input_data.positions.copy()
    positions.loc[positions["SourceRow"] == 2, "ISIN"] = None
    issue = parse_issue("positions", 2, "ISIN", "123456789012")
    changed = dataclasses.replace(input_data, positions=positions,
                                  parse_issues=pd.concat([input_data.parse_issues, issue]))
    assert run(changed, isin_only_config).exceptions["SourceRow"].tolist() == [A01_SOURCE_ROW]


def test_positions_outside_the_reference_date_are_not_controlled(input_data, isin_only_config):
    positions = input_data.positions.copy()
    positions.loc[positions["SourceRow"] == A01_SOURCE_ROW, "ValuationDate"] = pd.Timestamp(
        "2026-11-04")
    changed = dataclasses.replace(input_data, positions=positions)
    assert run(changed, isin_only_config).exceptions.empty


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
