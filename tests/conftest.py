from pathlib import Path

import pandas as pd
import pytest

from dq.config import load_config
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
def clean_data(real_config):
    return load_folder(REPO_ROOT / "data" / "clean", real_config)


@pytest.fixture(scope="session")
def input_data(real_config):
    return load_folder(REPO_ROOT / "data" / "inputs", real_config)


@pytest.fixture(scope="session")
def manifest():
    return pd.read_excel(REPO_ROOT / "docs" / "anomaly_manifest.xlsx", dtype=object)


@pytest.fixture(scope="session")
def clean_results(clean_data, real_config):
    return run(clean_data, real_config)


@pytest.fixture(scope="session")
def input_results(input_data, real_config):
    return run(input_data, real_config)
