import pytest

from dq.config import with_overrides
from dq.controls.prices import check_price_move_above_threshold
from dq.engine import run
from helpers import CONFIG, check, make_data, position, price


def price_alerts(asset_type: str, previous_price: float, market_price: float, config=CONFIG):
    data = make_data([position(AssetType=asset_type, MarketPrice=market_price)],
                     [price(PreviousPrice=previous_price)])
    return check(check_price_move_above_threshold, data, config)


@pytest.mark.parametrize("asset_type", ["CLO Debt", "ABS"])
@pytest.mark.parametrize("market_price", [95.0, 105.0])
def test_exactly_5_percent_does_not_alert(asset_type, market_price):
    assert price_alerts(asset_type, 100.0, market_price).empty


@pytest.mark.parametrize("previous_price, market_price", [(30.2, 31.71), (30.6, 29.07)])
def test_floating_point_residue_at_5_percent_does_not_alert(previous_price, market_price):
    # The raw result is 5.000000000000005 %: rule 7 rounds to 6 decimals first.
    assert abs((market_price - previous_price) / previous_price * 100) > 5
    assert price_alerts("CLO Debt", previous_price, market_price).empty


@pytest.mark.parametrize("asset_type", ["CLO Debt", "ABS"])
@pytest.mark.parametrize("market_price, observed", [(94.99, "-5.01"), (105.01, "5.01")])
def test_just_above_5_percent_alerts_both_ways(asset_type, market_price, observed):
    alerts = price_alerts(asset_type, 100.0, market_price)
    assert alerts["ObservedValue"].tolist() == [observed]


@pytest.mark.parametrize("market_price", [72.0, 88.0])
def test_clo_equity_10_percent_is_below_its_15_percent_threshold(market_price):
    assert price_alerts("CLO Equity", 80.0, market_price).empty


@pytest.mark.parametrize("market_price", [67.99, 92.01])
def test_clo_equity_alerts_just_above_15_percent(market_price):
    assert len(price_alerts("CLO Equity", 80.0, market_price)) == 1


def test_raising_the_clo_debt_threshold_to_8_percent_silences_a06(input_data, real_config):
    config = with_overrides(real_config, {"price_move_thresholds_pct": {"CLO Debt": 8.0}})
    moves = run(input_data, config).exceptions
    moves = moves[moves["ControlID"] == "PRICE_MOVE_ABOVE_THRESHOLD"]
    assert "SYN000000004" not in moves["ISIN"].tolist()  # A06: -7.14 % on CLO Debt
    assert sorted(moves["ISIN"]) == ["SYN000000025", "SYN000000033"]  # A08 and A07 still fire


def test_lowering_a_threshold_adds_alerts(input_data, real_config):
    default = run(input_data, real_config).exceptions
    lowered = run(input_data, with_overrides(real_config, {"price_move_thresholds_pct": {
        "CLO Equity": 12.0}})).exceptions
    def count(exceptions):
        return (exceptions["ControlID"] == "PRICE_MOVE_ABOVE_THRESHOLD").sum()
    assert count(lowered) == count(default) + 1  # N01: -12.5 % on CLO Equity


def test_disabled_price_control_raises_nothing(input_data, real_config):
    config = with_overrides(real_config, {"controls": {
        "PRICE_MOVE_ABOVE_THRESHOLD": {"enabled": False}}})
    exceptions = run(input_data, config).exceptions
    assert not (exceptions["ControlID"] == "PRICE_MOVE_ABOVE_THRESHOLD").any()
