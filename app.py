from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pandas.io.formats.style import Styler

from dq import (Config, ConfigError, InputData, InputSchemaError, RegistryError, Results,
                load_config, load_inputs, run, with_overrides)
from dq.config import INPUT_FILES, SEVERITIES
from dq.report import (AMOUNT_FORMAT, BREACH_TINT, COLUMN_BLOCK_TITLE, COLUMN_FORMATS,
                       DATE_FORMAT, DIMENSION_BLOCK_TITLE, PLAUSIBILITY_NOTE, PRICE_FORMAT,
                       SEVERITY_TINTS, write_report)
from dq.schema import COMPUTED, NO_PORTFOLIO, SECTION_BY_CONTROL, SECTION_BY_SEVERITY

# Display only (architecture.md section 4.9): every number comes from
# Results; the app filters and shows, it never computes.

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
# Bundled datasets (docs/data_spec.md "Datasets"); the fixture is the default.
SAMPLES = {"Test fixture (data/inputs)": REPO_ROOT / "data" / "inputs",
           "Large sample (data/large)": REPO_ROOT / "data" / "large"}
UPLOAD = "Upload the 3 files"
SETTING_KEY = "setting_"  # prefix of the sidebar widgets that Reset clears
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
UNIT_FORMATS = {"count": "%d", "amount": "%,.2f", "pct": "%.2f"}
# The Excel number formats of report.COLUMN_FORMATS in Streamlit syntax, so
# the app and the report format the same columns the same way. Percentages
# share the price format (0.00).
NUMBER_FORMATS = {AMOUNT_FORMAT: "%,.2f", PRICE_FORMAT: "%.2f"}
INK = "#0b0b0b"  # dark text on the light tints, readable in the dark theme too
# Chart colours (validated for colour-blind separation and contrast, light
# and dark): series blue, status-critical red for a breach, muted grey rules.
SERIES_COLOUR = "#2a78d6"
BREACH_COLOUR = "#d03b3b"
RULE_COLOUR = "#898781"
BAR_HEIGHT = 26  # pixels per bar of the control chart
STRIP_HEIGHT = 260  # pixels per price-move panel, whatever the number of positions
DOT_SIZE = 9


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
    source = st.sidebar.radio("Input files", [*SAMPLES, UPLOAD], key="source")
    if source in SAMPLES:
        files = {file: (SAMPLES[source] / f"{file}.xlsx").read_bytes() for file in INPUT_FILES}
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
        show_price_movements(results.price_movements)
    with cashflows:
        st.caption(f"Cashflows dated from the reference date to {days} days after it "
                   f"(both included), Status not Paid.")
        show_table(results.upcoming_cashflows)
    with quality:
        show_data_quality(results)


def show_data_quality(results: Results) -> None:
    # Same blocks, titles and note as the report's Data Quality sheet.
    st.subheader(DIMENSION_BLOCK_TITLE)
    show_table(results.dimension_quality)
    st.caption(PLAUSIBILITY_NOTE)
    st.subheader(COLUMN_BLOCK_TITLE)
    show_table(results.data_quality)


def show_summary(results: Results) -> None:
    summary = results.summary
    st.markdown(f"**Reference date:** {results.reference_date:%Y-%m-%d}")
    by_severity = summary[summary["Section"] == SECTION_BY_SEVERITY]
    for column, (severity, count) in zip(st.columns(len(by_severity)),
                                         zip(by_severity["Item"], by_severity["Value"])):
        column.metric(severity, int(count))
    by_control = summary[summary["Section"] == SECTION_BY_CONTROL]
    if not by_control.empty:
        st.plotly_chart(control_figure(by_control), key="by_control")
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
    severity_style = filtered.style.map(
        lambda severity: tint_css(SEVERITY_TINTS[severity]), subset=["Severity"])
    show_table(severity_style)


def show_price_movements(moves: pd.DataFrame) -> None:
    # One panel per asset type: the thresholds differ (5 % and 15 %), so a
    # shared axis would flatten the CLO Debt and ABS moves.
    st.caption("Positions whose movement is computed (MoveStatus Computed). The others "
               "are listed in the table with the reason.")
    computed = moves[moves["MoveStatus"] == COMPUTED]
    for asset_type, rows in computed.groupby("AssetType", sort=True):
        st.plotly_chart(price_move_figure(asset_type, rows), key=f"moves_{asset_type}")
    show_table(moves.style.apply(breach_css, axis=None))


def price_move_figure(asset_type: str, rows: pd.DataFrame) -> go.Figure:
    # One dot per computed position on the change axis: the same chart reads
    # at 50 or 500 positions (bars would need one line each).
    rows = rows.sort_values(["PriceChangePct", "SourceRow"], kind="stable")
    labels = position_labels(rows)
    spread = strip_spread(rows)
    breach = rows["Breach"].fillna(False).astype(bool)
    threshold = rows["ThresholdPct"].iloc[0]  # one threshold per asset type
    figure = go.Figure()
    for name, selected, colour in [("Within threshold", ~breach, SERIES_COLOUR),
                                   ("Breach", breach, BREACH_COLOUR)]:
        if selected.any():
            # Lists, not arrays: plotly would encode arrays as binary data.
            figure.add_scatter(
                x=rows.loc[selected, "PriceChangePct"].tolist(), y=spread[selected].tolist(),
                text=labels[selected].tolist(), mode="markers", name=name,
                marker={"color": colour, "size": DOT_SIZE,
                        "line": {"width": 1, "color": "rgba(255, 255, 255, 0.8)"}},
                hovertemplate="%{text}: %{x:+.2f} %<extra>" + name + "</extra>")
    for limit in (-threshold, threshold):
        figure.add_vline(x=limit, line_dash="dash", line_width=1, line_color=RULE_COLOUR,
                         annotation_text=f"{limit:+g} %", annotation_font_color=RULE_COLOUR)
    # Symmetric axis around 0, so up and down moves read the same way.
    reach = max(threshold, rows["PriceChangePct"].abs().max()) * 1.15
    figure.update_layout(
        title=f"{asset_type}: change vs previous price (%), threshold ±{threshold:g} %",
        height=STRIP_HEIGHT, legend={"orientation": "h", "y": 1.02, "yanchor": "bottom"},
        xaxis={"range": [-reach, reach], "ticksuffix": " %", "zeroline": True},
        yaxis={"visible": False, "range": [-1, 1]})
    return figure


def strip_spread(rows: pd.DataFrame) -> pd.Series:
    # Vertical position with no meaning, a fixed function of the Excel row (no
    # randomness, so the chart is the same on every run): close dots stay apart.
    return (rows["SourceRow"].astype(int) * 37 % 17 - 8) / 10


def position_labels(rows: pd.DataFrame) -> pd.Series:
    # Portfolio · ISIN; a duplicated position also gets its Excel row, so that
    # its two bars do not merge into one.
    labels = rows["Portfolio"].fillna(NO_PORTFOLIO) + " · " + rows["ISIN"]
    repeated = labels.duplicated(keep=False)
    return labels.where(~repeated, labels + " (row " + rows["SourceRow"].astype(str) + ")")


def control_figure(by_control: pd.DataFrame) -> go.Figure:
    # Largest count at the top; a single series, so no legend.
    rows = by_control.sort_values("Value", kind="stable")
    figure = go.Figure(go.Bar(
        x=rows["Value"], y=rows["Item"], orientation="h", marker_color=SERIES_COLOUR,
        text=rows["Value"].astype(int), textposition="outside",
        hovertemplate="%{y}: %{x:d}<extra></extra>"))
    figure.update_layout(title="Exceptions by control", showlegend=False, barcornerradius=4,
                         height=BAR_HEIGHT * len(rows) + 120, xaxis={"dtick": 1})
    return figure


def breach_css(moves: pd.DataFrame) -> pd.DataFrame:
    css = pd.DataFrame("", index=moves.index, columns=moves.columns)
    css.loc[moves["Breach"].fillna(False).astype(bool)] = tint_css(BREACH_TINT)
    return css


def tint_css(tint: str) -> str:
    return f"background-color: #{tint}; color: {INK}"


def show_table(table: pd.DataFrame | Styler) -> None:
    frame = table.data if isinstance(table, Styler) else table
    st.dataframe(table, hide_index=True, column_config=column_config(frame))


def column_config(frame: pd.DataFrame) -> dict:
    # Same formats as the Excel report, for the columns it formats.
    config = {}
    for column in frame.columns:
        excel_format = COLUMN_FORMATS.get(column)
        if excel_format == DATE_FORMAT:
            config[column] = st.column_config.DateColumn(format=DATE_FORMAT)
        elif excel_format:
            config[column] = st.column_config.NumberColumn(format=NUMBER_FORMATS[excel_format])
    return config


main()
