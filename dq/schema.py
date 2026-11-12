from __future__ import annotations

import pandas as pd

from dq.config import SEVERITIES

# Column names, order and dtypes of docs/output_spec.md. If they differ,
# the document wins and this file is fixed.

SEVERITY_DTYPE = pd.CategoricalDtype(SEVERITIES, ordered=True)

# Cashflow Status values with a business meaning (CLAUDE.md "Reference date").
STATUS_PAID = "Paid"
STATUS_FORECAST = "Forecast"

# MoveStatus vocabulary of section 2, in priority order (first reason wins).
DUPLICATE_ROW = "Duplicate row (not evaluated)"
MISSING_ISIN = "Missing ISIN"
INVALID_ISIN = "Invalid ISIN"
MISSING_MARKET_PRICE = "Missing market price"
INVALID_MARKET_PRICE = "Invalid market price"
NO_PRICE_HISTORY = "No price history"
NO_USABLE_PRICE = "No usable previous price"
CONFLICTING_PRICES = "Conflicting previous prices"
MISSING_PREVIOUS_PRICE = "Missing previous price"
INVALID_PREVIOUS_PRICE = "Invalid previous price"
NO_THRESHOLD = "No threshold"
COMPUTED = "Computed"

# Summary sections of section 5, in output order.
SECTION_OVERVIEW = "Overview"
SECTION_BY_SEVERITY = "Exceptions by severity"
SECTION_BY_CONTROL = "Exceptions by control"
SECTION_DISABLED_CONTROLS = "Disabled controls"  # only when a control is disabled
SECTION_BY_DIMENSION = "Exceptions by dimension"
SECTION_BY_PORTFOLIO = "Exceptions by portfolio"
SECTION_MARKET_VALUE = "Market value by currency"
SECTION_UPCOMING_CASHFLOWS = "Upcoming cashflows by currency"
SECTION_PRICE_MOVES = "Price moves"
NO_PORTFOLIO = "(none)"  # exceptions without a portfolio

EXCEPTION_COLUMNS = {
    "ExceptionID": "object",
    "Severity": SEVERITY_DTYPE,
    "ControlID": "object",
    "Dimension": "object",
    "AnomalyType": "object",
    "Source": "object",
    "Portfolio": "object",
    "ISIN": "object",
    "SecurityName": "object",
    "Field": "object",
    "ObservedValue": "object",
    "Comment": "object",
    "SourceRow": "Int64",
}

PRICE_MOVEMENT_COLUMNS = {
    "Portfolio": "object",
    "ISIN": "object",
    "SecurityName": "object",
    "AssetType": "object",
    "Currency": "object",
    "Rating": "object",
    "Nominal": "float64",
    "PreviousDate": "datetime64[us]",
    "PreviousPrice": "float64",
    "MarketPrice": "float64",
    "PriceChangePct": "float64",
    "ThresholdPct": "float64",
    "Breach": "boolean",
    "MoveStatus": "object",
    "MarketValue": "float64",
    "SourceRow": "Int64",
}

UPCOMING_CASHFLOW_COLUMNS = {
    "CashflowDate": "datetime64[us]",
    "DaysFromReference": "Int64",
    "Portfolio": "object",
    "ISIN": "object",
    "SecurityName": "object",
    "AssetType": "object",
    "CashflowType": "object",
    "Amount": "float64",
    "Currency": "object",
    "Status": "object",
    "MatchedPosition": "boolean",
    "SourceRow": "Int64",
}

DATA_QUALITY_COLUMNS = {
    "Source": "object",
    "Column": "object",
    "Required": "boolean",
    "Rows": "Int64",
    "NotApplicable": "Int64",
    "Missing": "Int64",
    "ParseIssues": "Int64",
    "CompletenessPct": "float64",
}

# section 0: built by the loader, part of InputData
PARSE_ISSUE_COLUMNS = {
    "File": "object",
    "SourceRow": "Int64",
    "Column": "object",
    "RawValue": "object",
}

# section 4b: one row per data quality dimension of config.yaml
DIMENSION_QUALITY_COLUMNS = {
    "Dimension": "object",
    "Controls": "object",
    "Exceptions": "Int64",
    **{severity: "Int64" for severity in SEVERITIES},
}

SUMMARY_COLUMNS = {
    "Section": "object",
    "Item": "object",
    "Currency": "object",
    "Value": "float64",
    "Unit": "object",
}


def empty_frame(columns: dict) -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(dtype=dtype) for name, dtype in columns.items()})


def empty_exceptions() -> pd.DataFrame:
    """Return an empty exceptions frame.

    Columns and dtypes of output_spec.md section 1, so that a control with
    nothing to report still returns the common format.
    """
    return empty_frame(EXCEPTION_COLUMNS)


def conform(frame: pd.DataFrame, columns: dict) -> pd.DataFrame:
    # A mismatch here is a bug in the engine, not a problem in the data.
    missing = [name for name in columns if name not in frame.columns]
    extra = [name for name in frame.columns if name not in columns]
    if missing or extra:
        raise AssertionError(f"Output columns do not match docs/output_spec.md: "
                             f"missing {missing}, unexpected {extra}.")
    for name, dtype in columns.items():
        if isinstance(dtype, pd.CategoricalDtype):
            unknown = sorted(set(frame[name].dropna()) - set(dtype.categories))
            if unknown:
                raise AssertionError(f"{name}: value(s) {unknown} not in {list(dtype.categories)}.")
    return frame[list(columns)].astype(columns)
