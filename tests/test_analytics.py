import pandas as pd
import pytest

from dq.analytics import data_quality, price_movements, upcoming_cashflows
from dq.config import with_overrides
from dq.context import build_context
from dq.engine import run
from dq.schema import (DATA_QUALITY_COLUMNS, DIMENSION_QUALITY_COLUMNS, EXCEPTION_COLUMNS,
                       PRICE_MOVEMENT_COLUMNS,
                       SUMMARY_COLUMNS, UPCOMING_CASHFLOW_COLUMNS, empty_frame)
from helpers import CONFIG, cashflow, make_data, parse_issue, position, price


def manifest_values(manifest: pd.DataFrame, case_id: str) -> dict:
    # "PreviousPrice (VD-7); MarketPrice" + "98.00; 91.00" -> {field: value}
    case = manifest[manifest["AnomalyID"] == case_id].iloc[0]
    fields = [field.strip() for field in case["Field"].split(";")]
    values = [float(value) for value in str(case["InjectedValue"]).split(";")]
    return {"ISIN": case["ISIN"], **dict(zip(fields, values))}


def move_of(results, isin: str) -> pd.Series:
    moves = results.price_movements
    return moves[moves["ISIN"] == isin].iloc[0]


def summary_value(results, section: str, item: str, currency: str | None = None) -> float:
    summary = results.summary
    rows = summary[(summary["Section"] == section) & (summary["Item"] == item)]
    if currency is not None:
        rows = rows[rows["Currency"] == currency]
    assert len(rows) == 1
    return rows.iloc[0]["Value"]


def test_every_output_conforms_to_the_schema(input_results):
    for output, columns in [("exceptions", EXCEPTION_COLUMNS),
                            ("price_movements", PRICE_MOVEMENT_COLUMNS),
                            ("upcoming_cashflows", UPCOMING_CASHFLOW_COLUMNS),
                            ("data_quality", DATA_QUALITY_COLUMNS),
                            ("dimension_quality", DIMENSION_QUALITY_COLUMNS),
                            ("summary", SUMMARY_COLUMNS)]:
        frame = getattr(input_results, output)
        assert list(frame.columns) == list(columns)
        assert frame.dtypes.equals(empty_frame(columns).dtypes), output


def test_outputs_conform_on_empty_inputs():
    results = run(make_data(previous_prices=[], cashflows=[]), CONFIG)
    assert results.upcoming_cashflows.dtypes.equals(empty_frame(UPCOMING_CASHFLOW_COLUMNS).dtypes)
    assert results.price_movements.dtypes.equals(empty_frame(PRICE_MOVEMENT_COLUMNS).dtypes)


def test_price_movements_has_one_row_per_position(input_results, input_data):
    assert len(input_results.price_movements) == len(input_data.positions) == 52
    assert input_results.price_movements["SourceRow"].is_unique


@pytest.mark.parametrize("case_id", ["A06", "A07", "A08"])
def test_injected_moves_breach_with_the_manifest_values(input_results, manifest, case_id):
    values = manifest_values(manifest, case_id)
    move = move_of(input_results, values["ISIN"])
    previous, market = values["PreviousPrice (VD-7)"], values["MarketPrice"]
    assert (move["PreviousPrice"], move["MarketPrice"]) == (previous, market)
    assert move["PriceChangePct"] == round((market - previous) / previous * 100, 6)
    assert move["Breach"] == True  # noqa: E712 (pandas boolean)
    assert move["MoveStatus"] == "Computed"


@pytest.mark.parametrize("case_id, change", [("N01", -12.5), ("N02", -5.0)])
def test_no_alert_cases_are_computed_without_breach(input_results, manifest, case_id, change):
    move = move_of(input_results, manifest_values(manifest, case_id)["ISIN"])
    assert move["PriceChangePct"] == change
    assert move["Breach"] == False  # noqa: E712 (pandas boolean)


@pytest.mark.parametrize("isin, status", [("SYN000000046", "Missing market price"),   # A03
                                          ("SYN000000049", "No price history"),       # A05
                                          ("SYN000000020", "Invalid market price")])  # A11
def test_not_computed_moves_have_the_right_status(input_results, isin, status):
    move = move_of(input_results, isin)
    assert move["MoveStatus"] == status
    assert pd.isna(move["PriceChangePct"]) and pd.isna(move["Breach"])


def test_position_without_isin_is_not_computed(input_results):
    moves = input_results.price_movements
    assert moves.loc[moves["SourceRow"] == 21, "MoveStatus"].tolist() == ["Missing ISIN"]


def test_breaches_match_the_price_move_exceptions(input_results):
    exceptions = input_results.exceptions
    alerts = exceptions[exceptions["ControlID"] == "PRICE_MOVE_ABOVE_THRESHOLD"]
    breaches = input_results.price_movements[input_results.price_movements["Breach"].fillna(False)]
    assert sorted(breaches["SourceRow"]) == sorted(alerts["SourceRow"])
    assert len(breaches) == 3


def test_breaches_are_listed_first_by_size(input_results):
    head = input_results.price_movements.head(3)
    assert head["Breach"].all()
    assert head["PriceChangePct"].abs().tolist() == sorted(head["PriceChangePct"].abs(),
                                                           reverse=True)


@pytest.mark.parametrize("isin", ["SYN000000039", "SYN000000029", "SYN000000020"])  # A09, A10, A11
def test_market_value_is_empty_when_nominal_or_price_is_invalid(input_results, isin):
    assert pd.isna(move_of(input_results, isin)["MarketValue"])


def test_market_value_is_nominal_times_price_over_100(input_results):
    move = move_of(input_results, "SYN000000004")
    assert move["MarketValue"] == move["Nominal"] * move["MarketPrice"] / 100


def test_a12_is_upcoming_and_unmatched(input_results):
    upcoming = input_results.upcoming_cashflows
    a12 = upcoming[upcoming["ISIN"] == "SYN999999999"]
    assert len(a12) == 1
    assert a12.iloc[0]["MatchedPosition"] == False  # noqa: E712 (pandas boolean)
    assert pd.isna(a12.iloc[0]["SecurityName"])


def test_a14_past_dated_cashflow_is_not_upcoming(input_results):
    upcoming = input_results.upcoming_cashflows
    a14 = upcoming[(upcoming["ISIN"] == "SYN000000034")
                   & (upcoming["CashflowDate"] == pd.Timestamp("2026-11-06"))]
    assert a14.empty


def test_upcoming_cashflows_exclude_paid_and_stay_in_the_window(input_results):
    upcoming = input_results.upcoming_cashflows
    assert not (upcoming["Status"] == "Paid").any()
    assert upcoming["DaysFromReference"].between(0, 30).all()


def test_window_boundaries_are_inclusive():
    rows = [cashflow(CashflowDate=date) for date in
            ("2026-11-10", "2026-11-11", "2026-12-11", "2026-12-12")]
    rows.append(cashflow(CashflowDate="2026-11-11", Status="Paid"))
    data = make_data(cashflows=rows)
    upcoming = upcoming_cashflows(data, build_context(data, CONFIG))
    assert upcoming["CashflowDate"].dt.strftime("%Y-%m-%d").tolist() == ["2026-11-11",
                                                                        "2026-12-11"]
    assert upcoming["DaysFromReference"].tolist() == [0, 30]


def test_horizon_comes_from_the_config():
    data = make_data(cashflows=[cashflow(CashflowDate="2027-01-10")])
    config = with_overrides(CONFIG, {"cashflow_horizon_days": 60})
    assert upcoming_cashflows(data, build_context(data, CONFIG)).empty
    assert len(upcoming_cashflows(data, build_context(data, config))) == 1


def test_clo_equity_ratings_are_not_applicable_not_missing(input_results):
    quality = input_results.data_quality
    rating = quality[(quality["Source"] == "positions") & (quality["Column"] == "Rating")].iloc[0]
    assert (rating["Rows"], rating["NotApplicable"], rating["Missing"]) == (52, 8, 1)  # A04
    assert rating["CompletenessPct"] == round(43 / 44 * 100, 2)


def test_data_quality_counts_parse_issues_apart_from_missing():
    data = make_data([position(Nominal=None)],
                     parse_issues=parse_issue("positions", 2, "Nominal", "abc"))
    quality = data_quality(data, build_context(data, CONFIG))
    nominal = quality[(quality["Source"] == "positions") & (quality["Column"] == "Nominal")].iloc[0]
    assert (nominal["Missing"], nominal["ParseIssues"]) == (0, 1)
    assert nominal["CompletenessPct"] == 0.0  # a cell that cannot be read is not complete


def test_data_quality_lists_every_column_in_data_spec_order(input_results):
    quality = input_results.data_quality
    assert quality["Source"].unique().tolist() == ["positions", "previous_prices", "cashflows"]
    assert len(quality) == 11 + 4 + 7


def test_summary_counts_exceptions_by_severity(input_results):
    assert [summary_value(input_results, "Exceptions by severity", severity)
            for severity in ("Critical", "Warning", "Information")] == [6, 7, 2]


def test_summary_lists_every_enabled_control_and_portfolio(input_results):
    summary = input_results.summary
    controls = summary.loc[summary["Section"] == "Exceptions by control", "Item"].tolist()
    assert controls == list(CONFIG.controls)
    portfolios = summary.loc[summary["Section"] == "Exceptions by portfolio", "Item"].tolist()
    assert portfolios == list(CONFIG.allowed_values["Portfolio"]) + ["(none)"]
    by_portfolio = summary.loc[summary["Section"] == "Exceptions by portfolio", "Value"].sum()
    assert by_portfolio == len(input_results.exceptions)


def test_no_amount_is_summed_across_currencies(input_results):
    summary = input_results.summary
    amounts = summary[summary["Unit"] == "amount"]
    assert amounts["Currency"].notna().all()
    assert not amounts.duplicated(["Section", "Currency"]).any()
    assert (summary.loc[summary["Unit"] != "amount", "Currency"].isna()).all()
    moves = input_results.price_movements
    for currency, total in moves.groupby("Currency")["MarketValue"].sum().items():
        assert summary_value(input_results, "Market value by currency", "Market value",
                             currency) == pytest.approx(total)


def test_summary_shows_the_ignored_position_rows(input_data, input_results):
    item = "Position rows not at the reference date (ignored)"
    assert summary_value(input_results, "Overview", item) == 0
    later = run(input_data, with_overrides(CONFIG, {"reference_date": "2026-11-12"}))
    assert summary_value(later, "Overview", item) == 52
    assert summary_value(later, "Overview", "Positions") == 0


def test_summary_price_moves_add_up(input_results):
    computed = summary_value(input_results, "Price moves", "Computed")
    not_computable = summary_value(input_results, "Price moves", "Not computable")
    assert (computed, not_computable) == (48, 4)
    assert summary_value(input_results, "Price moves", "Breaches") == 3


@pytest.mark.parametrize("data, status", [
    (make_data([position(ISIN=None)]), "Missing ISIN"),
    (make_data([position(ISIN="SYN1")]), "Invalid ISIN"),
    (make_data(previous_prices=[price(PreviousPrice=None)]), "Missing previous price"),
    (make_data(previous_prices=[price(PreviousPrice=0.0)]), "Invalid previous price"),
    (make_data(previous_prices=[price(PreviousDate="2026-11-20")]), "No usable previous price"),
    (make_data(previous_prices=[price(), price(PreviousPrice=50.0)]), "Conflicting previous prices"),
    (make_data([position(ISIN=None, MarketPrice=None)]), "Missing ISIN"),  # first reason wins
    (make_data(), "Computed"),
])
def test_move_status_names_the_precise_reason(data, status):
    moves = price_movements(data, build_context(data, CONFIG))
    assert moves["MoveStatus"].tolist() == [status]


def test_summary_shows_duplicated_rows_on_inputs(input_results):
    assert summary_value(input_results, "Overview",
                         "Duplicated position rows (counted twice in totals)") == 1  # A02
    assert summary_value(input_results, "Overview",
                         "Duplicated cashflow rows in the horizon") == 0  # A13 is beyond 30 days


def test_duplicated_rows_stay_in_the_totals():
    upcoming = cashflow(CashflowDate="2026-11-25")
    data = make_data([position(), position()], cashflows=[upcoming, upcoming])
    results = run(data, CONFIG)
    assert summary_value(results, "Overview",
                         "Duplicated position rows (counted twice in totals)") == 1
    assert summary_value(results, "Overview", "Duplicated cashflow rows in the horizon") == 1
    assert summary_value(results, "Overview", "Cashflows in the horizon") == 2
    assert summary_value(results, "Upcoming cashflows by currency", "Upcoming cashflows",
                         "EUR") == 2 * 75000.0


def test_every_exception_carries_the_dimension_of_its_control(input_results):
    exceptions = input_results.exceptions
    expected = exceptions["ControlID"].map(lambda control: CONFIG.controls[control].dimension)
    assert exceptions["Dimension"].tolist() == expected.tolist()


def test_dimension_quality_adds_up_to_the_exceptions(input_results):
    block = input_results.dimension_quality
    exceptions = input_results.exceptions
    assert block["Dimension"].tolist() == list(CONFIG.dimensions)  # config order, zeros kept
    assert block["Exceptions"].sum() == len(exceptions)
    for row in block.itertuples(index=False):
        found = exceptions[exceptions["Dimension"] == row.Dimension]
        assert row.Exceptions == len(found)
        assert (row.Critical, row.Warning, row.Information) == tuple(
            (found["Severity"] == severity).sum() for severity in ("Critical", "Warning",
                                                                    "Information"))
        assert set(row.Controls.split(", ")) == {
            control for control, settings in CONFIG.controls.items()
            if settings.dimension == row.Dimension}


def test_dimension_quality_lists_only_enabled_controls(input_data):
    config = with_overrides(CONFIG, {"controls": {"PAST_FORECAST_CASHFLOW": {"enabled": False},
                                                  "MISSING_ISIN": {"enabled": False}}})
    block = run(input_data, config).dimension_quality.set_index("Dimension")
    assert pd.isna(block.loc["Timeliness", "Controls"])  # no enabled control left
    assert block.loc["Timeliness", "Exceptions"] == 0
    assert "MISSING_ISIN" not in block.loc["Completeness", "Controls"]


def test_summary_counts_exceptions_by_dimension(input_results):
    summary = input_results.summary
    rows = summary[summary["Section"] == "Exceptions by dimension"]
    assert rows["Item"].tolist() == list(CONFIG.dimensions)
    assert rows["Value"].sum() == len(input_results.exceptions)
    block = input_results.dimension_quality
    assert rows["Value"].tolist() == block["Exceptions"].astype(float).tolist()
