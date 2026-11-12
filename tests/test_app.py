import io
import json
import re

import pandas as pd
from streamlit.testing.v1 import AppTest

from dq.report import BREACH_TINT, SEVERITY_TINTS
from helpers import REPO_ROOT

APP = str(REPO_ROOT / "app.py")
INPUTS = REPO_ROOT / "data" / "inputs"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def started() -> AppTest:
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    assert not app.exception
    return app


def exceptions_shown(app: AppTest) -> pd.DataFrame:
    return app.tabs[1].dataframe[0].value


def upload(app: AppTest, files: dict[str, bytes]) -> AppTest:
    app.radio(key="source").set_value("Upload the 3 files").run()
    for file, content in files.items():
        app.file_uploader(key=f"upload_{file}").set_value((f"{file}.xlsx", content, XLSX_MIME))
    return app.run()


def sample_files() -> dict[str, bytes]:
    return {file: (INPUTS / f"{file}.xlsx").read_bytes()
            for file in ("positions", "previous_prices", "cashflows")}


def test_app_sample_shows_the_5_tabs_and_the_severity_counts():
    app = started()
    assert [tab.label for tab in app.tabs] == [
        "Summary", "Exceptions", "Price Movements", "Cashflows 30D", "Data Quality"]
    assert [(metric.label, metric.value) for metric in app.metric] == [
        ("Critical", "6"), ("Warning", "7"), ("Information", "2")]
    assert len(exceptions_shown(app)) == 15
    assert len(app.get("download_button")) == 1


def test_app_lowering_the_clo_equity_threshold_adds_an_alert():
    app = started()
    app.number_input(key="setting_CLO Equity").set_value(12.0).run()
    moves = exceptions_shown(app)
    assert (moves["ControlID"] == "PRICE_MOVE_ABOVE_THRESHOLD").sum() == 4  # + N01, -12.5 %


def test_app_disabled_control_removes_its_exceptions():
    app = started()
    app.checkbox(key="setting_PRICE_MOVE_ABOVE_THRESHOLD").uncheck().run()
    shown = exceptions_shown(app)
    assert len(shown) == 12
    assert "PRICE_MOVE_ABOVE_THRESHOLD" not in shown["ControlID"].tolist()


def test_app_horizon_renames_the_cashflows_tab():
    app = started()
    app.number_input(key="setting_horizon").set_value(60).run()
    assert app.tabs[3].label == "Cashflows 60D"


def test_app_reset_restores_the_config_values():
    app = started()
    app.number_input(key="setting_CLO Debt").set_value(8.0).run()
    app.checkbox(key="setting_MISSING_ISIN").uncheck().run()
    assert len(exceptions_shown(app)) == 13
    app.button[0].click().run()
    assert app.number_input(key="setting_CLO Debt").value == 5.0
    assert app.checkbox(key="setting_MISSING_ISIN").value is True
    assert len(exceptions_shown(app)) == 15


def test_app_exception_filter_only_hides_rows():
    app = started()
    app.tabs[1].multiselect[0].select("Critical").run()
    shown = exceptions_shown(app)
    assert len(shown) == 6 and set(shown["Severity"]) == {"Critical"}
    assert app.metric[0].value == "6"  # the summary is unchanged


def test_app_waits_for_the_3_uploads():
    app = started()
    app.radio(key="source").set_value("Upload the 3 files").run()
    assert not app.tabs and len(app.info) == 1


def test_app_uploaded_files_run_like_the_sample():
    app = upload(started(), sample_files())
    assert not app.exception and not app.error
    assert len(exceptions_shown(app)) == 15


def test_app_upload_with_a_missing_column_shows_a_readable_error():
    files = sample_files()
    positions = pd.read_excel(io.BytesIO(files["positions"])).drop(columns="Rating")
    buffer = io.BytesIO()
    positions.to_excel(buffer, index=False)
    app = upload(started(), {**files, "positions": buffer.getvalue()})
    assert not app.exception and not app.tabs
    assert "missing required column(s): Rating" in app.error[0].value


def test_app_unreadable_upload_shows_a_readable_error():
    app = upload(started(), {**sample_files(), "cashflows": b"not an excel file"})
    assert not app.exception
    assert "cannot be read as an Excel workbook" in app.error[0].value


def chart_specs(app: AppTest) -> dict[str, dict]:
    specs = [json.loads(chart.proto.spec) for chart in app.get("plotly_chart")]
    return {spec["layout"]["title"]["text"].split(":")[0]: spec for spec in specs}


def bars(spec: dict) -> dict[str, list]:
    return {trace.get("name", "-"): trace["y"] for trace in spec["data"]}


def test_app_price_moves_have_one_panel_per_asset_type_with_their_threshold():
    specs = chart_specs(started())
    assert list(specs) == ["Exceptions by control", "ABS", "CLO Debt", "CLO Equity"]
    assert "threshold ±15 %" in specs["CLO Equity"]["layout"]["title"]["text"]
    breaches = {name: len(bars(spec).get("Breach", [])) for name, spec in specs.items()
                if name != "Exceptions by control"}
    assert breaches == {"ABS": 1, "CLO Debt": 1, "CLO Equity": 1}  # A07, A06, A08
    computed = sum(len(y) for name, spec in specs.items() if name != "Exceptions by control"
                   for y in bars(spec).values())
    assert computed == 48  # summary "Price moves / Computed"


def test_app_duplicated_position_keeps_two_bars():
    abs_bars = bars(chart_specs(started())["ABS"])["Within threshold"]
    assert [label for label in abs_bars if "SYN000000035" in label] == [
        "ABS_EUROPE_03 · SYN000000035 (row 10)", "ABS_EUROPE_03 · SYN000000035 (row 11)"]


def test_app_control_chart_follows_the_enabled_controls():
    app = started()
    assert len(bars(chart_specs(app)["Exceptions by control"])["-"]) == 15
    app.checkbox(key="setting_MISSING_ISIN").uncheck().run()
    assert "MISSING_ISIN" not in bars(chart_specs(app)["Exceptions by control"])["-"]


def test_app_tables_use_the_report_number_formats():
    columns = json.loads(started().tabs[2].dataframe[0].proto.columns)
    formats = {name: columns[name]["type_config"]["format"]
               for name in ("Nominal", "MarketValue", "MarketPrice", "PriceChangePct",
                            "PreviousDate")}
    assert formats == {"Nominal": "%,.2f", "MarketValue": "%,.2f", "MarketPrice": "%.2f",
                       "PriceChangePct": "%.2f", "PreviousDate": "YYYY-MM-DD"}


def styled_rows(app: AppTest, tab: int, tint: str) -> set[str]:
    # Rows of the CSS rule that uses this tint, read from the Styler output.
    styles = app.tabs[tab].dataframe[0].proto.arrow_data.styler.styles
    rule = next(block for block in styles.split("}") if tint in block)
    return set(re.findall(r"row(\d+)_", rule))


def test_app_severity_cells_and_breach_rows_are_tinted():
    app = started()
    for tint in SEVERITY_TINTS.values():
        assert styled_rows(app, 1, tint)
    assert len(styled_rows(app, 2, BREACH_TINT)) == 3
