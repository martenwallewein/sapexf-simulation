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
from matplotlib.patches import Patch
import pandas as pd


DEFAULT_METRICS = [
    "latency_avg_ms",
    "latency_p95_ms",
    "packet_loss_rate_percent",
    "total_packets_received",
    "total_path_switches",
    "global_jfi",
]

FAIRNESS_METRICS = {
    "global_jfi",
}


def has_lambda_div_column(df: pd.DataFrame) -> bool:
    """Check if the dataframe has lambda_div column and multiple values."""
    if "lambda_div" not in df.columns:
        return False
    return df["lambda_div"].nunique() > 1


def has_t_round_column(df: pd.DataFrame) -> bool:
    """Check if the dataframe has t_round_ms column and multiple values."""
    if "t_round_ms" not in df.columns:
        return False
    return df["t_round_ms"].nunique() > 1


def has_num_packets_column(df: pd.DataFrame) -> bool:
    """Check if the dataframe has num_packets column and multiple values."""
    if "num_packets" not in df.columns:
        return False
    return df["num_packets"].nunique() > 1


def find_latest_all_results(results_dir: Path) -> Path:
    """Return the newest all_results.csv under results_dir."""
    candidates = list(results_dir.glob("*/all_results.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No all_results.csv found under {results_dir}. "
            "Run experiments first or provide --input."
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def validate_columns(df: pd.DataFrame, metrics: list[str]) -> list[str]:
    required = {"algorithm", "scenario"}
    missing_required = sorted(required - set(df.columns))
    if missing_required:
        raise ValueError(f"Missing required columns: {missing_required}")

    available_metrics = [m for m in metrics if m in df.columns]
    missing_metrics = [m for m in metrics if m not in df.columns]
    if missing_metrics:
        print(
            "Skipping missing metric columns: "
            + ", ".join(missing_metrics)
        )

    if not available_metrics:
        raise ValueError("None of the requested metrics exist in the CSV.")

    return available_metrics


def plot_metric(df: pd.DataFrame, metric: str, output_dir: Path) -> Path:
    """Create one grouped bar chart for a metric."""
    # Aggregate only numeric values; malformed cells become NaN and are ignored by mean().
    metric_df = df.copy()
    metric_df[metric] = pd.to_numeric(metric_df[metric], errors="coerce")

    grouped = (
        metric_df.groupby(["scenario", "algorithm"], as_index=False)[metric]
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

    # Fairness metrics are often tightly clustered near 1.0; zoom the axis to show differences.
    if metric in FAIRNESS_METRICS:
        values = pd.to_numeric(pivot.to_numpy().ravel(), errors="coerce")
        values = values[~pd.isna(values)]
        if len(values) > 0:
            vmin = float(values.min())
            vmax = float(values.max())
            span = vmax - vmin
            padding = max(span * 0.2, 0.002)
            lower = max(0.0, vmin - padding)
            upper = min(1.0, vmax + padding)
            if upper <= lower:
                upper = min(1.0, lower + 0.01)
            ax.set_ylim(lower, upper)

    plt.tight_layout()

    output_file = output_dir / f"{metric}.png"
    fig.savefig(output_file, dpi=150)
    plt.close(fig)
    return output_file


def _plot_sapex_parameter_comparison(
    df: pd.DataFrame,
    metric: str,
    output_dir: Path,
    parameter_col: str,
    parameter_label: str,
    output_suffix: str,
) -> Path:
    """
    Create separate subplots per scenario comparing one SAPEX parameter.
    
    Layout depends on data:
        - If only SAPEX present: Shows parameter progression on x-axis
        - If SAPEX + other algorithms: Shows SAPEX with parameter bars + baseline algorithms as single bars
      for direct comparison to see if SAPEX beats baselines at any setting
    """
    metric_df = df.copy()
    metric_df[metric] = pd.to_numeric(metric_df[metric], errors="coerce")
    metric_df[parameter_col] = pd.to_numeric(metric_df[parameter_col], errors="coerce")
    
    scenarios = sorted(metric_df["scenario"].unique())
    algorithms = sorted(metric_df["algorithm"].unique())
    n_scenarios = len(scenarios)
    has_sapex = "sapex" in algorithms
    other_algos = [a for a in algorithms if a != "sapex"]
    baseline_order = [
        "lowest_latency",
        "lowest_hop_count",
        "random",
        "round_robin",
    ]
    baseline_algos = [algo for algo in baseline_order if algo in other_algos]
    baseline_algos.extend([algo for algo in other_algos if algo not in baseline_algos])
    
    # Create subplots: one per scenario
    fig, axes = plt.subplots(1, n_scenarios, figsize=(8 * n_scenarios, 6))
    if n_scenarios == 1:
        axes = [axes]
    
    for idx, scenario in enumerate(scenarios):
        scenario_data = metric_df[metric_df["scenario"] == scenario]
        ax = axes[idx]
        
        if has_sapex and len(other_algos) == 0:
            # Only SAPEX: parameter progression
            grouped = scenario_data.groupby(parameter_col, as_index=False)[metric].mean()
            grouped = grouped.sort_values(parameter_col)
            ax.bar(grouped[parameter_col].astype(str), grouped[metric], color="steelblue", alpha=0.8, label="SAPEX")
            ax.set_xlabel(parameter_label, fontsize=10)
            title_suffix = "(SAPEX only)"
            
        elif has_sapex and len(other_algos) > 0:
            # SAPEX + baselines: draw named bars for SAPEX parameter values and one bar per baseline algorithm.
            x_positions = []
            x_labels = []
            bar_values = []
            bar_colors = []
            legend_handles = []

            sapex_data = scenario_data[scenario_data["algorithm"] == "sapex"]
            parameter_values = sorted(sapex_data[parameter_col].dropna().unique().tolist())
            for i, parameter_value in enumerate(parameter_values):
                grouped = sapex_data[sapex_data[parameter_col] == parameter_value][metric].dropna()
                if not grouped.empty:
                    x_positions.append(len(x_positions))
                    x_labels.append(f"SAPEX\n{parameter_label}={parameter_value}")
                    bar_values.append(float(grouped.mean()))
                    color = f"C{i % 10}"
                    bar_colors.append(color)
                    legend_handles.append(Patch(facecolor=color, label=f"SAPEX {parameter_label}={parameter_value}"))

            for algo in baseline_algos:
                algo_values = scenario_data[scenario_data["algorithm"] == algo][metric].dropna()
                if not algo_values.empty:
                    x_positions.append(len(x_positions))
                    x_labels.append(algo)
                    bar_values.append(float(algo_values.mean()))
                    color = "lightgray"
                    bar_colors.append(color)
                    legend_handles.append(Patch(facecolor=color, label=algo))

            ax.bar(x_positions, bar_values, color=bar_colors, alpha=0.9)
            ax.set_xticks(x_positions)
            ax.set_xticklabels(x_labels, fontsize=9, rotation=45, ha="right")
            ax.set_xlabel("SAPEX settings and baselines", fontsize=10)
            if legend_handles:
                ax.legend(handles=legend_handles, title="Bars", fontsize=8, loc="upper left")
            title_suffix = "(SAPEX vs Baselines)"
            
        else:
            # No SAPEX: standard algorithm comparison
            grouped = scenario_data.groupby("algorithm", as_index=False)[metric].mean()
            pivot = grouped.pivot_table(values=metric, index="algorithm")
            ax.bar(pivot.index, pivot[metric], alpha=0.8)
            ax.set_xlabel("Algorithm", fontsize=10)
            title_suffix = "(comparison)"
        
        ax.set_ylabel(metric, fontsize=10)
        ax.set_title(f"{scenario}", fontsize=12, fontweight="bold")
        ax.grid(axis="y", alpha=0.3)
        
        # Fairness metrics zoom
        if metric in FAIRNESS_METRICS:
            y_min, y_max = ax.get_ylim()
            if y_min < y_max:
                span = y_max - y_min
                padding = max(span * 0.2, 0.002)
                lower = max(0.0, y_min - padding)
                upper = min(1.0, y_max + padding)
                if upper <= lower:
                    upper = min(1.0, lower + 0.01)
                ax.set_ylim(lower, upper)
    
    fig.suptitle(f"{metric} - {parameter_label} Comparison {title_suffix}", fontsize=14, fontweight="bold", y=0.98)
    plt.tight_layout()

    output_file = output_dir / f"{metric}_{output_suffix}_comparison.png"
    fig.savefig(output_file, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return output_file


def plot_lambda_div_comparison(df: pd.DataFrame, metric: str, output_dir: Path) -> Path:
    """Create comparison plot for SAPEX lambda_div settings with optional baselines."""
    return _plot_sapex_parameter_comparison(
        df=df,
        metric=metric,
        output_dir=output_dir,
        parameter_col="lambda_div",
        parameter_label="lambda_div",
        output_suffix="lambda_div",
    )


def plot_t_round_comparison(df: pd.DataFrame, metric: str, output_dir: Path) -> Path:
    """Create comparison plot for SAPEX t_round_ms settings with optional baselines."""
    return _plot_sapex_parameter_comparison(
        df=df,
        metric=metric,
        output_dir=output_dir,
        parameter_col="t_round_ms",
        parameter_label="t_round_ms",
        output_suffix="t_round_ms",
    )


def plot_num_packets_comparison(df: pd.DataFrame, metric: str, output_dir: Path) -> Path:
    """Create per-scenario plots with num_packets on x-axis and algorithms as grouped bars."""
    metric_df = df.copy()
    metric_df[metric] = pd.to_numeric(metric_df[metric], errors="coerce")
    metric_df["num_packets"] = pd.to_numeric(metric_df["num_packets"], errors="coerce")

    scenarios = sorted(metric_df["scenario"].unique())
    n_scenarios = len(scenarios)

    fig, axes = plt.subplots(1, n_scenarios, figsize=(8 * n_scenarios, 6))
    if n_scenarios == 1:
        axes = [axes]

    for idx, scenario in enumerate(scenarios):
        scenario_data = metric_df[metric_df["scenario"] == scenario]
        grouped = scenario_data.groupby(["num_packets", "algorithm"], as_index=False)[metric].mean()
        pivot = grouped.pivot(index="num_packets", columns="algorithm", values=metric)
        pivot = pivot.sort_index()

        ax = axes[idx]
        pivot.plot(kind="bar", ax=ax)
        ax.set_title(f"{scenario}", fontsize=12, fontweight="bold")
        ax.set_xlabel("num_packets", fontsize=10)
        ax.set_ylabel(metric, fontsize=10)
        ax.grid(axis="y", alpha=0.3)
        ax.legend(title="Algorithm", fontsize=8, loc="upper left")
        ax.tick_params(axis="x", rotation=0)

        if metric in FAIRNESS_METRICS:
            values = pd.to_numeric(pivot.to_numpy().ravel(), errors="coerce")
            values = values[~pd.isna(values)]
            if len(values) > 0:
                vmin = float(values.min())
                vmax = float(values.max())
                span = vmax - vmin
                padding = max(span * 0.2, 0.002)
                lower = max(0.0, vmin - padding)
                upper = min(1.0, vmax + padding)
                if upper <= lower:
                    upper = min(1.0, lower + 0.01)
                ax.set_ylim(lower, upper)

    fig.suptitle(f"{metric} - num_packets comparison", fontsize=14, fontweight="bold", y=0.98)
    plt.tight_layout()

    output_file = output_dir / f"{metric}_num_packets_comparison.png"
    fig.savefig(output_file, dpi=150, bbox_inches="tight")
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
    metrics_to_plot = validate_columns(df, args.metrics)

    if args.scenario:
        df = df[df["scenario"] == args.scenario]
    if args.algorithm:
        df = df[df["algorithm"] == args.algorithm]

    if df.empty:
        raise ValueError("No rows left after applying filters.")

    # Detect parameter-sweep style datasets and choose specialized plot layout.
    is_lambda_div_comparison = has_lambda_div_column(df)
    is_t_round_comparison = has_t_round_column(df)
    is_num_packets_comparison = has_num_packets_column(df)
    
    # Build descriptive suffix for output directory based on detected comparisons
    settings_suffix = ""
    if is_lambda_div_comparison:
        settings_suffix = "_lambda_div_sweep"
    elif is_t_round_comparison:
        settings_suffix = "_t_round_sweep"
    elif is_num_packets_comparison:
        settings_suffix = "_num_packets_sweep"
    
    out_dir = args.out_dir / (input_csv.parent.name + settings_suffix)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Using input: {input_csv}")
    print(f"Writing plots to: {out_dir}")

    # Detect parameter-sweep style datasets and choose specialized plot layout.
    if is_lambda_div_comparison:
        print("Detected lambda_div variations - generating lambda_div comparison plots")
        for metric in metrics_to_plot:
            output_file = plot_lambda_div_comparison(df, metric, out_dir)
            print(f"Saved: {output_file}")
    elif is_t_round_comparison:
        print("Detected t_round_ms variations - generating t_round comparison plots")
        for metric in metrics_to_plot:
            output_file = plot_t_round_comparison(df, metric, out_dir)
            print(f"Saved: {output_file}")
    elif is_num_packets_comparison:
        print("Detected num_packets variations - generating num_packets comparison plots")
        for metric in metrics_to_plot:
            output_file = plot_num_packets_comparison(df, metric, out_dir)
            print(f"Saved: {output_file}")
    else:
        for metric in metrics_to_plot:
            output_file = plot_metric(df, metric, out_dir)
            print(f"Saved: {output_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
