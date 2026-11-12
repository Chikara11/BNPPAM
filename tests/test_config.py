import copy
import dataclasses
import datetime
from pathlib import Path

import pandas as pd
import pytest
import yaml

from dq.config import ConfigError, config_from_dict, load_config, with_overrides

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture(scope="module")
def raw_yaml() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as file:
        return yaml.safe_load(file)


@pytest.fixture
def raw(raw_yaml) -> dict:
    return copy.deepcopy(raw_yaml)


def test_real_config_loads(raw_yaml):
    config = load_config(CONFIG_PATH)
    assert config.reference_date is None
    assert config.cashflow_horizon_days == 30
    assert config.isin_pattern == r"^SYN\d{9}$"
    assert config.price_move_thresholds_pct == {"CLO Debt": 5.0, "ABS": 5.0, "CLO Equity": 15.0}
    assert config.rating_required_for == ("CLO Debt", "ABS")
    assert config.keys["position_unique"] == ("ValuationDate", "Portfolio", "ISIN")
    assert config.controls["MISSING_ISIN"].severity == "Critical"
    assert config.controls["NO_PRICE_HISTORY"].label == "New security without price history"


def test_controls_keep_the_yaml_order(raw_yaml):
    config = load_config(CONFIG_PATH)
    assert list(config.controls) == list(raw_yaml["controls"])
    assert len(config.controls) == 15


@pytest.mark.parametrize("value", ["2026-11-11", datetime.date(2026, 11, 11),
                                   datetime.datetime(2026, 11, 11, 15, 30)])
def test_reference_date_is_parsed_to_midnight(raw, value):
    raw["reference_date"] = value
    assert config_from_dict(raw).reference_date == pd.Timestamp("2026-11-11")


def test_with_overrides_changes_the_value_and_keeps_the_original():
    config = load_config(CONFIG_PATH)
    changed = with_overrides(config, {"price_move_thresholds_pct": {"ABS": 4.0}})
    assert changed.price_move_thresholds_pct["ABS"] == 4.0
    assert changed.price_move_thresholds_pct["CLO Debt"] == 5.0
    assert config.price_move_thresholds_pct["ABS"] == 5.0


def test_with_overrides_merges_nested_settings():
    config = load_config(CONFIG_PATH)
    changed = with_overrides(config, {"controls": {"MISSING_RATING": {"enabled": False}}})
    assert changed.controls["MISSING_RATING"].enabled is False
    assert changed.controls["MISSING_RATING"].severity == "Warning"
    assert config.controls["MISSING_RATING"].enabled is True


def test_with_overrides_replaces_lists():
    config = load_config(CONFIG_PATH)
    changed = with_overrides(config, {"rating_required_for": ["ABS"]})
    assert changed.rating_required_for == ("ABS",)


@pytest.mark.parametrize("overrides, named", [
    ({"price_move_thresholds_pct": {"ABSX": 4.0}}, "price_move_thresholds_pct.ABSX"),
    ({"cashflow_horizon": 60}, "cashflow_horizon"),
    ({"controls": {"NEW_CONTROL": {"enabled": True}}}, "controls.NEW_CONTROL"),
])
def test_with_overrides_rejects_unknown_keys(overrides, named):
    with pytest.raises(ConfigError, match=named):
        with_overrides(load_config(CONFIG_PATH), overrides)


def test_with_overrides_revalidates():
    with pytest.raises(ConfigError, match="cashflow_horizon_days"):
        with_overrides(load_config(CONFIG_PATH), {"cashflow_horizon_days": -1})


def break_missing_severity(raw):
    del raw["controls"]["MISSING_RATING"]["severity"]


def break_unknown_severity(raw):
    raw["controls"]["MISSING_ISIN"]["severity"] = "High"


def break_enabled_as_text(raw):
    raw["controls"]["MISSING_ISIN"]["enabled"] = "yes"


def break_empty_label(raw):
    raw["controls"]["MISSING_ISIN"]["label"] = " "


def break_missing_threshold(raw):
    del raw["price_move_thresholds_pct"]["ABS"]


def break_negative_threshold(raw):
    raw["price_move_thresholds_pct"]["ABS"] = -1


def break_horizon_as_bool(raw):
    raw["cashflow_horizon_days"] = True


def break_rating_required_for(raw):
    raw["rating_required_for"] = ["CLO Debt", "Loans"]


def break_key_column(raw):
    raw["keys"]["cashflow_unique"] = ["Portfolio", "ISIN", "Amonut"]


def break_reference_date(raw):
    raw["reference_date"] = "11/11/2026"


def break_missing_top_level(raw):
    del raw["keys"]


def break_missing_required_file(raw):
    del raw["required_columns"]["cashflows"]


def break_amount_sign_value(raw):
    raw["amount_sign"]["Fee"] = "minus"


def break_amount_sign_type(raw):
    raw["amount_sign"]["Dividend"] = "positive"


def break_isin_pattern(raw):
    raw["isin_pattern"] = "^SYN[0-9"


def break_required_column_without_type(raw):
    raw["required_columns"]["positions"].append("Seniority")


@pytest.mark.parametrize("breaker, named", [
    (break_missing_severity, "controls.MISSING_RATING.severity"),
    (break_unknown_severity, "controls.MISSING_ISIN.severity"),
    (break_enabled_as_text, "controls.MISSING_ISIN.enabled"),
    (break_empty_label, "controls.MISSING_ISIN.label"),
    (break_missing_threshold, "no threshold for asset type ABS"),
    (break_negative_threshold, "price_move_thresholds_pct.ABS"),
    (break_horizon_as_bool, "cashflow_horizon_days"),
    (break_rating_required_for, "Loans"),
    (break_key_column, "Amonut"),
    (break_reference_date, "reference_date"),
    (break_missing_top_level, "keys"),
    (break_missing_required_file, "required_columns.cashflows"),
    (break_required_column_without_type, "Seniority has no type"),
    (break_isin_pattern, "isin_pattern"),
    (break_amount_sign_value, "amount_sign.Fee"),
    (break_amount_sign_type, "Dividend is not an allowed CashflowType"),
])
def test_invalid_config_raises_a_clear_error(raw, breaker, named):
    breaker(raw)
    with pytest.raises(ConfigError, match=named):
        config_from_dict(raw)


def test_every_problem_is_listed_at_once(raw):
    break_unknown_severity(raw)
    break_missing_threshold(raw)
    with pytest.raises(ConfigError) as error:
        config_from_dict(raw)
    assert "MISSING_ISIN.severity" in str(error.value)
    assert "asset type ABS" in str(error.value)


def test_config_is_read_only():
    config = load_config(CONFIG_PATH)
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.cashflow_horizon_days = 60
    with pytest.raises(TypeError):
        config.price_move_thresholds_pct["ABS"] = 1.0
    with pytest.raises(TypeError):
        config.raw["controls"]["MISSING_ISIN"]["enabled"] = False
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.controls["MISSING_ISIN"].enabled = False


def test_config_from_dict_does_not_keep_a_link_to_its_input(raw):
    config = config_from_dict(raw)
    raw["cashflow_horizon_days"] = 99
    assert config.cashflow_horizon_days == 30


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read"):
        load_config(tmp_path / "missing.yaml")


def test_invalid_yaml_raises_config_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("controls: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)
