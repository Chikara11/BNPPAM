import pandas as pd
import pytest

# TEMPORARY: grows with each control. Once every control is registered, the
# test runs on every manifest case and this set is removed.
IMPLEMENTED_CASES = {"A01"}


def manifest_case(manifest: pd.DataFrame, case_id: str) -> pd.Series:
    return manifest[manifest["AnomalyID"] == case_id].iloc[0]


def matching_exceptions(exceptions: pd.DataFrame, case: pd.Series) -> pd.DataFrame:
    # ControlID + Portfolio + ISIN; a case without ISIN (A01) matches on
    # ControlID + Portfolio. The manifest File column is ignored.
    found = exceptions[(exceptions["ControlID"] == case["ExpectedControl"])
                       & (exceptions["Portfolio"] == case["Portfolio"])]
    if pd.isna(case["ISIN"]):
        return found
    return found[found["ISIN"] == case["ISIN"]]


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


def test_implemented_controls_raise_no_unexpected_exception(input_results, manifest,
                                                            test_config):
    # No false positive: every exception of the registered controls is expected.
    expected = manifest[manifest["ExpectedControl"].isin(list(test_config.controls))]
    assert len(input_results.exceptions) == expected["ExpectedExceptionCount"].sum()


def test_clean_data_raises_no_exception(clean_results):
    assert clean_results.exceptions.empty
