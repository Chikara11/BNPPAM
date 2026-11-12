# CLO / ABS Portfolio Monitoring Prototype

Data quality and price monitoring of CLO Debt, CLO Equity and ABS positions:
3 Excel inputs, deterministic controls driven by `config.yaml`, an Excel
report and a Streamlit app. All data is synthetic (identifiers `SYN` + 9
digits); no real portfolio or market data.

## Run locally

Python 3.12, from the repo root:

```
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/streamlit run app.py                    # the app, http://localhost:8501
.venv/bin/python -m scripts.run_report --inputs data/inputs --output output/report.xlsx
.venv/bin/python -m pytest -q -W error            # the tests
```

The app opens on the test fixture (`data/inputs/`); the sidebar switches to
the large sample (`data/large/`) or accepts an upload of the 3 files.

## Sample data

Two synthetic datasets, one design: same file contract, same clean-data
invariants, same `config.yaml`. Each has a clean base (zero exceptions) and
an injected copy checked against an exact oracle.

| | Test fixture | Large sample |
|---|---|---|
| Purpose | One isolated anomaly per control (unit and end-to-end tests) | Production-like feed: volumes and rates at scale |
| Files | `data/clean/` -> `data/inputs/` | `data/large_clean/` -> `data/large/` |
| Oracle | `docs/anomaly_manifest.xlsx` | `docs/large_manifest.xlsx` |
| Positions / cashflows | 52 / 210 | 504 / 2,095 |
| Expected exceptions | 15 | 89, plus 10 near-boundary cases that must stay silent |
| Regenerate | `python scripts/generate_clean_data.py` then `python scripts/inject_anomalies.py` | `python scripts/generate_large_sample.py` |

Rows per file, exceptions per control and rates: `docs/data_spec.md`,
section "Datasets" (checked against the manifests by the tests).

## Deployed app

<URL to be added after deployment on Streamlit Community Cloud>

Streamlit Community Cloud, main file `app.py`, Python 3.12. The app is
public: upload synthetic files only, never real portfolio data.

## Possible improvements

Found by the clean-code review of 2026-11-12 and deliberately left out of
scope: none of them changes a result.

- `analytics.summary` recomputes the price movements and upcoming
  cashflows that the engine has already computed (pass them in; changes
  the contract of docs/architecture.md section 4.6).
- The row helpers shared by controls and analytics (`valid_isin`,
  `first_occurrence`, `matching_positions`, ...) live in `analytics.py`;
  a separate `dq/rows.py` would read more clearly (architecture change).
- `analytics.summary` (43 lines) could be split into overview, exception
  and amount row builders.
- Two tables link key names and files (`config.KEY_FILES`,
  `analytics.DUPLICATE_KEYS`); they could live side by side in config.py.
- The "get the list if allowed_values is a mapping" pattern is repeated 3
  times in `config.py`.
- The `%Y-%m-%d` date format is written in 5 places; one constant in
  schema.py.
- A few row loops and guards have no WHY comment yet (small data, empty
  `agg` returns a DataFrame, `concat` warns on empty frames).
- `cashflows.matchable` could have a clearer name.
- The manifest row lookup and the "count by ControlID" filter are repeated
  in several test files.
- Many test names do not follow `test_<control>_<case>_<expected>`.
- The scripts have no type hints on some parameters.
