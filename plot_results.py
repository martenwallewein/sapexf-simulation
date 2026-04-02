"""
Generate comparison plots from experiment result CSV files.

Examples:
  python plot_results.py --input results/20260311_233944/all_results.csv
  python plot_results.py --latest
  python plot_results.py --latest --scenario thundering_herd
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


DEFAULT_METRICS = [
    "latency_avg_ms",
    "latency_p95_ms",
    "packet_loss_rate_percent",
    "total_packets_received",
    "total_path_switches",
]


def find_latest_all_results(results_dir: Path) -> Path:
    """Return the newest all_results.csv under results_dir."""
    candidates = list(results_dir.glob("*/all_results.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No all_results.csv found under {results_dir}. "
            "Run experiments first or provide --input."
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def validate_columns(df: pd.DataFrame, metrics: list[str]) -> None:
    required = {"algorithm", "scenario"}
    missing_required = sorted(required - set(df.columns))
    if missing_required:
        raise ValueError(f"Missing required columns: {missing_required}")

    missing_metrics = [m for m in metrics if m not in df.columns]
    if missing_metrics:
        raise ValueError(
            "Missing metric columns in CSV: "
            + ", ".join(missing_metrics)
        )


def plot_metric(df: pd.DataFrame, metric: str, output_dir: Path) -> Path:
    """Create one grouped bar chart for a metric."""
    grouped = (
        df.groupby(["scenario", "algorithm"], as_index=False)[metric]
        .mean()
    )

    pivot = grouped.pivot(index="scenario", columns="algorithm", values=metric)

    fig, ax = plt.subplots(figsize=(10, 6))
    pivot.plot(kind="bar", ax=ax)

    ax.set_title(f"{metric} by Scenario and Algorithm")
    ax.set_xlabel("Scenario")
    ax.set_ylabel(metric)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(title="Algorithm", bbox_to_anchor=(1.02, 1), loc="upper left")

    plt.tight_layout()

    output_file = output_dir / f"{metric}.png"
    fig.savefig(output_file, dpi=150)
    plt.close(fig)
    return output_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot SAPEX-F experiment results")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to all_results.csv (or another CSV with the same columns)",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("results"),
        help="Base results directory used with --latest (default: results)",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Automatically use the newest results/*/all_results.csv",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Optional scenario filter (e.g., thundering_herd)",
    )
    parser.add_argument(
        "--algorithm",
        type=str,
        default=None,
        help="Optional algorithm filter (e.g., sapex)",
    )
    parser.add_argument(
        "--metrics",
        nargs="+",
        default=DEFAULT_METRICS,
        help="Metric columns to plot",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("plots"),
        help="Directory where PNG files will be written",
    )
    args = parser.parse_args()

    if args.latest:
        input_csv = find_latest_all_results(args.results_dir)
    elif args.input is not None:
        input_csv = args.input
    else:
        parser.error("Provide --input <csv> or use --latest")

    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    df = pd.read_csv(input_csv)
    validate_columns(df, args.metrics)

    if args.scenario:
        df = df[df["scenario"] == args.scenario]
    if args.algorithm:
        df = df[df["algorithm"] == args.algorithm]

    if df.empty:
        raise ValueError("No rows left after applying filters.")

    out_dir = args.out_dir / input_csv.parent.name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Using input: {input_csv}")
    print(f"Writing plots to: {out_dir}")

    for metric in args.metrics:
        output_file = plot_metric(df, metric, out_dir)
        print(f"Saved: {output_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
