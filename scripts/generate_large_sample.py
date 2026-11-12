"""Generate the large production-like sample (seed 43).

Same portfolios, rules and invariants as the test fixture (docs/data_spec.md),
10 times more securities. Writes the clean base to data/large_clean/.
Run: python scripts/generate_large_sample.py
"""

import numpy as np

from generate_clean_data import (
    CONFIG_PATH,
    REPO_ROOT,
    SECURITY_LAYOUT,
    build_clean_files,
    fingerprint,
    load_config,
    print_summary,
    write_and_verify,
)

SEED = 43
SCALE = 10  # securities per layout line, times the fixture
FIRST_ISIN_NUMBER = 1_000_001  # SYN001000001...: never mistaken for a fixture ISIN
SHARED_SECURITY_COUNT = 20  # USD CLO Debt of CLO_GLOBAL_02 also held in MULTI_USD_04
CLEAN_DIR = REPO_ROOT / "data" / "large_clean"

LAYOUT = [(portfolio, asset_type, currency, count * SCALE)
          for portfolio, asset_type, currency, count in SECURITY_LAYOUT]


def main() -> None:
    config = load_config(CONFIG_PATH)
    files = build_clean_files(np.random.default_rng(SEED), config, LAYOUT, FIRST_ISIN_NUMBER,
                              SHARED_SECURITY_COUNT)
    saved, results = write_and_verify(files, CLEAN_DIR, config)

    print_summary(saved["positions"], saved["previous_prices"], saved["cashflows"], config,
                  SEED)
    print("\n--- Invariants (checked before saving and on the saved files) ---")
    print("\n".join(results))
    print(f"\nFiles written to {CLEAN_DIR.relative_to(REPO_ROOT)}/")
    print(f"Data fingerprint: {fingerprint(saved)}")


if __name__ == "__main__":
    main()
