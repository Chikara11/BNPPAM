import pytest

from dq.config import with_overrides
from dq.context import build_context, positions_at_reference_date
from dq.controls import cashflows as cashflow_controls
from dq.controls import common as common_controls
from dq.controls import positions as position_controls
from dq.controls import prices as price_controls
from helpers import (CONFIG, cashflow, check, empty_row, make_data, parse_issue, position,
                     price, raised)


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
    data = make_data([position(), empty_row("positions")])
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


def test_cashflow_is_compared_with_the_first_occurrence_of_a_duplicated_position():
    # Rule 12: the second row (USD) is reported by DUPLICATE_POSITION only,
    # it does not make a USD cashflow match the EUR position.
    data = make_data([position(), position(Currency="USD", MaturityDate="2026-12-01")],
                     cashflows=[cashflow(Currency="USD", CashflowDate="2026-12-05")])
    assert raised(data) == [
        ("CURRENCY_MISMATCH", "cashflows", "Currency"),
        ("DUPLICATE_POSITION", "positions", "ValuationDate+Portfolio+ISIN")]


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


def test_small_clean_dataset_raises_nothing():
    assert raised(make_data()) == []


def test_zero_market_price_is_invalid_price_only():
    assert raised(make_data([position(MarketPrice=0.0)])) == [
        ("INVALID_PRICE", "positions", "MarketPrice")]


def test_empty_nominal_is_missing_value_only():
    assert raised(make_data([position(Nominal=None)])) == [
        ("MISSING_VALUE", "positions", "Nominal")]


@pytest.mark.parametrize("maturity", ["2026-11-01", "2026-11-11"])
def test_maturity_on_or_before_the_reference_date_is_invalid_value_only(maturity):
    data = make_data([position(MaturityDate=maturity)], cashflows=[])
    assert raised(data) == [("INVALID_VALUE", "positions", "MaturityDate")]


def test_empty_rating_on_clo_debt_is_missing_rating_only():
    assert raised(make_data([position(Rating=None)])) == [
        ("MISSING_RATING", "positions", "Rating")]


def test_empty_rating_on_clo_equity_raises_nothing():
    assert raised(make_data([position(AssetType="CLO Equity", Rating=None)])) == []


@pytest.mark.parametrize("data, expected", [
    (make_data([position(BookPrice=None)]), ("MISSING_VALUE", "positions", "BookPrice")),
    (make_data(previous_prices=[price(PreviousPrice=None)]),
     ("MISSING_VALUE", "previous_prices", "PreviousPrice")),
    (make_data(cashflows=[cashflow(Amount=None)]), ("MISSING_VALUE", "cashflows", "Amount")),
    (make_data(previous_prices=[price(), price(ISIN=None, PreviousPrice=-1.0)]),
     ("MISSING_VALUE", "previous_prices", "ISIN")),
])
def test_missing_value_fires_alone_in_each_file(data, expected):
    assert raised(data) == [expected]


def test_missing_value_ignores_cells_with_a_dedicated_control():
    exceptions = check(common_controls.check_missing_value,
                       make_data([position(ISIN=None, MarketPrice=None, Rating=None)]))
    assert exceptions.empty


def test_unparseable_cell_is_invalid_value_only_with_the_raw_text():
    data = make_data([position(Nominal=None)],
                     parse_issues=parse_issue("positions", 2, "Nominal", "5,000,000"))
    exceptions = check(common_controls.check_invalid_value, data)
    assert exceptions[["Field", "ObservedValue"]].values.tolist() == [["Nominal", "5,000,000"]]
    assert raised(data) == [("INVALID_VALUE", "positions", "Nominal")]


@pytest.mark.parametrize("file", ["positions", "previous_prices", "cashflows"])
def test_empty_row_is_reported_once_in_every_file(file):
    rows = {"positions": [position(), empty_row(file)],
            "previous_prices": [price(), empty_row(file)],
            "cashflows": [cashflow(), empty_row(file)]}
    data = make_data(**{file: rows[file]})
    exceptions = check(common_controls.check_invalid_value, data)
    assert exceptions[["Field", "Comment", "SourceRow"]].values.tolist() == [["row", "Empty row", 3]]
    assert raised(data) == [("INVALID_VALUE", file, "row")]


@pytest.mark.parametrize("data, expected", [
    (make_data([position(Rating="AA+")]), ("INVALID_VALUE", "positions", "Rating")),
    (make_data(cashflows=[cashflow(Currency=" EUR")]), ("INVALID_VALUE", "cashflows", "Currency")),
    (make_data(cashflows=[cashflow(Portfolio="CLO_EURO_99")]),
     ("INVALID_VALUE", "cashflows", "Portfolio")),
    (make_data(previous_prices=[price(PriceSource="Bloomberg")]),
     ("INVALID_VALUE", "previous_prices", "PriceSource")),
])
def test_value_outside_the_allowed_list_is_invalid_value_only(data, expected):
    # No CURRENCY_MISMATCH on " EUR", no UNMATCHED_CASHFLOW on an unknown portfolio.
    assert raised(data) == [expected]


@pytest.mark.parametrize("data, expected", [
    (make_data([position(ISIN="SYN00000001")], previous_prices=[], cashflows=[]),
     ("INVALID_VALUE", "positions", "ISIN")),
    (make_data(cashflows=[cashflow(), cashflow(ISIN="syn000000001", Currency="USD")]),
     ("INVALID_VALUE", "cashflows", "ISIN")),
    (make_data(previous_prices=[price(), price(ISIN="SYN000000001 ", PreviousPrice=0.0)]),
     ("INVALID_VALUE", "previous_prices", "ISIN")),
])
def test_badly_formatted_isin_is_invalid_value_only(data, expected):
    # Rule 1: no NO_PRICE_HISTORY, UNMATCHED_CASHFLOW, CURRENCY_MISMATCH or
    # INVALID_PRICE on the row.
    assert raised(data) == [expected]


def test_cashflow_after_the_maturity_of_its_position_is_invalid_value_only():
    data = make_data(cashflows=[cashflow(CashflowDate="2036-04-16")])
    exceptions = check(common_controls.check_invalid_value, data)
    assert exceptions["Comment"].tolist() == [
        "CashflowDate is after the MaturityDate of its position (2036-04-15)."]
    assert raised(data) == [("INVALID_VALUE", "cashflows", "CashflowDate")]


def test_cashflow_on_the_maturity_date_is_valid():
    assert raised(make_data(cashflows=[cashflow(CashflowDate="2036-04-15")])) == []


def test_unmatched_cashflow_is_not_compared_with_a_maturity():
    data = make_data(cashflows=[cashflow(ISIN="SYN999999999", CashflowDate="2040-01-01")])
    assert raised(data) == [("UNMATCHED_CASHFLOW", "cashflows", "Portfolio+ISIN")]


@pytest.mark.parametrize("previous_date", ["2026-11-11", "2026-11-20"])
def test_previous_date_on_or_after_the_reference_date_is_invalid_value_only(previous_date):
    # The security still has a price row, so no NO_PRICE_HISTORY.
    data = make_data(previous_prices=[price(PreviousDate=previous_date)])
    assert raised(data) == [("INVALID_VALUE", "previous_prices", "PreviousDate")]


def test_prices_of_securities_not_held_are_ignored():
    data = make_data(previous_prices=[price(), price(ISIN="SYN000000099", PreviousPrice=-1.0,
                                                     PreviousDate="2026-12-01")])
    assert raised(data) == []


def test_invalid_price_fires_once_per_field():
    data = make_data([position(MarketPrice=-1.0, BookPrice=0.0)])
    exceptions = check(common_controls.check_invalid_price, data)
    assert exceptions[["Field", "ObservedValue"]].values.tolist() == [
        ["MarketPrice", "-1"], ["BookPrice", "0"]]


def test_invalid_previous_price_fires():
    data = make_data(previous_prices=[price(PreviousPrice=0.0)])
    assert raised(data) == [("INVALID_PRICE", "previous_prices", "PreviousPrice")]


@pytest.mark.parametrize("book_price", [None, 0.01])
def test_invalid_price_does_not_fire_on_empty_or_positive(book_price):
    data = make_data([position(BookPrice=book_price)])
    assert check(common_controls.check_invalid_price, data).empty


def test_duplicated_past_forecast_cashflow_gives_one_of_each():
    # Rule 12: only the duplicate control sees the repeated row.
    past = cashflow(CashflowDate="2026-11-06", Status="Forecast")
    assert raised(make_data(cashflows=[past, past])) == [
        ("DUPLICATE_CASHFLOW", "cashflows", "Portfolio+ISIN+CashflowDate+CashflowType+Amount"),
        ("PAST_FORECAST_CASHFLOW", "cashflows", "CashflowDate")]


def test_duplicated_position_with_a_negative_nominal_gives_one_of_each():
    bad = position(Nominal=-1.0)
    assert raised(make_data([bad, bad])) == [
        ("DUPLICATE_POSITION", "positions", "ValuationDate+Portfolio+ISIN"),
        ("INVALID_NOMINAL", "positions", "Nominal")]


def test_duplicated_previous_price_with_a_zero_price_gives_one_of_each():
    zero = price(PreviousPrice=0.0)
    assert raised(make_data(previous_prices=[zero, zero])) == [
        ("DUPLICATE_PREVIOUS_PRICE", "previous_prices", "ISIN+PreviousDate"),
        ("INVALID_PRICE", "previous_prices", "PreviousPrice")]


def test_repeated_rows_with_a_bad_isin_are_each_evaluated():
    # Not compared by the duplicate control, so rule 12 does not hide them.
    bad = position(ISIN="SYN1")
    data = make_data([bad, bad], previous_prices=[], cashflows=[])
    assert raised(data) == [("INVALID_VALUE", "positions", "ISIN")] * 2


@pytest.mark.parametrize("data", [
    make_data([position(Portfolio=None), position(Portfolio=None)], cashflows=[]),
    make_data(cashflows=[cashflow(Amount=None), cashflow(Amount=None)]),
])
def test_repeated_rows_with_an_incomplete_key_are_each_evaluated(data):
    # Rule 12: an incomplete key is not compared by the duplicate control,
    # so both rows are reported and no duplicate is raised.
    file, column = ("positions", "Portfolio") if data.cashflows.empty else ("cashflows", "Amount")
    assert raised(data) == [("MISSING_VALUE", file, column)] * 2


def test_empty_valuation_date_is_missing_value_only():
    # Kept by the reference date filter so that it is reported, not dropped.
    data = make_data([position(), position(Portfolio="CLO_GLOBAL_02", ValuationDate=None)])
    assert raised(data) == [("MISSING_VALUE", "positions", "ValuationDate")]


def test_unparseable_valuation_date_is_invalid_value_only():
    data = make_data([position(), position(Portfolio="CLO_GLOBAL_02", ValuationDate=None)],
                     parse_issues=parse_issue("positions", 3, "ValuationDate", "11/11/2026"))
    exceptions = check(common_controls.check_invalid_value, data)
    assert exceptions[["Field", "ObservedValue"]].values.tolist() == [
        ["ValuationDate", "11/11/2026"]]
    assert raised(data) == [("INVALID_VALUE", "positions", "ValuationDate")]


@pytest.mark.parametrize("cashflow_type, amount, comment", [
    ("Fee", 4500.0, "A Fee amount must be less than 0."),
    ("Fee", 0.0, "A Fee amount must be less than 0."),
    ("Coupon", -75000.0, "A Coupon amount must be greater than 0."),
    ("Principal", 0.0, "A Principal amount must be greater than 0."),
])
def test_amount_with_the_wrong_sign_is_invalid_value_only(cashflow_type, amount, comment):
    data = make_data(cashflows=[cashflow(CashflowType=cashflow_type, Amount=amount)])
    exceptions = check(common_controls.check_invalid_value, data)
    assert exceptions["Comment"].tolist() == [comment]
    assert raised(data) == [("INVALID_VALUE", "cashflows", "Amount")]


@pytest.mark.parametrize("cashflow_type, amount", [("Fee", -4500.0), ("Coupon", 75000.0),
                                                   ("Principal", 0.01)])
def test_amount_with_the_right_sign_is_valid(cashflow_type, amount):
    data = make_data(cashflows=[cashflow(CashflowType=cashflow_type, Amount=amount)])
    assert raised(data) == []


def test_amount_sign_is_not_checked_on_an_unknown_cashflow_type():
    data = make_data(cashflows=[cashflow(CashflowType="Dividend", Amount=-1.0)])
    assert raised(data) == [("INVALID_VALUE", "cashflows", "CashflowType")]
