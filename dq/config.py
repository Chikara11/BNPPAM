from __future__ import annotations

import copy
import datetime
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pandas as pd
import yaml

SEVERITIES = ("Critical", "Warning", "Information")
INPUT_FILES = ("positions", "previous_prices", "cashflows")
# Upper bound of cashflow_horizon_days (10 years): a typo such as 1000000000
# days would overflow the date arithmetic.
MAX_HORIZON_DAYS = 3650

# Column types of docs/data_spec.md, used by the loader. It lives here so that
# the config can check that every required column has a type. Columns not
# listed here are kept as read.
COLUMN_TYPES = {
    "positions": {
        "ValuationDate": "date", "Portfolio": "text", "ISIN": "text",
        "SecurityName": "text", "AssetType": "text", "Currency": "text",
        "Nominal": "number", "BookPrice": "number", "MarketPrice": "number",
        "Rating": "text", "MaturityDate": "date",
    },
    "previous_prices": {
        "ISIN": "text", "PreviousDate": "date", "PreviousPrice": "number",
        "PriceSource": "text",
    },
    "cashflows": {
        "Portfolio": "text", "ISIN": "text", "CashflowDate": "date",
        "CashflowType": "text", "Currency": "text", "Amount": "number",
        "Status": "text",
    },
}

TOP_LEVEL_KEYS = (
    "reference_date", "cashflow_horizon_days", "isin_pattern", "price_move_thresholds_pct",
    "rating_required_for", "amount_sign", "allowed_values", "required_columns", "keys",
    "dimensions", "controls",
)

# Each key of config.yaml "keys" and the input file(s) whose columns it uses.
KEY_FILES = {
    "position_unique": ("positions",),
    "cashflow_unique": ("cashflows",),
    "cashflow_to_position": ("cashflows", "positions"),
    "previous_price_unique": ("previous_prices",),
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ControlSettings:
    enabled: bool
    severity: str
    label: str
    dimension: str  # one of Config.dimensions


@dataclass(frozen=True)
class Config:
    reference_date: pd.Timestamp | None
    cashflow_horizon_days: int
    isin_pattern: str
    price_move_thresholds_pct: Mapping[str, float]
    rating_required_for: tuple[str, ...]
    amount_sign: Mapping[str, str]  # CashflowType -> "positive" or "negative"
    allowed_values: Mapping[str, tuple[str, ...]]
    required_columns: Mapping[str, tuple[str, ...]]
    keys: Mapping[str, tuple[str, ...]]
    dimensions: tuple[str, ...]  # display order
    controls: Mapping[str, ControlSettings]  # config.yaml order = run order
    raw: Mapping[str, Any]  # read-only copy of the YAML, for overrides and audit


def load_config(path: str | Path) -> Config:
    """Read config.yaml with yaml.safe_load and build a validated Config.

    Returns the Config. Raises ConfigError if the file cannot be read, is not
    valid YAML or breaks a rule of config_from_dict (CLAUDE.md "Configuration").
    """
    try:
        with open(path, encoding="utf-8") as file:
            raw = yaml.safe_load(file)
    except OSError as error:
        raise ConfigError(f"Cannot read the config file {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"The config file {path} is not valid YAML: {error}") from error
    return config_from_dict(raw)


def config_from_dict(raw: dict) -> Config:
    """Validate a deep copy of `raw` and build a read-only Config.

    Returns the Config; every problem found is listed in a single ConfigError.
    The input dict is never kept or modified (CLAUDE.md "Configuration").
    """
    if not isinstance(raw, Mapping):
        raise ConfigError("The config must be a mapping of settings.")
    raw = copy.deepcopy(dict(raw))
    validate_config(raw)
    return Config(
        reference_date=parse_reference_date(raw["reference_date"]),
        cashflow_horizon_days=raw["cashflow_horizon_days"],
        isin_pattern=raw["isin_pattern"],
        price_move_thresholds_pct=MappingProxyType(
            {asset_type: float(value)
             for asset_type, value in raw["price_move_thresholds_pct"].items()}),
        rating_required_for=tuple(raw["rating_required_for"]),
        amount_sign=freeze(raw["amount_sign"]),
        allowed_values=freeze(raw["allowed_values"]),
        required_columns=freeze(raw["required_columns"]),
        keys=freeze(raw["keys"]),
        dimensions=tuple(raw["dimensions"]),
        controls=MappingProxyType(
            {control_id: ControlSettings(**settings)
             for control_id, settings in raw["controls"].items()}),
        raw=freeze(raw),
    )


def with_overrides(config: Config, overrides: Mapping) -> Config:
    """Return a new Config with `overrides` deep-merged and re-validated.

    Dicts are merged, lists and scalars replaced; an unknown key raises
    ConfigError. The original config is never modified (architecture.md 4.1).
    """
    if not isinstance(overrides, Mapping):
        raise ConfigError("Overrides must be a mapping, e.g. {'cashflow_horizon_days': 60}.")
    merged = thaw(config.raw)
    deep_merge(merged, overrides)
    return config_from_dict(merged)


def validate_config(raw: dict) -> None:
    missing = [key for key in TOP_LEVEL_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"config.yaml is invalid: missing setting(s) {', '.join(missing)}.")
    problems = (
        check_reference_date(raw)
        + check_horizon(raw)
        + check_isin_pattern(raw)
        + check_allowed_values(raw)
        + check_thresholds(raw)
        + check_rating_required_for(raw)
        + check_amount_sign(raw)
        + check_required_columns(raw)
        + check_keys(raw)
        + check_dimensions(raw)
        + check_controls(raw)
    )
    if problems:
        raise ConfigError("config.yaml is invalid:\n- " + "\n- ".join(problems))


def check_reference_date(raw: dict) -> list[str]:
    try:
        parse_reference_date(raw["reference_date"])
    except ValueError as error:
        return [str(error)]
    return []


def check_horizon(raw: dict) -> list[str]:
    value = raw["cashflow_horizon_days"]
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value <= MAX_HORIZON_DAYS):
        return [f"cashflow_horizon_days must be a whole number from 0 to {MAX_HORIZON_DAYS} "
                f"(got {value!r})."]
    return []


def check_isin_pattern(raw: dict) -> list[str]:
    pattern = raw["isin_pattern"]
    if not isinstance(pattern, str) or not pattern:
        return [f"isin_pattern must be a non-empty regular expression (got {pattern!r})."]
    try:
        re.compile(pattern)
    except re.error as error:
        return [f"isin_pattern is not a valid regular expression: {error}."]
    return []


def check_allowed_values(raw: dict) -> list[str]:
    allowed = raw["allowed_values"]
    if not isinstance(allowed, Mapping):
        return ["allowed_values must be a mapping of column -> list of values."]
    problems = [f"allowed_values.{column} must be a non-empty list of texts."
                for column, values in allowed.items() if not is_text_list(values)]
    if "AssetType" not in allowed:
        problems.append("allowed_values.AssetType is missing (needed for the thresholds).")
    return problems


def check_thresholds(raw: dict) -> list[str]:
    thresholds = raw["price_move_thresholds_pct"]
    if not isinstance(thresholds, Mapping):
        return ["price_move_thresholds_pct must be a mapping of asset type -> threshold."]
    asset_types = allowed_asset_types(raw)
    problems = [f"price_move_thresholds_pct has no threshold for asset type {asset_type}."
                for asset_type in asset_types if asset_type not in thresholds]
    for asset_type, value in thresholds.items():
        if asset_types and asset_type not in asset_types:
            problems.append(f"price_move_thresholds_pct.{asset_type} is not an allowed AssetType.")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            problems.append(f"price_move_thresholds_pct.{asset_type} must be a number >= 0 "
                            f"(got {value!r}).")
    return problems


def check_rating_required_for(raw: dict) -> list[str]:
    values = raw["rating_required_for"]
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return ["rating_required_for must be a list of asset types."]
    asset_types = allowed_asset_types(raw)
    return [f"rating_required_for: {value} is not an allowed AssetType."
            for value in values if asset_types and value not in asset_types]


def check_amount_sign(raw: dict) -> list[str]:
    signs = raw["amount_sign"]
    if not isinstance(signs, Mapping) or not signs:
        return ["amount_sign must be a mapping of CashflowType -> positive or negative."]
    allowed = raw["allowed_values"]
    types = allowed.get("CashflowType") if isinstance(allowed, Mapping) else None
    problems = [f"amount_sign.{cashflow_type} must be positive or negative (got {sign!r})."
                for cashflow_type, sign in signs.items() if sign not in ("positive", "negative")]
    if is_text_list(types):
        problems += [f"amount_sign: {cashflow_type} is not an allowed CashflowType."
                     for cashflow_type in signs if cashflow_type not in types]
    return problems


def check_required_columns(raw: dict) -> list[str]:
    required = raw["required_columns"]
    if not isinstance(required, Mapping):
        return ["required_columns must be a mapping of file -> list of columns."]
    problems = []
    for file in INPUT_FILES:
        columns = required.get(file)
        if not is_text_list(columns):
            problems.append(f"required_columns.{file} must be a non-empty list of column names.")
            continue
        # a required column without a type would skip type checking in the loader
        problems += [f"required_columns.{file}: column {column} has no type in COLUMN_TYPES "
                     f"(dq/config.py, docs/data_spec.md)."
                     for column in columns if column not in COLUMN_TYPES[file]]
    return problems


def check_keys(raw: dict) -> list[str]:
    keys, required = raw["keys"], raw["required_columns"]
    if not isinstance(keys, Mapping):
        return ["keys must be a mapping of key name -> list of columns."]
    problems = []
    for key_name, files in KEY_FILES.items():
        columns = keys.get(key_name)
        if not is_text_list(columns):
            problems.append(f"keys.{key_name} must be a non-empty list of column names.")
            continue
        for file in files:
            file_columns = required.get(file) if isinstance(required, Mapping) else None
            if not is_text_list(file_columns):
                continue  # already reported by check_required_columns
            problems += [f"keys.{key_name}: column {column} is not a required column of {file}."
                         for column in columns if column not in file_columns]
    return problems


def check_controls(raw: dict) -> list[str]:
    controls = raw["controls"]
    if not isinstance(controls, Mapping) or not controls:
        return ["controls must be a non-empty mapping of control ID -> settings."]
    problems = []
    for control_id, settings in controls.items():
        if not isinstance(settings, Mapping):
            problems.append(f"controls.{control_id} must have enabled, severity, label and "
                            f"dimension.")
            continue
        unknown = sorted(set(settings) - {"enabled", "severity", "label", "dimension"})
        if unknown:
            problems.append(f"controls.{control_id}: unknown setting(s) {', '.join(unknown)}.")
        if not isinstance(settings.get("enabled"), bool):
            problems.append(f"controls.{control_id}.enabled must be true or false.")
        if settings.get("severity") not in SEVERITIES:
            problems.append(f"controls.{control_id}.severity must be one of "
                            f"{', '.join(SEVERITIES)} (got {settings.get('severity')!r}).")
        label = settings.get("label")
        if not isinstance(label, str) or not label.strip():
            problems.append(f"controls.{control_id}.label must be a non-empty text.")
        dimensions = raw["dimensions"] if is_text_list(raw["dimensions"]) else []
        if settings.get("dimension") not in dimensions:
            problems.append(f"controls.{control_id}.dimension must be one of the dimensions "
                            f"list (got {settings.get('dimension')!r}).")
    return problems


def check_dimensions(raw: dict) -> list[str]:
    dimensions = raw["dimensions"]
    if not is_text_list(dimensions):
        return ["dimensions must be a non-empty list of texts."]
    repeated = sorted({value for value in dimensions if dimensions.count(value) > 1})
    return [f"dimensions: {value} is listed twice." for value in repeated]


def parse_reference_date(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"reference_date must be null or a date YYYY-MM-DD "
                             f"(got {value!r}).") from None
    if isinstance(value, datetime.date):  # datetime.datetime is a date too
        return pd.Timestamp(value).normalize()
    raise ValueError(f"reference_date must be null or a date YYYY-MM-DD (got {value!r}).")


def allowed_asset_types(raw: dict) -> list[str]:
    allowed = raw["allowed_values"]
    asset_types = allowed.get("AssetType") if isinstance(allowed, Mapping) else None
    return asset_types if is_text_list(asset_types) else []


def is_text_list(values: Any) -> bool:
    return (isinstance(values, list) and len(values) > 0
            and all(isinstance(value, str) for value in values))


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


def deep_merge(base: dict, overrides: Mapping, path: str = "") -> None:
    for key, value in overrides.items():
        where = f"{path}.{key}" if path else str(key)
        if key not in base:
            raise ConfigError(f"Unknown config setting in overrides: {where}.")
        if isinstance(base[key], dict) and isinstance(value, Mapping):
            deep_merge(base[key], value, where)
        else:
            base[key] = copy.deepcopy(thaw(value))
