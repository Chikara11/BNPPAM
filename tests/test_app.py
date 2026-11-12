import io

import pandas as pd
from streamlit.testing.v1 import AppTest

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
