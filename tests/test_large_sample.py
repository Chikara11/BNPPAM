import time
from collections import Counter

import pandas as pd
import pytest

from dq.engine import run
from dq.loaders import load_inputs
from helpers import CONFIG, REPO_ROOT

LARGE_DIR = REPO_ROOT / "data" / "large"
LARGE_CLEAN_DIR = REPO_ROOT / "data" / "large_clean"
LARGE_MANIFEST = REPO_ROOT / "docs" / "large_manifest.xlsx"
MAX_RUN_SECONDS = 5.0


def load(folder):
    return load_inputs(folder / "positions.xlsx", folder / "previous_prices.xlsx",
                       folder / "cashflows.xlsx", CONFIG)


def text_or_none(value):
    return None if pd.isna(value) else value


@pytest.fixture(scope="module")
def large_data():
    return load(LARGE_DIR)


@pytest.fixture(scope="module")
def large_results(large_data):
    return run(large_data, CONFIG)


@pytest.fixture(scope="module")
def large_manifest():
    return pd.read_excel(LARGE_MANIFEST, dtype=object)


def expected_rows(manifest: pd.DataFrame) -> pd.DataFrame:
    return manifest[manifest["ExpectedExceptionCount"] > 0]


def test_large_clean_base_raises_no_exception():
    assert run(load(LARGE_CLEAN_DIR), CONFIG).exceptions.empty


def test_large_sample_raises_exactly_the_manifest_exceptions(large_results, large_manifest):
    # No missed anomaly, no false positive, no substitution: the multiset of
    # (ControlID, Portfolio, ISIN) equals the manifest's expected rows.
    expected = Counter()
    for _, case in expected_rows(large_manifest).iterrows():
        key = (case["ExpectedControl"], text_or_none(case["Portfolio"]),
               text_or_none(case["ISIN"]))
        expected[key] += case["ExpectedExceptionCount"]
    produced = Counter((row.ControlID, text_or_none(row.Portfolio), text_or_none(row.ISIN))
                       for row in large_results.exceptions.itertuples())
    assert produced == expected


def test_large_sample_total_and_severities_match_the_manifest(large_results, large_manifest):
    expected = expected_rows(large_manifest)
    assert len(large_results.exceptions) == expected["ExpectedExceptionCount"].sum()
    for control, settings in CONFIG.controls.items():  # severities come from config.yaml
        assert (expected.loc[expected["ExpectedControl"] == control, "ExpectedSeverity"]
                == settings.severity).all()
    by_severity = expected.groupby("ExpectedSeverity")["ExpectedExceptionCount"].sum()
    produced = large_results.exceptions["Severity"].astype(str).value_counts()
    assert produced.to_dict() == by_severity.to_dict()


def test_large_sample_two_problem_rows_give_two_exceptions_on_one_row(large_results,
                                                                       large_manifest):
    cases = large_manifest[large_manifest["AnomalyID"].str.startswith("D")]
    assert cases["AnomalyID"].nunique() == 5 and len(cases) == 10
    exceptions = large_results.exceptions
    for case_id, rows in cases.groupby("AnomalyID"):
        controls = set(rows["ExpectedControl"])
        found = exceptions[exceptions["ControlID"].isin(controls)
                           & (exceptions["Portfolio"] == rows["Portfolio"].iloc[0])]
        isin = text_or_none(rows["ISIN"].iloc[0])
        found = found[found["ISIN"].isna()] if isin is None else found[found["ISIN"] == isin]
        assert set(found["ControlID"]) == controls, case_id
        assert found["SourceRow"].nunique() == 1, case_id  # the same row


def test_large_sample_near_boundary_cases_raise_nothing(large_results, large_manifest):
    cases = large_manifest[large_manifest["AnomalyID"].str.startswith("B")]
    assert len(cases) == 10 and (cases["ExpectedExceptionCount"] == 0).all()
    flagged = large_results.exceptions["ISIN"].isin(cases["ISIN"])
    assert not flagged.any()


def test_large_sample_boundary_moves_are_computed_at_their_exact_value(large_results,
                                                                        large_manifest):
    moves = large_results.price_movements.set_index("ISIN")
    for _, case in large_manifest[large_manifest["Field"].str.contains("MarketPrice")
                                  & large_manifest["AnomalyID"].str.startswith("B")].iterrows():
        move = moves.loc[case["ISIN"]]
        assert move["MoveStatus"] == "Computed" and move["Breach"] == False  # noqa: E712
        assert abs(move["PriceChangePct"]) <= move["ThresholdPct"]


def test_large_sample_b08_floating_point_residue_does_not_alert(large_results,
                                                                 large_manifest):
    case = large_manifest[large_manifest["AnomalyID"] == "B08"].iloc[0]
    move = large_results.price_movements.set_index("ISIN").loc[case["ISIN"]]
    raw = (move["MarketPrice"] - move["PreviousPrice"]) / move["PreviousPrice"] * 100
    assert raw > 5 and move["PriceChangePct"] == 5.0 and move["Breach"] == False  # noqa: E712


@pytest.mark.parametrize("case_id, days", [("B09", 0), ("B10", 30)])
def test_large_sample_horizon_boundaries_are_upcoming(large_results, large_manifest, case_id,
                                                      days):
    case = large_manifest[large_manifest["AnomalyID"] == case_id].iloc[0]
    upcoming = large_results.upcoming_cashflows
    rows = upcoming[(upcoming["Portfolio"] == case["Portfolio"])
                    & (upcoming["ISIN"] == case["ISIN"]) & (upcoming["DaysFromReference"] == days)]
    assert len(rows) == 1


def test_large_sample_engine_run_is_fast(large_data):
    start = time.perf_counter()
    run(large_data, CONFIG)
    assert time.perf_counter() - start < MAX_RUN_SECONDS
