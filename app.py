from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from dq import (Config, ConfigError, InputData, InputSchemaError, RegistryError, Results,
                load_config, load_inputs, run, with_overrides)
from dq.config import INPUT_FILES, SEVERITIES
from dq.report import write_report
from dq.schema import NO_PORTFOLIO, SECTION_BY_SEVERITY

# Display only (architecture.md section 4.9): every number comes from
# Results; the app filters and shows, it never computes.

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
SAMPLE_DIR = REPO_ROOT / "data" / "inputs"
SAMPLE = "Bundled sample (data/inputs)"
UPLOAD = "Upload the 3 files"
SETTING_KEY = "setting_"  # prefix of the sidebar widgets that Reset clears
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
UNIT_FORMATS = {"count": "%d", "amount": "%.2f", "pct": "%.2f"}


def main() -> None:
    st.set_page_config(page_title="CLO / ABS Monitoring", layout="wide")
    st.title("CLO / ABS portfolio monitoring")
    st.caption("Synthetic demonstration data: fictional securities, no real portfolio.")
    try:
        base = load_config(CONFIG_PATH)
        data = input_data()
        config = with_overrides(base, sidebar_overrides(base))
        if data is None:
            st.info("Upload positions.xlsx, previous_prices.xlsx and cashflows.xlsx "
                    "in the sidebar to run the controls.")
            return
        results = run(data, config)
    except (ConfigError, InputSchemaError, RegistryError) as error:
        st.error(str(error))  # a readable message, never a traceback
        return
    show_download(results)
    show_tabs(results)


def input_data() -> InputData | None:
    source = st.sidebar.radio("Input files", [SAMPLE, UPLOAD], key="source")
    if source == SAMPLE:
        files = {file: (SAMPLE_DIR / f"{file}.xlsx").read_bytes() for file in INPUT_FILES}
    else:
        uploads = {file: st.sidebar.file_uploader(f"{file}.xlsx", type="xlsx",
                                                  key=f"upload_{file}")
                   for file in INPUT_FILES}
        if any(upload is None for upload in uploads.values()):
            return None
        files = {file: upload.getvalue() for file, upload in uploads.items()}
    return load_files(files["positions"], files["previous_prices"], files["cashflows"])


@st.cache_data(show_spinner="Reading the input files...")
def load_files(positions: bytes, previous_prices: bytes, cashflows: bytes) -> InputData:
    # Cached on the file bytes. The required columns come from config.yaml,
    # which the sidebar never changes, so the config is not part of the key.
    return load_inputs(io.BytesIO(positions), io.BytesIO(previous_prices),
                       io.BytesIO(cashflows), load_config(CONFIG_PATH))


def sidebar_overrides(config: Config) -> dict:
    # Every sidebar value is passed to with_overrides, which re-validates it.
    sidebar = st.sidebar
    sidebar.button("Reset to config.yaml", on_click=reset_settings)
    sidebar.subheader("Price move thresholds (%)")
    thresholds = {asset_type: sidebar.number_input(asset_type, min_value=0.0, value=default,
                                                   step=0.5, key=f"{SETTING_KEY}{asset_type}")
                  for asset_type, default in config.price_move_thresholds_pct.items()}
    horizon = sidebar.number_input("Cashflow horizon (days)", min_value=0,
                                   value=config.cashflow_horizon_days, step=1,
                                   key=f"{SETTING_KEY}horizon")
    with sidebar.expander("Controls"):
        enabled = {control_id: st.checkbox(f"{settings.label} ({settings.severity})",
                                           value=settings.enabled,
                                           key=f"{SETTING_KEY}{control_id}")
                   for control_id, settings in config.controls.items()}
    return {"price_move_thresholds_pct": thresholds, "cashflow_horizon_days": horizon,
            "controls": {control_id: {"enabled": on} for control_id, on in enabled.items()}}


def reset_settings() -> None:
    # Without their session state, the widgets start again from config.yaml.
    for key in [key for key in st.session_state if str(key).startswith(SETTING_KEY)]:
        del st.session_state[key]


def show_download(results: Results) -> None:
    buffer = io.BytesIO()
    write_report(results, buffer)
    st.download_button("Download the Excel report", buffer.getvalue(),
                       file_name=f"report_{results.reference_date:%Y-%m-%d}.xlsx",
                       mime=XLSX_MIME, on_click="ignore")


def show_tabs(results: Results) -> None:
    days = results.config.cashflow_horizon_days
    summary, exceptions, moves, cashflows, quality = st.tabs(
        ["Summary", "Exceptions", "Price Movements", f"Cashflows {days}D", "Data Quality"])
    with summary:
        show_summary(results)
    with exceptions:
        show_exceptions(results.exceptions)
    with moves:
        st.dataframe(results.price_movements, hide_index=True)
    with cashflows:
        st.caption(f"Cashflows dated from the reference date to {days} days after it "
                   f"(both included), Status not Paid.")
        st.dataframe(results.upcoming_cashflows, hide_index=True)
    with quality:
        st.dataframe(results.data_quality, hide_index=True)


def show_summary(results: Results) -> None:
    summary = results.summary
    st.markdown(f"**Reference date:** {results.reference_date:%Y-%m-%d}")
    by_severity = summary[summary["Section"] == SECTION_BY_SEVERITY]
    for column, (severity, count) in zip(st.columns(len(by_severity)),
                                         zip(by_severity["Item"], by_severity["Value"])):
        column.metric(severity, int(count))
    for section, rows in summary.groupby("Section", sort=False):
        st.subheader(section)
        shown = ["Item", "Value"] if rows["Currency"].isna().all() else ["Currency", "Value"]
        value_format = UNIT_FORMATS[rows["Unit"].iloc[0]]  # one unit per section
        st.dataframe(rows[shown], hide_index=True, width="content",
                     column_config={"Value": st.column_config.NumberColumn(format=value_format)})


def show_exceptions(exceptions: pd.DataFrame) -> None:
    # The filters only hide rows: no exception is added, removed or changed.
    severity_column, control_column, portfolio_column = st.columns(3)
    severities = severity_column.multiselect(
        "Severity", [severity for severity in SEVERITIES
                     if severity in set(exceptions["Severity"].astype(str))])
    controls = control_column.multiselect("Control", exceptions["ControlID"].unique().tolist())
    portfolio_text = exceptions["Portfolio"].fillna(NO_PORTFOLIO)
    portfolios = portfolio_column.multiselect("Portfolio", sorted(portfolio_text.unique()))
    shown = pd.Series(True, index=exceptions.index)  # an empty filter keeps every row
    if severities:
        shown &= exceptions["Severity"].astype(str).isin(severities)
    if controls:
        shown &= exceptions["ControlID"].isin(controls)
    if portfolios:
        shown &= portfolio_text.isin(portfolios)
    filtered = exceptions[shown]
    st.caption(f"{len(filtered)} of {len(exceptions)} exceptions")
    st.dataframe(filtered, hide_index=True)


main()
