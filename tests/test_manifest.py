from collections import Counter

import pandas as pd
import pytest

# TEMPORARY: grows with each control. Once every control is registered, the
# test runs on every manifest case and this set is removed.
IMPLEMENTED_CASES = {"A01", "A02", "A03", "A04", "A09", "A10"}


def manifest_case(manifest: pd.DataFrame, case_id: str) -> pd.Series:
    return manifest[manifest["AnomalyID"] == case_id].iloc[0]


def text_or_none(value):
    return None if pd.isna(value) else value


def matching_exceptions(exceptions: pd.DataFrame, case: pd.Series) -> pd.DataFrame:
    # ControlID + Portfolio + ISIN; a case without ISIN (A01) matches on
    # ControlID + Portfolio. The manifest File column is ignored.
    found = exceptions[(exceptions["ControlID"] == case["ExpectedControl"])
                       & (exceptions["Portfolio"] == case["Portfolio"])]
    if pd.isna(case["ISIN"]):
        return found
    return found[found["ISIN"] == case["ISIN"]]


def listed_values(cell) -> list[str]:
    # The no-alert cases list several values separated by "; ".
    return [value.strip() for value in str(cell).split(";")]


@pytest.mark.parametrize("case_id", sorted(IMPLEMENTED_CASES))
def test_expected_anomaly_is_detected_with_its_severity(input_results, manifest, case_id):
    case = manifest_case(manifest, case_id)
    found = matching_exceptions(input_results.exceptions, case)
    assert len(found) == case["ExpectedExceptionCount"]
    assert (found["Severity"] == case["ExpectedSeverity"]).all()


def test_a01_has_no_isin_and_points_to_the_row(input_results, manifest):
    found = matching_exceptions(input_results.exceptions, manifest_case(manifest, "A01"))
    assert found["ISIN"].isna().all()
    assert found["SourceRow"].tolist() == [21]


def test_registered_controls_raise_exactly_the_expected_exceptions(input_results, manifest,
                                                                   test_config):
    # No false positive and no substitution: the multiset of
    # (ControlID, Portfolio, ISIN) must equal the manifest's expected rows.
    expected_rows = manifest[manifest["ExpectedControl"].isin(list(test_config.controls))
                             & (manifest["ExpectedExceptionCount"] > 0)]
    expected = Counter()
    for _, case in expected_rows.iterrows():
        key = (case["ExpectedControl"], case["Portfolio"], text_or_none(case["ISIN"]))
        expected[key] += case["ExpectedExceptionCount"]
    produced = Counter(
        (row.ControlID, text_or_none(row.Portfolio), text_or_none(row.ISIN))
        for row in input_results.exceptions.itertuples())
    assert produced == expected


def test_n03_same_isin_in_two_portfolios_is_not_a_duplicate(input_results, input_data,
                                                             manifest):
    case = manifest_case(manifest, "N03")
    shared = listed_values(case["ISIN"])
    held_in = input_data.positions[input_data.positions["ISIN"].isin(shared)]
    assert held_in.groupby("ISIN")["Portfolio"].nunique().eq(2).all()
    exceptions = input_results.exceptions
    flagged = exceptions[(exceptions["ControlID"] == "DUPLICATE_POSITION")
                         & exceptions["ISIN"].isin(shared)]
    assert flagged.empty


def test_n04_unrated_clo_equity_is_legitimate(input_results, input_data):
    positions = input_data.positions
    equity_rows = positions.loc[positions["AssetType"] == "CLO Equity", "SourceRow"]
    assert positions.loc[positions["AssetType"] == "CLO Equity", "Rating"].isna().sum() == 8
    exceptions = input_results.exceptions
    flagged = exceptions[(exceptions["ControlID"] == "MISSING_RATING")
                         & exceptions["SourceRow"].isin(equity_rows)]
    assert flagged.empty


def test_clean_data_raises_no_exception(clean_results):
    assert clean_results.exceptions.empty
