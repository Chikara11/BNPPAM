import pytest

from dq.config import with_overrides
from dq.context import build_context, positions_at_reference_date
from dq.controls import cashflows as cashflow_controls
from dq.controls import positions as position_controls
from dq.controls import prices as price_controls
from helpers import (CONFIG, EMPTY_POSITION, cashflow, check, make_data, parse_issue,
                     position, price)


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


def test_no_price_history_fires_when_the_isin_has_no_price_row():
    data = make_data([position(ISIN="SYN000000002")])
    exceptions = check(price_controls.check_no_price_history, data)
    assert exceptions[["ControlID", "Severity", "ISIN"]].values.tolist() == [
        ["NO_PRICE_HISTORY", "Information", "SYN000000002"]]


@pytest.mark.parametrize("previous_date", ["2026-11-11", "2026-12-01", None])
def test_no_price_history_does_not_fire_when_unusable_rows_exist(previous_date):
    # The bad or empty date is reported by its own control, not here.
    data = make_data(previous_prices=[price(PreviousDate=previous_date)])
    assert check(price_controls.check_no_price_history, data).empty


def test_no_price_history_skips_a_position_without_isin():
    data = make_data([position(ISIN=None)])
    assert check(price_controls.check_no_price_history, data).empty


def test_price_move_fires_above_the_threshold():
    data = make_data([position(MarketPrice=91.0)], [price(PreviousPrice=98.0)])
    exceptions = check(price_controls.check_price_move_above_threshold, data)
    exception = exceptions.iloc[0]
    assert len(exceptions) == 1
    assert (exception["Source"], exception["Field"]) == ("positions", "MarketPrice")
    assert exception["ObservedValue"] == "-7.142857"
    assert "-7.14 %" in exception["Comment"] and "5 % threshold for CLO Debt" in exception["Comment"]


def test_price_move_does_not_fire_below_the_threshold():
    data = make_data([position(MarketPrice=96.0)], [price(PreviousPrice=98.0)])
    assert check(price_controls.check_price_move_above_threshold, data).empty


def test_price_move_uses_the_latest_price_strictly_before_the_reference_date():
    prices = [price(PreviousDate="2026-10-28", PreviousPrice=100.0),
              price(PreviousDate="2026-11-04", PreviousPrice=92.0),
              price(PreviousDate="2026-11-11", PreviousPrice=50.0)]  # on the reference date: ignored
    data = make_data([position(MarketPrice=92.0)], prices)
    assert check(price_controls.check_price_move_above_threshold, data).empty


@pytest.mark.parametrize("latest_price", [None, 0.0, -5.0])
def test_price_move_has_no_fallback_to_an_older_price(latest_price):
    # Rule 7: the latest price is unusable, the older one is not used.
    prices = [price(PreviousDate="2026-10-28", PreviousPrice=50.0),
              price(PreviousDate="2026-11-04", PreviousPrice=latest_price)]
    data = make_data([position(MarketPrice=98.0)], prices)
    assert check(price_controls.check_price_move_above_threshold, data).empty


@pytest.mark.parametrize("market_price", [None, 0.0, -1.5])
def test_price_move_is_not_computed_without_a_valid_market_price(market_price):
    data = make_data([position(MarketPrice=market_price)], [price(PreviousPrice=98.0)])
    assert check(price_controls.check_price_move_above_threshold, data).empty


@pytest.mark.parametrize("asset_type", [None, "Loan"])
def test_price_move_needs_a_known_asset_type(asset_type):
    data = make_data([position(AssetType=asset_type, MarketPrice=50.0)], [price()])
    assert check(price_controls.check_price_move_above_threshold, data).empty


def test_price_move_runs_on_each_portfolio_holding_the_isin():
    data = make_data([position(MarketPrice=91.0), position(Portfolio="CLO_GLOBAL_02",
                                                           MarketPrice=91.0)],
                     [price(PreviousPrice=98.0)])
    exceptions = check(price_controls.check_price_move_above_threshold, data)
    assert exceptions["Portfolio"].tolist() == ["CLO_EURO_01", "CLO_GLOBAL_02"]


def test_duplicate_previous_price_with_the_same_price_is_still_used():
    data = make_data([position(MarketPrice=91.0)],
                     [price(PreviousPrice=98.0), price(PreviousPrice=98.0)])
    duplicates = check(price_controls.check_duplicate_previous_price, data)
    assert duplicates[["ControlID", "Severity", "Source", "Field", "SourceRow"]].values.tolist() == [
        ["DUPLICATE_PREVIOUS_PRICE", "Warning", "previous_prices", "ISIN+PreviousDate", 2]]
    assert "Same price on every row" in duplicates.iloc[0]["Comment"]
    assert len(check(price_controls.check_price_move_above_threshold, data)) == 1


def test_duplicate_previous_price_with_different_prices_blocks_the_movement():
    data = make_data([position(MarketPrice=91.0)],
                     [price(PreviousPrice=98.0), price(PreviousPrice=97.5)])
    duplicates = check(price_controls.check_duplicate_previous_price, data)
    assert len(duplicates) == 1
    assert "Prices differ (98, 97.5)" in duplicates.iloc[0]["Comment"]
    assert check(price_controls.check_price_move_above_threshold, data).empty


def test_duplicate_previous_price_does_not_fire_on_different_dates():
    data = make_data(previous_prices=[price(), price(PreviousDate="2026-10-28")])
    assert check(price_controls.check_duplicate_previous_price, data).empty


def test_duplicate_previous_price_ignores_securities_not_held():
    data = make_data(previous_prices=[price(), price(ISIN="SYN000000099"),
                                      price(ISIN="SYN000000099")])
    assert check(price_controls.check_duplicate_previous_price, data).empty


def test_unmatched_cashflow_fires_on_an_unknown_portfolio_isin_pair():
    data = make_data(cashflows=[cashflow(ISIN="SYN999999999")])
    exceptions = check(cashflow_controls.check_unmatched_cashflow, data)
    exception = exceptions.iloc[0]
    assert len(exceptions) == 1
    assert (exception["Field"], exception["ObservedValue"]) == (
        "Portfolio+ISIN", "CLO_EURO_01+SYN999999999")
    assert exception["SecurityName"] is None


def test_isin_held_in_another_portfolio_is_still_unmatched():
    data = make_data(cashflows=[cashflow(Portfolio="CLO_GLOBAL_02")])
    assert len(check(cashflow_controls.check_unmatched_cashflow, data)) == 1


def test_matched_cashflow_is_not_unmatched():
    assert check(cashflow_controls.check_unmatched_cashflow, make_data()).empty


@pytest.mark.parametrize("column", ["Portfolio", "ISIN"])
def test_cashflow_with_an_empty_key_is_not_unmatched(column):
    # The empty value is reported by MISSING_VALUE.
    data = make_data(cashflows=[cashflow(**{column: None})])
    assert check(cashflow_controls.check_unmatched_cashflow, data).empty


def test_cashflow_of_a_position_outside_the_reference_date_is_unmatched():
    # The engine only keeps positions at the reference date.
    data = make_data([position(), position(ValuationDate="2026-11-04", ISIN="SYN000000002")],
                     cashflows=[cashflow(ISIN="SYN000000002")])
    ctx = build_context(data, CONFIG)
    exceptions = cashflow_controls.check_unmatched_cashflow(
        positions_at_reference_date(data, ctx), ctx)
    assert exceptions["ISIN"].tolist() == ["SYN000000002"]


def test_duplicated_position_does_not_duplicate_cashflow_checks():
    # Rule 4: membership test, never a row join.
    data = make_data([position(), position()], cashflows=[cashflow(Currency="USD")])
    assert len(check(cashflow_controls.check_currency_mismatch, data)) == 1
    assert check(cashflow_controls.check_unmatched_cashflow, data).empty


def test_duplicate_cashflow_fires_once_per_key():
    data = make_data(cashflows=[cashflow(), cashflow(), cashflow(Amount=75000.01)])
    exceptions = check(cashflow_controls.check_duplicate_cashflow, data)
    assert len(exceptions) == 1
    assert exceptions.iloc[0]["SourceRow"] == 2
    assert "Excel rows 2, 3" in exceptions.iloc[0]["Comment"]
    assert exceptions.iloc[0]["SecurityName"] == "Aurora CLO 2023-1 B"


@pytest.mark.parametrize("changes", [{"Amount": 75000.01}, {"CashflowType": "Principal"},
                                     {"CashflowDate": "2026-11-26"},
                                     {"Portfolio": "CLO_GLOBAL_02"}])
def test_cashflows_differing_on_one_key_column_are_not_duplicates(changes):
    data = make_data(cashflows=[cashflow(), cashflow(**changes)])
    assert check(cashflow_controls.check_duplicate_cashflow, data).empty


def test_past_forecast_cashflow_fires_before_the_reference_date():
    data = make_data(cashflows=[cashflow(CashflowDate="2026-11-06", Status="Forecast")])
    exceptions = check(cashflow_controls.check_past_forecast_cashflow, data)
    assert exceptions[["ControlID", "Field", "ObservedValue"]].values.tolist() == [
        ["PAST_FORECAST_CASHFLOW", "CashflowDate", "2026-11-06"]]


@pytest.mark.parametrize("cashflow_date, status", [
    ("2026-11-11", "Forecast"),   # on the reference date: not past-dated
    ("2026-11-06", "Paid"),
    ("2026-11-06", "Confirmed"),
    ("2026-11-06", " Forecast"),  # not an allowed value: INVALID_VALUE only
])
def test_past_forecast_cashflow_does_not_fire(cashflow_date, status):
    data = make_data(cashflows=[cashflow(CashflowDate=cashflow_date, Status=status)])
    assert check(cashflow_controls.check_past_forecast_cashflow, data).empty


def test_currency_mismatch_fires_on_a_matched_cashflow():
    data = make_data(cashflows=[cashflow(Currency="USD")])
    exceptions = check(cashflow_controls.check_currency_mismatch, data)
    assert exceptions[["ControlID", "Severity", "ObservedValue"]].values.tolist() == [
        ["CURRENCY_MISMATCH", "Information", "USD"]]
    assert exceptions.iloc[0]["Comment"] == "Cashflow in USD, position in EUR."


@pytest.mark.parametrize("changes", [
    {},                                        # same currency
    {"ISIN": "SYN999999999", "Currency": "USD"},  # unmatched: rule 6
    {"Currency": "eur"},                       # not an allowed value: INVALID_VALUE only
    {"Currency": None},                        # empty: MISSING_VALUE only
])
def test_currency_mismatch_does_not_fire(changes):
    data = make_data(cashflows=[cashflow(**changes)])
    assert check(cashflow_controls.check_currency_mismatch, data).empty
