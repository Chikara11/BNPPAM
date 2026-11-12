from pathlib import Path

import pandas as pd
import pytest

from dq.engine import run
from dq.loaders import load_inputs
from helpers import CONFIG, MANIFEST_PATH, REPO_ROOT


def load_folder(folder: Path):
    return load_inputs(folder / "positions.xlsx", folder / "previous_prices.xlsx",
                       folder / "cashflows.xlsx", CONFIG)


@pytest.fixture(scope="session")
def clean_data():
    return load_folder(REPO_ROOT / "data" / "clean")


@pytest.fixture(scope="session")
def input_data():
    return load_folder(REPO_ROOT / "data" / "inputs")


@pytest.fixture(scope="session")
def manifest():
    return pd.read_excel(MANIFEST_PATH, dtype=object)


@pytest.fixture(scope="session")
def clean_results(clean_data):
    return run(clean_data, CONFIG)


@pytest.fixture(scope="session")
def input_results(input_data):
    return run(input_data, CONFIG)
