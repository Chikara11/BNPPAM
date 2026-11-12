"""Run the controls on the 3 input files and write the Excel report.

Run from the repo root, as a module (so that it can import dq):
python -m scripts.run_report --inputs data/inputs --output output/report.xlsx
"""

import argparse
import sys
from pathlib import Path

from dq import ConfigError, InputSchemaError, RegistryError, Results, load_config, load_inputs, run
from dq.report import write_report
from dq.schema import SECTION_BY_SEVERITY


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inputs", type=Path, default=Path("data/inputs"),
                        help="folder with positions.xlsx, previous_prices.xlsx, cashflows.xlsx")
    parser.add_argument("--output", type=Path, default=Path("output/report.xlsx"),
                        help="Excel report to write (its folder is created if needed)")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    # Bad config or input files are user errors: a readable message and exit
    # code 1, never a traceback (architecture.md section 5).
    try:
        config = load_config(arguments.config)
        data = load_inputs(positions=arguments.inputs / "positions.xlsx",
                           previous_prices=arguments.inputs / "previous_prices.xlsx",
                           cashflows=arguments.inputs / "cashflows.xlsx", config=config)
        results = run(data, config)
    except (ConfigError, InputSchemaError, RegistryError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    try:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        write_report(results, arguments.output)
    except OSError as error:  # e.g. the report is open in Excel, or a folder of that name
        print(f"Error: cannot write the report to {arguments.output}: {error}", file=sys.stderr)
        return 1
    print_run(results, arguments.output)
    return 0


def print_run(results: Results, output: Path) -> None:
    # Counts come from Results.summary: the CLI computes nothing itself.
    summary = results.summary
    by_severity = summary[summary["Section"] == SECTION_BY_SEVERITY]
    counts = ", ".join(f"{item} {int(value)}" for item, value in
                       zip(by_severity["Item"], by_severity["Value"]))
    print(f"Reference date: {results.reference_date:%Y-%m-%d}")
    print(f"Exceptions: {len(results.exceptions)} ({counts})")
    print(f"Report written: {output}")


if __name__ == "__main__":
    sys.exit(main())
