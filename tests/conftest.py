from pathlib import Path

import pandas as pd
import pytest

from dq.config import config_from_dict, load_config, thaw
from dq.controls import CONTROL_REGISTRY
from dq.engine import run
from dq.loaders import load_inputs

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_folder(folder: Path, config):
    return load_inputs(folder / "positions.xlsx", folder / "previous_prices.xlsx",
                       folder / "cashflows.xlsx", config)


@pytest.fixture(scope="session")
def real_config():
    return load_config(REPO_ROOT / "config.yaml")


@pytest.fixture(scope="session")
def test_config(real_config):
    # TEMPORARY, until every control of config.yaml is registered.
    # The real config.yaml with only the registered controls, so that the
    # strict registry check passes. When the last control is registered,
    # return real_config here and remove the RegistryError test in
    # test_engine.py.
    raw = thaw(real_config.raw)
    raw["controls"] = {control_id: settings for control_id, settings in raw["controls"].items()
                       if control_id in CONTROL_REGISTRY}
    return config_from_dict(raw)


@pytest.fixture(scope="session")
def clean_data(real_config):
    return load_folder(REPO_ROOT / "data" / "clean", real_config)


@pytest.fixture(scope="session")
def input_data(real_config):
    return load_folder(REPO_ROOT / "data" / "inputs", real_config)


@pytest.fixture(scope="session")
def manifest():
    return pd.read_excel(REPO_ROOT / "docs" / "anomaly_manifest.xlsx", dtype=object)


@pytest.fixture(scope="session")
def clean_results(clean_data, test_config):
    return run(clean_data, test_config)


@pytest.fixture(scope="session")
def input_results(input_data, test_config):
    return run(input_data, test_config)
